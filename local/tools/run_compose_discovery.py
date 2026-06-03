"""Tier-3 discovery report: compose_index identifications."""

from __future__ import annotations

import sys

from calx import db


def report_run(cur, run_id: int, *, title: str) -> None:
    print(f"=== {title} (run_id={run_id}) ===\n")
    cur.execute("SELECT COUNT(*) FROM sequence_compositions WHERE run_id = %s", (run_id,))
    if cur.fetchone()[0] == 0:
        print("  (no compositions in this run)\n")
        return

    cur.execute(
        """
        SELECT
          sc.selector_kind,
          COUNT(*) FILTER (
              WHERE COALESCE(oc.raw_payload->'scoring'->>'match_kind', '') = 'identification'),
          COUNT(*) FILTER (
              WHERE COALESCE(oc.raw_payload->'scoring'->>'match_kind', '') = 'suggestive'),
          COUNT(*) FILTER (WHERE oc.oeis_id IS NULL AND oc.candidate_id = 1)
        FROM sequence_compositions sc
        LEFT JOIN oeis_compose_candidates oc
          ON oc.composite_id = sc.composite_id AND oc.candidate_id = 1
        WHERE sc.run_id = %s
        GROUP BY sc.selector_kind
        ORDER BY sc.selector_kind
        """,
        (run_id,),
    )
    print("By selector_kind (top candidate):")
    for kind, ident, sugg, no_hit in cur.fetchall():
        print(f"  {kind:<10} identification={ident}  suggestive={sugg}  no_hit={no_hit}")
    print()

    cur.execute(
        """
        SELECT sc.composite_id, sc.base_seq_id, sc.selector_kind, sc.selector_ref,
               sc.selector_start, oc.oeis_id,
               ROUND(oc.confidence::numeric, 3),
               LEFT(oc.oeis_name, 60),
               (SELECT string_agg(n::text, ',' ORDER BY idx)
                FROM composition_membership cm
                WHERE cm.composite_id = sc.composite_id AND cm.idx <= 8)
        FROM sequence_compositions sc
        JOIN oeis_compose_candidates oc ON oc.composite_id = sc.composite_id
        WHERE sc.run_id = %s
          AND oc.candidate_id = 1
          AND COALESCE(oc.raw_payload->'scoring'->>'match_kind', '') = 'identification'
        ORDER BY sc.selector_kind DESC, oc.confidence DESC
        LIMIT 40
        """,
        (run_id,),
    )
    rows = cur.fetchall()
    print("Identifications (structural compose):")
    if not rows:
        print("  (none — expected sparse for orbit-as-C)")
    for cid, base, sk, sref, start, oid, conf, name, prefix in rows:
        label = f"orbit {sref} start={start}" if sk == "orbit" else f"seq {sref}"
        print(f"\n  {cid}")
        print(f"    compose_index({base}, {label})  -> {oid} ({conf})")
        print(f"    prefix [{prefix}]")
        print(f"    {name}")
    print()


def main() -> None:
    run_filter = int(sys.argv[1]) if len(sys.argv) > 1 else None

    with db.connect() as c:
        with c.cursor() as cur:
            if run_filter:
                runs = [(run_filter, "?")]
            else:
                cur.execute(
                    """
                    SELECT r.run_id,
                           (SELECT selector_kind FROM sequence_compositions sc
                            WHERE sc.run_id = r.run_id LIMIT 1)
                    FROM composition_runs r
                    WHERE EXISTS (
                        SELECT 1 FROM sequence_compositions sc WHERE sc.run_id = r.run_id
                    )
                    ORDER BY r.run_id
                    """
                )
                runs = cur.fetchall()
            if not runs:
                print("No composition_runs — run: calx compose-match")
                return

            for run_id, kind_hint in runs:
                report_run(cur, run_id, title=f"Tier 3 [{kind_hint or '?'}]")


if __name__ == "__main__":
    main()
