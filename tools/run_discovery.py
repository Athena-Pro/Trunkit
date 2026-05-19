"""Print OEIS orbit discovery summary from oeis_match_candidates."""

from __future__ import annotations

from calx import db


def main() -> None:
    with db.connect() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT
                  COALESCE(raw_payload->'scoring'->>'match_kind', 'legacy'),
                  COUNT(DISTINCT orbit_id)
                FROM oeis_match_candidates
                WHERE candidate_id = 1 AND oeis_id IS NOT NULL
                GROUP BY 1
                ORDER BY 1
                """
            )
            kind_rows = cur.fetchall()
            print("=== Spectrum (top candidate per orbit, by match_kind) ===")
            for kind, n in kind_rows:
                print(f"  {kind:<16} {n}")
            print()

            cur.execute(
                """
                SELECT
                  COUNT(DISTINCT orbit_id) FILTER (
                      WHERE oeis_id IS NOT NULL
                        AND COALESCE(raw_payload->'scoring'->>'match_kind', '') = 'identification'),
                  COUNT(DISTINCT orbit_id) FILTER (WHERE oeis_id IS NULL),
                  COUNT(DISTINCT orbit_id) FILTER (
                      WHERE oeis_id IS NOT NULL
                        AND COALESCE(raw_payload->'scoring'->>'match_kind', '') = 'suggestive'),
                  COUNT(DISTINCT orbit_id) FILTER (
                      WHERE oeis_id IS NOT NULL
                        AND COALESCE(raw_payload->'scoring'->>'match_kind', '') = 'tautology')
                FROM oeis_match_candidates
                WHERE candidate_id = 1
                """
            )
            identified, no_match, suggestive, tautology = cur.fetchone()
            print("=== Summary ===")
            print(f"  identification:    {identified}")
            print(f"  suggestive:        {suggestive}")
            print(f"  tautology (top):   {tautology}")
            print(f"  cached no-match:   {no_match}\n")

            cur.execute(
                """
                SELECT m.oeis_id, LEFT(m.oeis_name, 72), COUNT(DISTINCT o.orbit_id),
                       ROUND(AVG(m.confidence)::numeric, 3),
                       array_agg(DISTINCT o.rel_type ORDER BY o.rel_type)
                FROM oeis_match_candidates m
                JOIN orbits o USING (orbit_id)
                WHERE m.confidence >= 0.9 AND m.oeis_id IS NOT NULL
                  AND COALESCE(m.raw_payload->'scoring'->>'match_kind', '') = 'identification'
                GROUP BY m.oeis_id, m.oeis_name
                ORDER BY COUNT(DISTINCT o.orbit_id) DESC, m.oeis_id
                LIMIT 25
                """
            )
            rows = cur.fetchall()
            cur.execute(
                """
                SELECT o.orbit_id, o.rel_type,
                       (SELECT n FROM orbits WHERE orbit_id = o.orbit_id ORDER BY step LIMIT 1),
                       m.oeis_id, ROUND(m.confidence::numeric, 3),
                       COALESCE(m.raw_payload->'scoring'->>'match_kind', '?'),
                       LEFT(m.oeis_name, 50)
                FROM oeis_match_candidates m
                JOIN (SELECT DISTINCT orbit_id, rel_type FROM orbits) o USING (orbit_id)
                WHERE m.candidate_id = 1
                  AND COALESCE(m.raw_payload->'scoring'->>'match_kind', '') = 'suggestive'
                ORDER BY m.confidence DESC
                LIMIT 15
                """
            )
            sug = cur.fetchall()
            if sug:
                print("=== Suggestive band (0.6-0.9) ===")
                for oid, rel, start, aid, conf, kind, nm in sug:
                    print(f"  orbit {oid:>3} {rel:<12} from {start:>4} -> {aid} ({conf})  {nm}")
                print()

            print("=== Structural discovery (match_kind = identification) ===")
            if not rows:
                print("  (no high-confidence hits)")
            for oeis_id, name, n_orb, avg_c, rels in rows:
                print(f"\n{oeis_id}  ({n_orb} orbits, avg conf {avg_c})  via {rels}")
                print(f"  {name}")

            cur.execute(
                """
                SELECT o.orbit_id, o.rel_type,
                       (SELECT n FROM orbits WHERE orbit_id = o.orbit_id ORDER BY step LIMIT 1),
                       m.oeis_id, ROUND(m.confidence::numeric, 3), LEFT(m.oeis_name, 55)
                FROM oeis_match_candidates m
                JOIN (SELECT DISTINCT orbit_id, rel_type FROM orbits) o USING (orbit_id)
                WHERE m.candidate_id = 1 AND m.confidence >= 0.9 AND m.oeis_id IS NOT NULL
                  AND COALESCE(m.raw_payload->'scoring'->>'match_kind', '') = 'identification'
                ORDER BY m.confidence DESC, o.rel_type
                LIMIT 25
                """
            )
            print("\n=== Sample orbit identifications ===")
            for oid, rel, start, aid, conf, nm in cur.fetchall():
                print(f"  orbit {oid:>3} {rel:<12} from {start:>4} -> {aid} ({conf})  {nm}")


if __name__ == "__main__":
    main()
