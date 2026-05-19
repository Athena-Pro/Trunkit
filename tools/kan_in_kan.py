"""kan-in-kan: a self-analytical bootstrap for the unified model.

The analogue of tel/curry_in_curry.py, adapted to the calx/curry/kan Postgres
model. The unified model uses its OWN facilities to describe, record, and
validate itself:

  Phase 1  kan reflects calx/curry/kan into itself; declares unified-model
           self-constants in the `curry` schema (immutable, versioned).
  Phase 2  registers a `kan-self-model` and records this bootstrap run as a
           `curry.inferences` row — provenance of the self-analysis.
  Phase 3  registers the identity endofunctor kan -> kan (kan describing its
           own structure as a functor), alongside the existing curry_to_calx.
  Phase 4  LAYER AUTO-CONFIDENCE REPORT — scans the live DB and auto-detects,
           by counts (evidence, not opinion), which documented KAN layers and
           unified-model invariants are demonstrably present. Fails on any gap.
  Phase 5  persists the report as a versioned `kan_self_report` JSON constant
           in `curry` (each run is a new immutable version — an audit trail).

Idempotent. Exit code 1 if coverage < the KAN_COVERAGE_THRESHOLD constant.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)


# --- curry constant (de)serialization, mirroring Curry's SQLite semantics ----

def _enc(value, type_sig: str) -> bytes:
    if type_sig in ("String", "Int32", "Float64", "Json", "Bool"):
        return json.dumps(value).encode("utf-8")
    raise ValueError(f"unsupported type_signature {type_sig!r}")


def _dec(raw: bytes):
    return json.loads(bytes(raw).decode("utf-8"))


def declare_constant(cur, cid: str, value, type_sig: str, *, description=None) -> int:
    """Append-only versioned constant in curry.constants. Returns the version used.

    If an identical (value,type) tip already exists, it is reused (idempotent);
    otherwise a new monotonic version is appended.
    """
    cur.execute(
        "SELECT version, value, type_signature FROM curry.constants "
        "WHERE id=%s ORDER BY version DESC LIMIT 1",
        (cid,),
    )
    row = cur.fetchone()
    if row is not None:
        cur_ver, cur_val, cur_type = row
        if cur_type == type_sig and _dec(cur_val) == value:
            return cur_ver
        nxt = cur_ver + 1
    else:
        nxt = 1
    cur.execute(
        "INSERT INTO curry.constants (id, version, value, type_signature, description) "
        "VALUES (%s, %s, %s, %s, %s)",
        (cid, nxt, _enc(value, type_sig), type_sig, description),
    )
    return nxt


def get_constant_latest(cur, cid: str):
    cur.execute(
        "SELECT value FROM curry.constants WHERE id=%s AND retired_at IS NULL "
        "ORDER BY version DESC LIMIT 1",
        (cid,),
    )
    row = cur.fetchone()
    return None if row is None else _dec(row[0])


# --- Phase 4 evidence checks: (layer, description, sql, expected_min) ---------
# Evidence is a COUNT; covered iff count >= expected_min. No opinions.

LAYER_CHECKS = [
    ("L0  base",          "kan.category/object/morphism populated",
     "SELECT (SELECT count(*) FROM kan.category) "
     "+ (SELECT count(*) FROM kan.object) + (SELECT count(*) FROM kan.morphism)", 1),
    ("L0  functor",       "curry_to_calx object map (expect 19)",
     "SELECT count(*) FROM kan.functor_object_map WHERE functor='curry_to_calx'", 19),
    ("L0b elements",      "kan.element table present",
     "SELECT count(*) FROM information_schema.tables "
     "WHERE table_schema='kan' AND table_name IN ('element','composition','element_identity')", 3),
    ("L1  monoidal",      "monoidal structure tables present",
     "SELECT count(*) FROM information_schema.tables WHERE table_schema='kan' "
     "AND table_name IN ('monoidal_structure','tensor_product','involution_result')", 3),
    ("L2  nat-transf",    "natural_transformation tables present",
     "SELECT count(*) FROM information_schema.tables WHERE table_schema='kan' "
     "AND table_name IN ('natural_transformation','nt_component')", 2),
    ("L3  Kan-ext",       "extension_request tables present",
     "SELECT count(*) FROM information_schema.tables WHERE table_schema='kan' "
     "AND table_name IN ('extension_request','extension_object_map','extension_morphism_map')", 3),
    ("L4  enrichment",    "enrichment tables present",
     "SELECT count(*) FROM information_schema.tables WHERE table_schema='kan' "
     "AND table_name IN ('enrichment','linear_element','lc_term')", 3),
    ("L5  profunctors",   "profunctor tables present",
     "SELECT count(*) FROM information_schema.tables WHERE table_schema='kan' "
     "AND table_name IN ('profunctor','profunctor_cell')", 2),
    ("L6  adjunctions",   "adjunction table present",
     "SELECT count(*) FROM information_schema.tables "
     "WHERE table_schema='kan' AND table_name='adjunction'", 1),
    ("L30 corpus",        "corpus documents loaded (expect 5)",
     "SELECT count(*) FROM kan.corpus_document", 5),
    ("L30 corpus-fts",    "corpus chunks indexed for FTS",
     "SELECT count(*) FROM kan.corpus_chunk", 1),
    ("INV reflexive",     "kan describes itself (category kan -> schema kan)",
     "SELECT count(*) FROM kan.category WHERE name='kan' AND db_schema='kan'", 1),
    ("INV abstract",      "explicit-mode category exists (db_schema NULL)",
     "SELECT count(*) FROM kan.category WHERE db_schema IS NULL", 1),
    ("INV curry-port",    "Curry store ported (constants+functions)",
     "SELECT (SELECT count(*) FROM curry.constants) "
     "+ (SELECT count(*) FROM curry.functions)", 25),
    ("INV calx-data",     "calx arithmetic data present",
     "SELECT count(*) FROM calx.integers", 1),
]


def banner(t: str) -> None:
    print("=" * 70)
    print(t)
    print("=" * 70)


def main() -> int:
    banner("  KAN IN KAN: Self-Analytical Bootstrap of the Unified Model")
    print()

    with psycopg.connect(PG_DSN) as conn:
        conn.execute("SET search_path = calx, curry, kan, public")
        with conn.cursor() as cur:

            # ---- Phase 1: reflect + declare self-constants -------------------
            banner("PHASE 1: The model reflects and declares itself")
            for c in ("calx", "curry", "kan"):
                cur.execute("SELECT kan.sync_category(%s, %s)", (c, c))
            print("  [OK] kan.sync_category x3 (calx, curry, kan)")

            v_ver = declare_constant(cur, "unified_model_version", "1.0", "String",
                                     description="calx/curry/kan unified model")
            v_layers = declare_constant(cur, "kan_layer_count_expected", 8, "Int32",
                                        description="documented KAN layers 0,0b,1-6")
            v_thr = declare_constant(cur, "KAN_COVERAGE_THRESHOLD", 1.0, "Float64",
                                     description="min fraction of checks that must pass")
            print(f"  [OK] curry.constants: unified_model_version@v{v_ver}='1.0'")
            print(f"  [OK] curry.constants: kan_layer_count_expected@v{v_layers}=8")
            print(f"  [OK] curry.constants: KAN_COVERAGE_THRESHOLD@v{v_thr}=1.0")

            # ---- Phase 2: register self-model + record provenance -----------
            banner("PHASE 2: Record this bootstrap as its own provenance")
            cur.execute(
                "INSERT INTO curry.model_versions "
                "(model_name, version, checkpoint_hash, temperature, top_p, max_tokens) "
                "VALUES ('kan-self-model', 1, 'kan-in-kan-v1', 1.0, 0.9, 4096) "
                "ON CONFLICT (model_name, version) DO NOTHING"
            )
            inf_id = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO curry.inferences "
                "(inference_id, model_name, model_version, input_tokens, "
                " output_tokens, temperature_used, seed, metadata) "
                "VALUES (%s, 'kan-self-model', 1, %s, %s, 1.0, 42, %s)",
                (
                    inf_id,
                    json.dumps({"operation": "kan_in_kan_bootstrap"}),
                    b"self-analysis started",
                    Jsonb({"phase": "bootstrap", "started_at":
                           datetime.now(timezone.utc).isoformat()}),
                ),
            )
            print(f"  [OK] curry.model_versions: kan-self-model@v1")
            print(f"  [OK] curry.inferences: {inf_id} (operation=kan_in_kan_bootstrap)")

            # ---- Phase 3: identity endofunctor kan -> kan -------------------
            banner("PHASE 3: kan describes its own structure as a functor")
            cur.execute(
                "INSERT INTO kan.functor (name, src_category, tgt_category, description) "
                "VALUES ('kan_self', 'kan', 'kan', "
                "'Identity endofunctor: kan reflecting its own objects') "
                "ON CONFLICT (name) DO NOTHING"
            )
            cur.execute(
                "INSERT INTO kan.functor_object_map (functor, src_object, tgt_object) "
                "SELECT 'kan_self', name, name FROM kan.object WHERE category='kan' "
                "ON CONFLICT (functor, src_object) DO NOTHING"
            )
            cur.execute(
                "SELECT count(*) FROM kan.functor_object_map WHERE functor='kan_self'"
            )
            n_self = cur.fetchone()[0]
            print(f"  [OK] kan.functor: kan_self (identity endofunctor)")
            print(f"  [OK] kan.functor_object_map: {n_self} objects mapped id->id")

            # ---- Phase 4: layer auto-confidence report ----------------------
            banner("PHASE 4: Layer Auto-Confidence Report (evidence, not opinion)")
            covered, missing, evidence = [], [], {}
            for layer, desc, sql, need in LAYER_CHECKS:
                cur.execute(sql)
                got = cur.fetchone()[0] or 0
                evidence[layer.strip()] = {"got": got, "need": need, "desc": desc}
                if got >= need:
                    covered.append((layer, desc, got, need))
                else:
                    missing.append((layer, desc, got, need))

            total = len(LAYER_CHECKS)
            coverage = len(covered) / total
            print(f"  Coverage: {len(covered)}/{total} checks "
                  f"({coverage:.0%})  evidence-based\n")
            for layer, desc, got, need in covered:
                print(f"  [COVERED] {layer:16s} {desc}  ({got} >= {need})")
            for layer, desc, got, need in missing:
                print(f"  [GAP]     {layer:16s} {desc}  ({got} < {need})")

            threshold = get_constant_latest(cur, "KAN_COVERAGE_THRESHOLD") or 1.0

            # ---- Phase 5: persist the report as a versioned constant --------
            banner("PHASE 5: Persist the self-report (immutable, versioned)")
            report = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "coverage": coverage,
                "threshold": threshold,
                "checks_total": total,
                "checks_covered": len(covered),
                "evidence": evidence,
                "provenance_inference": inf_id,
            }
            rv = declare_constant(cur, "kan_self_report", report, "Json",
                                  description="kan-in-kan auto-confidence report")
            print(f"  [OK] curry.constants: kan_self_report@v{rv} persisted")

            # ---- Phase 6: certificate attestation (the cert pillar) --------
            # Counts (Phase 4) graduate to attestations: re-check every claim
            # AFTER persisting this run's report, so the reflexive claim
            # ("latest kan_self_report coverage = 1.0") attests THIS run.
            banner("PHASE 6: Certificate attestation (counts -> attestations)")
            cert_ok = True
            try:
                cur.execute("SELECT count(*) FROM cert.claim")
                n_claims = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM cert.check_all()")  # re-check all
                cur.execute(
                    "SELECT status, count(*) FROM cert.standing "
                    "GROUP BY status ORDER BY status"
                )
                by_status = cur.fetchall()
                cur.execute("SELECT count(*) FROM cert.standing WHERE status='valid'")
                n_valid = cur.fetchone()[0]
                cert_ok = (n_valid == n_claims and n_claims > 0)
                print(f"  claims attested: {n_valid}/{n_claims} valid  "
                      f"({', '.join(f'{s}={c}' for s, c in by_status)})")
                cur.execute(
                    "SELECT statement, status FROM cert.standing "
                    "WHERE statement LIKE 'latest kan_self_report%'"
                )
                refl = cur.fetchone()
                if refl:
                    print(f"  [{'OK ' if refl[1]=='valid' else 'FAIL'}] reflexive "
                          f"closure: {refl[0]} -> {refl[1]}")
            except psycopg.Error as exc:
                cert_ok = False
                print(f"  [GAP] cert pillar not available: {exc}")

            conn.commit()

    print()
    banner("BOOTSTRAP COMPLETE")
    failed = False
    if coverage + 1e-9 < threshold:
        print(f"  [FAIL] coverage {coverage:.0%} < threshold {threshold:.0%}: "
              f"{[m[0].strip() for m in missing]}")
        failed = True
    if not cert_ok:
        print("  [FAIL] certificate attestation incomplete "
              "(some claims not 'valid')")
        failed = True
    if failed:
        return 1
    print(f"  [OK] coverage {coverage:.0%} >= threshold {threshold:.0%}; "
          f"all claims cert-attested — the unified model describes, "
          f"validates, AND attests itself.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
