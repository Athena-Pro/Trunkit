"""Quick script to force-rebuild the sequence cache and capture the NOTIFY payload."""
import time
import psycopg
from nerode.db import resolve_dsn

dsn = resolve_dsn()

conn = psycopg.connect(dsn, autocommit=False)
listen_conn = psycopg.connect(dsn, autocommit=True)
listen_conn.execute("LISTEN nerode_sequence_ready")

# Delete existing cache entry so the build actually runs
conn.execute("DELETE FROM nerode.sequence_cache WHERE seq_key = 'accept_quad:60'")
conn.commit()

# Resolve automaton IDs
rows = conn.execute(
    "SELECT slug, automaton_id FROM nerode.corpus"
    " WHERE slug IN ('cycle_4','cycle_6','cycle_9','cycle_10') ORDER BY slug"
).fetchall()
slug_to_id = {r[0]: r[1] for r in rows}
auto_ids = [slug_to_id[s] for s in ("cycle_4", "cycle_6", "cycle_9", "cycle_10")]

t0 = time.perf_counter()
cache_id = conn.execute(
    "SELECT nerode.build_sequence_cache(%s, %s, %s, %s)",
    ("accept_quad:60", auto_ids, 60, "parallel_accept"),
).fetchone()[0]
conn.commit()
build_ms = (time.perf_counter() - t0) * 1000

print(f"build_sequence_cache returned id={cache_id}  ({build_ms:.1f} ms)")

time.sleep(0.1)  # let the notification propagate
for notify in listen_conn.notifies(timeout=1.0):
    print(f"NOTIFY channel={notify.channel!r}  payload={notify.payload}")
    break
else:
    print("(no NOTIFY received within 1 s)")

conn.close()
listen_conn.close()
