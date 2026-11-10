"""Self-contained HTML report — inline CSS only, no JavaScript, no external assets."""
from __future__ import annotations

import html
from pathlib import Path

from .analyze import Analysis
from .model import GB


def _bar(value: float, maximum: float) -> str:
    pct = 0.0 if maximum <= 0 else max(2.0, value / maximum * 100.0)
    return f'<div class="bar-wrap"><div class="bar" style="width:{pct:.1f}%"></div></div>'


def _rollup_rows(analysis: Analysis, rollups, kind: str) -> str:
    maximum = max((r.credits for r in rollups), default=0.0)
    rows = []
    for r in rollups:
        rows.append(
            "<tr>"
            f"<td>{html.escape(r.name)}</td><td>{r.queries}</td>"
            f"<td>{r.credits:.3f}</td><td>{_bar(r.credits, maximum)}</td>"
            f"<td>{r.bytes_scanned / GB:.2f}</td><td>{r.flagged_queries}</td>"
            "</tr>"
        )
    return (
        f"<h2>Credits by {kind}</h2><table><thead><tr><th>{kind.title()}</th><th>Queries</th>"
        "<th>Credits</th><th></th><th>GB scanned</th><th>Flagged</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_html(analysis: Analysis) -> str:
    a = analysis
    top_wh = a.by_warehouse[0] if a.by_warehouse else None
    top_user = a.by_user[0] if a.by_user else None

    flag_rows = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{count}</td></tr>"
        for name, count in a.flag_counts.items()
    )

    maximum_day = max((d.credits for d in a.by_day), default=0.0)
    day_rows = []
    for d in a.by_day:
        marker = ' class="anomaly"' if d.is_anomaly else ""
        z = f"{d.z_score:.2f}" if d.z_score is not None else "—"
        day_rows.append(
            f"<tr{marker}><td>{html.escape(d.day)}</td><td>{d.queries}</td>"
            f"<td>{d.credits:.3f}</td><td>{_bar(d.credits, maximum_day)}</td><td>{z}</td>"
            f"<td>{'ANOMALY' if d.is_anomaly else ''}</td></tr>"
        )

    top_rows = []
    for r in a.top_expensive(10):
        flags = ", ".join(a.flags_by_id.get(r.query_id, [])) or "—"
        text = html.escape((r.query_text or "")[:90])
        top_rows.append(
            "<tr>"
            f"<td>{html.escape(r.query_id)}</td><td>{html.escape(r.user_name)}</td>"
            f"<td>{html.escape(r.warehouse_name)} ({html.escape(r.warehouse_size)})</td>"
            f"<td>{r.execution_seconds:.0f}</td><td>{r.bytes_scanned / GB:.2f}</td>"
            f"<td>{r.credits:.3f}</td><td>{html.escape(flags)}</td><td class='q'>{text}</td>"
            "</tr>"
        )

    anomaly_text = (
        ", ".join(f"{d.day} (z={d.z_score:.2f}, {d.credits:.2f} credits)" for d in a.anomalies)
        if a.anomalies
        else "None detected"
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Snowflake Cost Guard — Report</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:2rem auto;max-width:1080px;padding:0 1rem;color:#17202a;background:#fbfcfd}}
h1{{margin-bottom:.2rem}} .sub{{color:#5d6d7e;margin-top:0}}
.cards{{display:flex;gap:1rem;flex-wrap:wrap;margin:1.2rem 0}}
.card{{background:#fff;border:1px solid #d5dbdb;border-radius:10px;padding:1rem 1.2rem;min-width:170px}}
.card b{{font-size:1.5rem;display:block}} .card span{{color:#5d6d7e;font-size:.85rem}}
table{{border-collapse:collapse;width:100%;background:#fff;margin:.6rem 0 1.6rem}}
th,td{{border:1px solid #e5e8e8;padding:.45rem .6rem;text-align:left;font-size:.9rem}}
th{{background:#eaf2f8}} .bar-wrap{{background:#eaeded;width:140px;height:10px;border-radius:5px}}
.bar{{background:#2e86c1;height:10px;border-radius:5px}} tr.anomaly{{background:#fdedec;font-weight:600}}
td.q{{font-family:ui-monospace,monospace;font-size:.78rem;color:#555}}
.note{{color:#5d6d7e;font-size:.85rem}}
</style></head><body>
<h1>Snowflake Cost Guard</h1>
<p class="sub">Per-query credit attribution from Snowflake ACCOUNT_USAGE.QUERY_HISTORY. Credits are an execution-time estimate — see limitations in the README.</p>
<div class="cards">
<div class="card"><b>{a.total_credits:.2f}</b><span>Total estimated credits</span></div>
<div class="card"><b>{a.total_queries}</b><span>Queries analysed</span></div>
<div class="card"><b>{a.flagged_queries}</b><span>Flagged queries</span></div>
<div class="card"><b>{html.escape(top_wh.name) if top_wh else '—'}</b><span>Top warehouse ({top_wh.credits:.2f} credits)</span></div>
<div class="card"><b>{html.escape(top_user.name) if top_user else '—'}</b><span>Top user ({top_user.credits:.2f} credits)</span></div>
</div>
<h2>Waste flags</h2>
<table><thead><tr><th>Flag</th><th>Queries</th></tr></thead><tbody>{flag_rows}</tbody></table>
<p><strong>Anomaly days:</strong> {html.escape(anomaly_text)}</p>
{_rollup_rows(a, a.by_warehouse, 'warehouse')}
{_rollup_rows(a, a.by_user, 'user')}
<h2>Daily burn</h2>
<table><thead><tr><th>Day</th><th>Queries</th><th>Credits</th><th></th><th>z-score vs prior days</th><th></th></tr></thead>
<tbody>{''.join(day_rows)}</tbody></table>
<h2>Top 10 expensive queries</h2>
<table><thead><tr><th>Query ID</th><th>User</th><th>Warehouse</th><th>Exec (s)</th><th>GB scanned</th><th>Credits</th><th>Flags</th><th>Query text</th></tr></thead>
<tbody>{''.join(top_rows)}</tbody></table>
<p class="note">Generated by snowflake-cost-guard. Flag thresholds and the credit model are documented in the README.</p>
</body></html>
"""


def write_report(analysis: Analysis, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(analysis), encoding="utf-8")
    return path
