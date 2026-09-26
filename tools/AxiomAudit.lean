/-
AxiomAudit.lean — Lean-bridge correctness gate for the cert formal tier (T1).

Run via:  lake env lean --run scripts/AxiomAudit.lean <Module> <Fully.Qualified.Decl>

  <Module> is the Lean module to import so the declaration is in scope
  (e.g. `Erdos728`); the driver derives it from the decl's leading namespace.

NOTE: Lean's metaprogramming surface (CollectAxioms, ppExpr, importModules) is
version-sensitive. These names target the Lean 4 line pinned in the project's
`lean-toolchain`; re-confirm against that toolchain before first use.

For the target declaration it:
  1. resolves the name (missing ⇒ exit 2),
  2. collects the transitive axiom set (the same data `#print axioms` shows),
  3. buckets it (docs/DESIGN_TOOL_ATTESTATION_TIER.md):
       core       — the ALLOWED trusted set,
       attested   — tool-attestation axioms matching the naming schema
                    `trunkit_att_<claim_id>_<sha256_16>` (top-level names
                    only; must stay in sync with
                    cert.attestation_axiom_name in 104_cert_attestation.sql),
       disallowed — everything else (always includes any sorry variant),
  4. prints a one-line JSON verdict to stdout:
       {"decl":"…","type":"…","axioms":[…],
        "buckets":{"core":[…],"attested":[…],"disallowed":[…]},
        "statement_closure":[["<const>","<type hash>","<value hash>"],…],
        "allow_attested":false,"uses_sorry":false,"ok":true}
     statement_closure is every constant the statement's MEANING depends on
     (see statementClosure; bound and compared by 117_cert_statement_closure).
     Set LEAN_AUDIT_NO_CLOSURE=1 to omit it.
  5. exits 0 iff  (¬uses_sorry) ∧ disallowed = ∅
                ∧ (attested = ∅ ∨ --allow-attested was given).

Without `--allow-attested` (or LEAN_AUDIT_ALLOW_ATTESTED=1) the contract is
exactly the historical one: axioms ⊆ ALLOWED. A kernel-pure (T0) claim can
therefore never silently acquire empirical dependencies.

ALLOWED defaults to Mathlib's three trusted axioms. `sorryAx` is always a
failure. `Lean.ofReduceBool` (the native_decide trust root) is NOT in ALLOWED by
default; set LEAN_AUDIT_ALLOW_NATIVE=1 to admit it (recorded by the harness).

`--selftest` runs the naming-schema matcher unit checks and exits (no imports).

`lake build` succeeding only proves the project typechecks; this is the gate
that proves the *declaration* is sorry-free and rests on trusted axioms.
-/
import Lean
open Lean

def baseAllowed : List Name :=
  [``propext, ``Classical.choice, ``Quot.sound]

/-- `String.toName` parses an all-digit segment (e.g. the `80170` in
`FormalConjectures.OEIS.80170`) as a numeric name part, which cannot map to a
module file. Coerce numeric parts back to string atoms. -/
def asModuleName : Name → Name
  | .anonymous => .anonymous
  | .str p s   => .str (asModuleName p) s
  | .num p n   => .str (asModuleName p) (toString n)

/-- Tool-attestation axiom naming schema: a TOP-LEVEL name of the form
`trunkit_att_<claim_id digits>_<exactly 16 lowercase hex>`. The claim id and
statement-hash prefix make the name a foreign key into `cert.attestation`;
namespaced names never match (attestation axioms are emitted top-level). -/
def isAttestationAxiom (n : Name) : Bool :=
  match n with
  | .str .anonymous s =>
      -- Split the WHOLE atom rather than `(s.drop pre.length)`: as of Lean
      -- 4.28 `String.drop` returns a `String.Slice`, and `String.Slice` has
      -- no `splitOn`, so the old spelling stopped compiling and took claim
      -- 373 down with it. Putting the prefix in the pattern also removes the
      -- second reading of it -- startsWith and drop had to agree on the same
      -- literal, and nothing enforced that they did.
      match s.splitOn "_" with
      | ["trunkit", "att", claimId, hex] =>
          claimId.length > 0 && claimId.all Char.isDigit
            && hex.length == 16
            && hex.all (fun c => c.isDigit || ('a' ≤ c && c ≤ 'f'))
      | _ => false
  | _ => false

def selftestCases : List (String × Bool) :=
  [ ("trunkit_att_42_0123456789abcdef", true),
    ("trunkit_att_9000000_00000000000000ff", true),
    ("trunkit_att_42_0123456789ABCDEF", false),  -- uppercase hex
    ("trunkit_att_42_0123456789abcde",  false),  -- 15 hex chars
    ("trunkit_att_42_0123456789abcdef0", false), -- 17 hex chars
    ("trunkit_att__0123456789abcdef",   false),  -- empty claim id
    ("trunkit_att_x2_0123456789abcdef", false),  -- non-digit claim id
    ("trunkit_att_42_0123456789abcdxy", false),  -- non-hex suffix
    ("lift_deadbeef_argmax",            false),  -- EG-VAR's schema, not ours
    ("sorryAx",                         false),
    ("propext",                         false) ]

def runSelftest : IO UInt32 := do
  let mut failures := 0
  for (s, expected) in selftestCases do
    let got := isAttestationAxiom (.str .anonymous s)
    if got != expected then
      failures := failures + 1
      IO.eprintln s!"selftest FAIL: {s} → {got}, expected {expected}"
  -- namespaced names must not match even with a schema-shaped atom
  if isAttestationAxiom (.str (.str .anonymous "Foo") "trunkit_att_1_0123456789abcdef") then
    failures := failures + 1
    IO.eprintln "selftest FAIL: namespaced name matched"
  IO.println <| "{\"selftest\":\"AxiomAudit\",\"cases\":" ++ toString (selftestCases.length + 1) ++ ",\"failures\":" ++ toString failures ++ "}"
  return (if failures == 0 then 0 else 1)

/-- A 64-bit hash as exactly 16 lowercase hex digits. -/
def hex16 (h : UInt64) : String :=
  -- Built from Char.toString and pushn, both stable across the 4.2x line;
  -- `String.mk` is deprecated as of 4.27 and its replacement is too new.
  let s := String.join ((Nat.toDigits 16 h.toNat).map Char.toString)
  ("".pushn '0' (16 - s.length)) ++ s

/-- The STATEMENT CLOSURE of `root`: every constant the meaning of its type
depends on, each with a hash of its type and of its definitional content.

This is what the syntactic statement digest (calx.goalhash, 106) cannot see.
`theorem t : P` keeps its text when `def P` is weakened from a real condition
to `True`; its closure does not keep its hash. Comparator
(github.com/leanprover/comparator) closes the same hole by checking every
declaration a statement uses is identical across two environments; this is
the single-environment version of that check, recorded so the ledger can
compare a binding against a later observation.

Walk rules:
  * the root contributes its TYPE only -- its value is the proof;
  * definitions contribute type and value, and both are walked;
  * inductives contribute their shape (constructors, params, indices) and the
    constructors are walked;
  * theorems are skipped entirely. By proof irrelevance no statement's meaning
    can depend on which proof of a Prop was used, and walking theorem types
    reached through proof fields of instances would pull in most of Mathlib
    for no information;
  * opaque constants, axioms, recursors and quotient primitives contribute
    their type only;
  * a name that does not resolve is recorded with zero hashes rather than
    dropped, so it cannot hide.

`Expr.hash` is structural and ignores binder names, so alpha-renaming does not
register as drift. It is non-cryptographic and, although typed UInt64, carries
32 significant bits (the rest of Expr.Data holds flags): a given edit escapes
detection with probability about 2^-32. So this detects drift,
including drift an agent introduced; it is not a defence against a collision
crafted on purpose. That case is comparator's, which compares the
declarations themselves. -/
def statementClosure (env : Environment) (root : Name) : Array (Name × UInt64 × UInt64) := Id.run do
  let some rootCi := env.find? root | return #[]
  let mut seen : NameSet := NameSet.empty.insert root
  let mut out : Array (Name × UInt64 × UInt64) := #[(root, rootCi.type.hash, 0)]
  let mut stack : Array Name := rootCi.type.getUsedConstants
  while !stack.isEmpty do
    let n := stack.back!
    stack := stack.pop
    if seen.contains n then continue
    seen := seen.insert n
    match env.find? n with
    | none => out := out.push (n, 0, 0)
    | some (.thmInfo _) => pure ()
    | some c =>
      let (vh, next) : UInt64 × Array Name := match c with
        | .defnInfo d   => (d.value.hash, d.value.getUsedConstants)
        | .inductInfo i => (hash (i.ctors, i.numParams, i.numIndices), i.ctors.toArray)
        | _             => (0, #[])
      out := out.push (n, c.type.hash, vh)
      stack := stack ++ c.type.getUsedConstants ++ next
  return out

def jsonEscape (s : String) : String :=
  s.foldl (init := "") fun acc c =>
    acc ++ match c with
      | '"'  => "\\\""
      | '\\' => "\\\\"
      | '\n' => "\\n"
      | '\t' => "\\t"
      | '\r' => "\\r"
      | _    => String.singleton c

unsafe def main (args : List String) : IO UInt32 := do
  if args.contains "--selftest" then
    return (← runSelftest)
  let allowAttested := args.contains "--allow-attested"
    || (← IO.getEnv "LEAN_AUDIT_ALLOW_ATTESTED").isSome
  let (modStr, declStr) ← match args.filter (fun a => !a.startsWith "--") with
    | [m, d] => pure (m, d)
    | _ => do IO.eprintln "usage: AxiomAudit <Module> <Decl> [--allow-attested]"; return 2
  let allowNative := (← IO.getEnv "LEAN_AUDIT_ALLOW_NATIVE").isSome
  let allowed := if allowNative then baseAllowed ++ [``Lean.ofReduceBool] else baseAllowed

  initSearchPath (← findSysroot)
  -- import the proof module so the declaration is resolvable
  let env ← importModules #[{ module := asModuleName modStr.toName }] {} 0
  let declName := declStr.toName

  match env.find? declName with
  | none =>
    IO.println <| "{\"decl\":\"" ++ jsonEscape declStr ++ "\",\"ok\":false,\"error\":\"decl not found\"}"
    return 2
  | some ci =>
    -- collect axioms transitively
    let (_, s) := ((CollectAxioms.collect declName).run env).run {}
    let axioms := s.axioms
    let usesSorry := axioms.contains ``sorryAx
    let core       := axioms.filter (fun a => allowed.contains a)
    let attested   := axioms.filter (fun a =>
      !(allowed.contains a) && isAttestationAxiom a)
    let disallowed := axioms.filter (fun a =>
      !(allowed.contains a) && !(isAttestationAxiom a))
    let ok := (¬ usesSorry) && disallowed.isEmpty
      && (attested.isEmpty || allowAttested)
    let typeStr ←
      try
        let (fmt, _) ← ((PrettyPrinter.ppExpr ci.type).run').toIO
          { fileName := "<AxiomAudit>", fileMap := default } { env := env }
        pure (toString fmt)
      catch _ => pure "<unprintable>"
    let nameArr := fun (ns : Array Name) => String.intercalate ","
      (ns.toList.map (fun n => "\"" ++ jsonEscape (toString n) ++ "\""))
    -- [name, type hash, value hash] per constant; see statementClosure.
    -- LEAN_AUDIT_NO_CLOSURE=1 omits it (the field is then absent, which the
    -- harness reads as "no closure observed", never as "no drift").
    let closureJson ←
      if (← IO.getEnv "LEAN_AUDIT_NO_CLOSURE").isSome then pure ""
      else
        let rows := (statementClosure env declName).toList.map fun (n, th, vh) =>
          "[\"" ++ jsonEscape (toString n) ++ "\",\"" ++ hex16 th ++ "\",\"" ++ hex16 vh ++ "\"]"
        pure (",\"statement_closure\":[" ++ String.intercalate "," rows ++ "]")
    IO.println <| String.intercalate "" [
      "{\"decl\":\"", jsonEscape declStr, "\"",
      ",\"type\":\"", jsonEscape typeStr, "\"",
      ",\"axioms\":[", nameArr axioms, "]",
      ",\"buckets\":{\"core\":[", nameArr core,
      "],\"attested\":[", nameArr attested,
      "],\"disallowed\":[", nameArr disallowed, "]}",
      closureJson,
      ",\"allow_attested\":", (if allowAttested then "true" else "false"),
      ",\"uses_sorry\":", (if usesSorry then "true" else "false"),
      ",\"ok\":", (if ok then "true" else "false"), "}"
    ]
    return (if ok then 0 else 1)
