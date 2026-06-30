# arith_check — method spec (Trunkit universal method)

Verify a numeric claim by **exact recomputation**. The single highest-leverage method for
LLMs: every model emits numbers, gets them wrong often, and the check is a calculator.

## Claim shape
A claim is a relation between a computed expression and an asserted value:

    <expr>  <relation>  <claimed>

`relation ∈ { = , < , <= , > , >= , ~ }`  ( `~` = approximate, needs `tol` ).

## Witness schema (portable, model-independent)
The witness is a **typed expression AST in JSON** — never natural language, so any model
re-checks the same bytes. Literal nodes carry their type so there is no parsing ambiguity:

    {"int":"123"}                integer (arbitrary precision)
    {"rat":["1","3"]}            exact rational p/q
    {"dec":"6.2832"}             decimal string -> exact rational (NOT a float)
    {"qty":["60","mile/hour"]}   dimensioned quantity (value + unit expression)
    {"const":"pi"|"e"}           pinned constant (rational interval)
    {"op":"+|-|*|/|^","args":[…]}  operator node
    {"float":"0.1"}              raw IEEE float -> deliberately rejected as unverified

Full claim:

    {"semantics":"arith/1",
     "expr": <node>, "relation":"~", "claimed": <node>,
     "tol": {"abs":"0.001"}}            // tol required only for ~

## Verdict semantics (three-valued)
| Verdict | Meaning | Example |
|---|---|---|
| `valid` | relation provably holds | 987654321·123456789 = 121932631112635269 |
| `refuted` | relation provably fails — a real certificate | the same product off by 9; 3 kg = 3 m (dimension mismatch) |
| `unverified` | kernel cannot decide deterministically | unknown `sqrt2`; raw float; interval straddles the tolerance |

`unverified` is the honesty valve: the kernel returns it instead of guessing, so a `valid`
or `refuted` is always trustworthy.

## Determinism rules (what makes verdicts portable)
1. **Exact arithmetic** — int and rational via `Fraction`; no float ever enters a decision.
2. **Transcendentals as rational intervals** — `pi`, `e` are pinned `[lo, hi]` with Fraction
   endpoints; interval arithmetic is exact, so the verdict is identical on every host.
3. **Decimals are rationals** — `"6.2832"` means exactly 62832/10000, not the nearest double.
4. **Raw floats are refused** — an IEEE literal is non-portable, so it returns `unverified`.
5. **Pinned semantics id** (`"arith/1"`) — operator set, constant registry, and rounding are
   versioned, so two models agree on what the witness *means*.

## Units
Quantities carry a 7-vector over SI base dimensions `[m,kg,s,A,K,mol,cd]` plus a Fraction SI
factor. The kernel checks **dimensional consistency** (3 kg = 3 m → refuted) and **conversions**
(5 km = 3 mile → refuted; 60 mph · 2 h = 120 mile → valid) exactly.

## Trunkit mapping
| Trunkit piece | arith_check |
|---|---|
| `curry` pure fn | `arith_eval(expr) -> Value` |
| claim / method | `comp_sql` (or a dedicated `arith_carry`) |
| witness / method | the JSON AST, `witness_carry` |
| `kernel_verify` | `kernel_verify(claim)` — re-evaluates, compares, returns the verdict |
| `claim_export` | the claim + AST is already a self-contained portable bundle |

## Demo results (from `arith_check.py`)
- exact bignum: correct → `valid`; off-by-9 → `refuted`
- rational: 1/3 + 1/6 = 1/2 → `valid`
- units: good convert → `valid`; bad convert / dimension error → `refuted`
- 2·pi ~ 6.2832: tol 1e-3 → `valid`; tol 1e-6 → `refuted` (|Δ| = 1.47e-5)
- 2^64 > 10^19 → `valid`; 13·12·11 < 1700 → `refuted`
- unknown const / raw float → `unverified`

## Files
- `arith_check.py` — kernel, number tower (exact / interval / units), node builders, demo battery.
