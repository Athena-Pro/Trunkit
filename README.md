<p align="center">
  <img src="assets/logo.png" alt="Trunkit" width="220" />
</p>

# Trunkit

> The smallest elephant in the room that's not too Coq-y and doesn't Lean too heavily on your system.

Proof-carrying code middleware on PostgreSQL. Trunkit is a self-contained schema stack
that attaches verifiable claims to mathematical objects, chains proofs compositionally,
and lets consumers re-verify results without trusting the producer — all inside a
database you already operate, with no specialist toolchain required.

No 3 GB compiler. No gigabytes of cached proof objects. No new runtime to learn.
Just PostgreSQL, Python, and ~1 MB of schemas that describe, attest, and verify themselves.

---

## Stack

```
+------------------------------------------------------------------+
|  Porter  -- agent context handoff                                 |
|    Precacher * Sources * close_session() * open_session()         |
|    cybernetic DFAs * composite pattern detection                  |
+------------------------------------------------------------------+
|  Nerode  -- deterministic automata engine                         |
|    DFA construction * minimization * product * session DFAs       |
|    sequence cache * cert-signed handoff envelopes                 |
+--------------------------+---------------------------------------+
|  cert                    |  kan                                  |
|  proof-carrying          |  category theory meta-layer           |
|  attestation             |  (monoidal, NTs, Kan, profunctors)    |
+--------------------------+---------------------------------------+
|  curry                   |  calx                                 |
|  versioned provenance    |  integer arithmetic bedrock           |
|  + immutable constants   |  (primes, CRT, dynamics, OEIS)       |
+--------------------------+---------------------------------------+
   Trunkit DB  postgresql://trunk:trunk@localhost:5434/trunk
   Nerode DB   postgresql://nerode:nerode@localhost:5435/nerode
```

| Layer | Role |
|-------|------|
| **calx** | Dense prime factorisation of Z[1..N]; aliquot/derivative dynamics; CRT; OEIS sequence matching |
| **curry** | Immutable versioned constants and functions; append-only computational provenance |
| **kan** | Category-theory meta-layer: base categories, monoidal, NTs, Kan extensions, enrichment, profunctors, adjunctions |
| **cert** | Proof-carrying attestation: five method tiers, structured witness storage, proof composition DAG, portable bundle export, consumer re-verification |
| **Nerode** | DFA/automata engine on PostgreSQL: construction, minimization, product, session DFAs, sequence cache, certified handoff envelopes |
| **Porter** | Agent context handoff: pre-pack external data, certify session boundaries, hand verified context to a new model with zero tool calls |

---

## Quick start

```bash
# 1. Start both PostgreSQL instances
docker compose up -d db-trunkit db-nerode

# 2. Apply all schemas (idempotent)
make apply

# 3. Trunkit: populate integers and run reflexive closure
trunkit generate --limit 10000
trunkit close --write

# 4. Porter: pre-pack a morning brief and open it as Model B
python scripts/morning_brief_demo.py
```

```bash
# Install
pip install trunkit   # proof kernel
pip install nerode    # automata + Porter layer
```

Environment variables:
- `CALX_DSN=postgresql://trunk:trunk@localhost:5434/trunk`
- `NERODE_DSN=postgresql://nerode:nerode@localhost:5435/nerode`

---

## Porter — model-to-model context handoff

Porter solves the cold-start problem for LLM agents. Each new model call starts with no
memory of what the previous call proved, fetched, or decided. Porter pre-packs that
context into a certified envelope before the session ends; the next model opens the
envelope and has everything it needs with zero tool calls.

```python
from nerode.precache import Precacher
from nerode.sources import WeatherSource, TickerSource, HNSource

today = "2026-05-19"

# Model A -- pack context before closing
with Precacher(f"brief-{today}") as pc:
    pc.fetch(f"weather:london:{today}", WeatherSource(51.5, -0.1, label="London"))
    pc.fetch(f"ticker:AAPL:{today}",    TickerSource("AAPL"))
    pc.fetch(f"news:hn:top5:{today}",   HNSource(5))

# Model B -- arrive with full context, cert verified, zero tool calls
ctx = Precacher.open(pc.envelope, "model-b-001")
resolved = ctx["resolved"]   # all three values, ready to use
```

### Cybernetic monitoring

Porter includes DFA-based pattern detectors that fire over metric and control streams:

| DFA | Pattern | Meaning |
|-----|---------|---------|
| `metric_rise_3` | `U{3,}` | 3+ consecutive rises |
| `metric_oscillate` | `(UD){3,}` | oscillation / gain too high |
| `dead_time_k` | `A_{k,}` | action without response in k steps |
| `homeostasis_alarm_5` | `O{5,}` | 5+ steps outside target band |
| `dead_time_5_x_metric_oscillate` | composite | oscillating AND unresponsive |

```python
# Log a paired (metric, control) event and scan all relevant DFAs
conn.execute(
    "SELECT nerode.log_cybernetic(%s, 'metric_x_control', %s, %s)",
    (session_id, "UA", json.dumps({"src": "sensor"}))
)
# pg_notify('nerode_control_warn', ...) fires if any pattern matches
```

---

## cert method tiers

| Method | Trust root | Use |
|--------|-----------|-----|
| `comp_sql` | In-DB probe | Computational facts about integers or categorical counts |
| `struct_kan` | Existing kan invariant | Naturality, triangle identities, faithfulness checks |
| `formal_external` | SHA256-pinned external artifact | Python/Lean/Agda proof scripts |
| `empirical_corpus` | Provenance only | Corpus document assertions |
| `witness_carry` | In-DB witness term | Structured proof terms stored alongside certificates; consumer-replayable |

---

## CLI

Trunkit ships a dual-surface CLI. Consumer commands are read-only and safe for LLM use.
Prover commands require `--write` to record; they dry-run without it.

```bash
# Consumer (read-only)
trunkit verify <claim_id>                    # re-verify without inserting
trunkit standing [--method M] [--status S]   # list claims by status
trunkit export <id> [<id> ...]               # portable JSONB proof bundle to stdout

# Prover (dry-run without --write)
trunkit check <claim_id> [--write]
trunkit attest [--write]
trunkit close [--write]
trunkit witness <claim_id> --kind KIND --body JSON [--write]

# calx data
trunkit generate --limit N
trunkit validate [--limit N]
trunkit oeis-load / oeis-match / compose-match
```

---

## Repository layout

```
src/
  calx/       -- Trunkit Python package (calx + kan + curry + cert)
  nerode/     -- Nerode/Porter Python package
    sql/      -- 18 idempotent SQL schema files (00_bootstrap to 97_composite_dfa)
    precache.py      -- Precacher context manager (Porter API)
    sources.py       -- WeatherSource, TickerSource, HNSource, TickerHistorySource
    adapters.py      -- HttpSource, CallableSource, resolve(), with_retry()
    db.py            -- connection utilities + SCHEMA_FILES list
scripts/
  morning_brief_demo.py   -- end-to-end Porter demo
tests/
  test_sources.py          -- network tests (pytest -m network)
  test_cybernetic.py       -- cybernetic DFA tests
  test_composite_dfa.py    -- paired-alphabet composite DFA tests
  test_dead_time_factory.py
  test_*.py                -- unit tests for all schema layers
proofs/
  *.py    -- Trunkit proof scripts
tools/
  kan_in_kan.py   -- Trunkit reflexive closure tool
```

---

## Bundle size

| Component | Size |
|-----------|------|
| SQL schemas (calx + nerode) | ~300 KB |
| Python source | ~330 KB |
| Proof scripts | ~175 KB |
| Tests + config | ~130 KB |
| **Total (no virtualenv)** | **~1.1 MB** |

Compare: Lean 4 toolchain ~2.9 GB; Mathlib compiled ~4-10 GB.

---

## License

MIT
