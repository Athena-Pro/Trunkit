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
  3. prints a one-line JSON verdict to stdout:
       {"decl":"…","type":"…","axioms":[…],"uses_sorry":false,"ok":true}
  4. exits 0 iff  (¬uses_sorry) ∧ (axioms ⊆ ALLOWED).

ALLOWED defaults to Mathlib's three trusted axioms. `sorryAx` is always a
failure. `Lean.ofReduceBool` (the native_decide trust root) is NOT in ALLOWED by
default; set LEAN_AUDIT_ALLOW_NATIVE=1 to admit it (recorded by the harness).

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
  let (modStr, declStr) ← match args with
    | [m, d] => pure (m, d)
    | _ => do IO.eprintln "usage: AxiomAudit <Module> <Decl>"; return 2
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
    let disallowed := axioms.filter (fun a => !(allowed.contains a))
    let ok := (¬ usesSorry) && disallowed.isEmpty
    let typeStr ←
      try
        let (fmt, _) ← ((PrettyPrinter.ppExpr ci.type).run').toIO
          { fileName := "<AxiomAudit>", fileMap := default } { env := env }
        pure (toString fmt)
      catch _ => pure "<unprintable>"
    let axJson := String.intercalate ","
      (axioms.toList.map (fun n => "\"" ++ jsonEscape (toString n) ++ "\""))
    IO.println <| String.intercalate "" [
      "{\"decl\":\"", jsonEscape declStr, "\"",
      ",\"type\":\"", jsonEscape typeStr, "\"",
      ",\"axioms\":[", axJson, "]",
      ",\"uses_sorry\":", (if usesSorry then "true" else "false"),
      ",\"ok\":", (if ok then "true" else "false"), "}"
    ]
    return (if ok then 0 else 1)
