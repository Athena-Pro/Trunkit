#!/usr/bin/env python3
"""Generate STATUS_BOARD.html — a self-contained, dark-theme, color-coded
visual representation of cert.board_summary.

One card per area.  Card accent colour:
  green  — all claims verified (failed=0, unknown=0)
  amber  — some unknown but no failures
  red    — at least one failure
  gray   — no claims yet (total=0)

A progress bar inside each card shows verified / total.  Failing claims are
listed in a "Needs attention" section below the grid.

Run standalone or imported as a module (call generate(dsn) → html string).
"""
import datetime
import os
import psycopg

DSN = os.environ.get("TRUNK_DSN", "postgresql://trunk:trunk@localhost:5434/trunk")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "STATUS_BOARD.html")

# ---------------------------------------------------------------------------
# HTML template — fully self-contained, zero external deps
# ---------------------------------------------------------------------------
_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Federation Status Board</title>
  <style>
    :root {{
      --green:   #22c55e;
      --red:     #ef4444;
      --amber:   #f59e0b;
      --gray:    #6b7280;
      --bg:      #0d1117;
      --surface: #161b22;
      --border:  #30363d;
      --text:    #e6edf3;
      --muted:   #8b949e;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, sans-serif;
      padding: 2rem 2.5rem;
      max-width: 1100px;
      margin: 0 auto;
    }}
    h1 {{ font-size: 1.5rem; font-weight: 700; margin-bottom: .3rem; }}
    .subtitle {{ color: var(--muted); font-size: .85rem; margin-bottom: 2rem; }}
    /* ---- headline stat row ---- */
    .headline {{ display: flex; gap: 1rem; margin-bottom: 2.5rem; flex-wrap: wrap; }}
    .stat {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: .9rem 1.4rem;
      text-align: center;
      min-width: 100px;
    }}
    .stat-num  {{ font-size: 2rem; font-weight: 700; line-height: 1; }}
    .stat-label {{ font-size: .75rem; color: var(--muted); margin-top: .3rem; }}
    .stat.green .stat-num {{ color: var(--green); }}
    .stat.red   .stat-num {{ color: var(--red);   }}
    .stat.amber .stat-num {{ color: var(--amber); }}
    .stat.gray  .stat-num {{ color: var(--gray);  }}
    /* ---- area grid ---- */
    .section-label {{
      font-size: .7rem; text-transform: uppercase; letter-spacing: .06em;
      color: var(--muted); margin-bottom: .9rem;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
      gap: .85rem;
      margin-bottom: 2.5rem;
    }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
    }}
    .card-accent {{ height: 3px; }}
    .card-body   {{ padding: .9rem 1rem; }}
    .card-title  {{ font-size: .85rem; font-weight: 600; margin-bottom: .65rem; line-height: 1.3; }}
    .chips {{ display: flex; gap: .4rem; flex-wrap: wrap; }}
    .chip {{
      font-size: .7rem; padding: .15rem .55rem;
      border-radius: 4px; font-weight: 600;
    }}
    .chip.v {{ background: rgba(34,197,94,.15);  color: var(--green); }}
    .chip.f {{ background: rgba(239,68,68,.15);  color: var(--red);   }}
    .chip.u {{ background: rgba(107,114,128,.15);color: var(--gray);  }}
    .progress {{
      margin-top: .7rem; height: 3px;
      background: var(--border); border-radius: 2px; overflow: hidden;
    }}
    .progress-fill {{ height: 100%; border-radius: 2px; }}
    /* ---- failures section ---- */
    .failures-section {{ margin-bottom: 2.5rem; }}
    .failures-section h2 {{ font-size: 1rem; font-weight: 600; margin-bottom: .75rem; color: var(--red); }}
    .failure-item {{
      background: var(--surface); border: 1px solid #3d1f1f;
      border-left: 3px solid var(--red);
      border-radius: 6px; padding: .6rem .9rem;
      margin-bottom: .5rem; font-size: .82rem;
    }}
    .failure-area {{ font-weight: 600; color: var(--red); margin-right: .4rem; }}
    /* ---- legend ---- */
    .legend {{ display: flex; gap: 1.5rem; flex-wrap: wrap; margin-bottom: 2rem; }}
    .legend-item {{ display: flex; align-items: center; gap: .4rem; font-size: .78rem; color: var(--muted); }}
    .swatch {{ width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }}
    footer {{ font-size: .73rem; color: var(--muted); border-top: 1px solid var(--border); padding-top: 1rem; }}
    footer code {{ background: var(--surface); padding: .1em .4em; border-radius: 3px; font-size: .9em; }}
  </style>
</head>
<body>
  <h1>🔐 Federation Status Board</h1>
  <p class="subtitle">Generated {date} &nbsp;·&nbsp; {total} tracked claims &nbsp;·&nbsp; Trunkit cert ledger (<code>cert.board_summary</code>)</p>

  <div class="headline">
    <div class="stat green">
      <div class="stat-num">{verified}</div>
      <div class="stat-label">verified</div>
    </div>
    <div class="stat red">
      <div class="stat-num">{failed}</div>
      <div class="stat-label">failed</div>
    </div>
    <div class="stat gray">
      <div class="stat-num">{unknown}</div>
      <div class="stat-label">unknown</div>
    </div>
    <div class="stat amber">
      <div class="stat-num">{pct}%</div>
      <div class="stat-label">verified rate</div>
    </div>
  </div>

  <p class="section-label">By area</p>
  <div class="grid">
{cards}
  </div>

{failures_html}
  <div class="legend">
    <span class="legend-item"><span class="swatch" style="background:var(--green)"></span>all verified</span>
    <span class="legend-item"><span class="swatch" style="background:var(--amber)"></span>some unknown, no failures</span>
    <span class="legend-item"><span class="swatch" style="background:var(--red)"></span>has failures</span>
    <span class="legend-item"><span class="swatch" style="background:var(--gray)"></span>no claims yet</span>
  </div>

  <footer>
    Backed by Postgres &nbsp;·&nbsp;
    <code>python tools/gen_board.py</code> &nbsp;·&nbsp;
    green = probe ran &amp; confirmed &nbsp;·&nbsp;
    red = probe ran &amp; contradiction found &nbsp;·&nbsp;
    gray = not yet checkable
  </footer>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Card HTML helper
# ---------------------------------------------------------------------------
def _card(area: str, verified: int, failed: int, unknown: int, total: int) -> str:
    if total == 0:
        accent, fill_color = "#6b7280", "#6b7280"
    elif failed > 0:
        accent, fill_color = "#ef4444", "#ef4444"
    elif unknown > 0:
        accent, fill_color = "#f59e0b", "#f59e0b"
    else:
        accent, fill_color = "#22c55e", "#22c55e"

    pct = round(100 * verified / total) if total else 0

    chips = []
    if verified:
        chips.append(f'<span class="chip v">✓ {verified}</span>')
    if failed:
        chips.append(f'<span class="chip f">✗ {failed}</span>')
    if unknown:
        chips.append(f'<span class="chip u">? {unknown}</span>')
    chips_html = "\n        ".join(chips) if chips else '<span class="chip u">? 0</span>'

    return f"""\
    <div class="card">
      <div class="card-accent" style="background:{accent}"></div>
      <div class="card-body">
        <div class="card-title">{area}</div>
        <div class="chips">
        {chips_html}
        </div>
        <div class="progress">
          <div class="progress-fill" style="width:{pct}%;background:{fill_color}"></div>
        </div>
      </div>
    </div>"""


# ---------------------------------------------------------------------------
# Main generation logic
# ---------------------------------------------------------------------------
def generate(dsn: str = DSN) -> str:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FILTER (WHERE status IN ('valid','pass')), "
            "       count(*) FILTER (WHERE status='refuted'), "
            "       count(*) FILTER (WHERE status IN ('unverified','unchecked','error')), "
            "       count(*) "
            "FROM cert.board"
        )
        v, f, u, t = cur.fetchone()

        cur.execute(
            "SELECT area, verified, failed, unknown, total "
            "FROM cert.board_summary ORDER BY area"
        )
        areas = cur.fetchall()

        cur.execute(
            "SELECT area, left(statement, 100) "
            "FROM cert.board WHERE status = 'refuted' ORDER BY area"
        )
        fails = cur.fetchall()

    cards_html = "\n".join(_card(area, vv, ff, uu, tt) for area, vv, ff, uu, tt in areas)

    if fails:
        items = "\n".join(
            f'    <div class="failure-item">'
            f'<span class="failure-area">[{area}]</span>{stmt}</div>'
            for area, stmt in fails
        )
        failures_html = (
            '  <div class="failures-section">\n'
            '    <h2>❌ Needs attention</h2>\n'
            f'{items}\n'
            '  </div>\n'
        )
    else:
        failures_html = ""

    pct = round(100 * v / t) if t else 0
    return _HTML.format(
        date=datetime.date.today().isoformat(),
        total=t, verified=v, failed=f, unknown=u, pct=pct,
        cards=cards_html,
        failures_html=failures_html,
    )


def main() -> None:
    html = generate()
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)
    # Count cards
    n_areas = html.count('class="card"')
    print(f"wrote STATUS_BOARD.html  ({n_areas} area cards, {len(html)} bytes)")


if __name__ == "__main__":
    main()
