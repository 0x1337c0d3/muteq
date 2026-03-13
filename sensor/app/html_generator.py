import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from .db import query_events, query_readings

TIMEFRAME_HOURS: Dict[str, int] = {"1h": 1, "1d": 24, "1w": 168, "1m": 720}
TIMEFRAME_LIMIT: Dict[str, int] = {"1h": 1800, "1d": 1440, "1w": 1008, "1m": 1440}
DOWNSAMPLE_TARGET = 600
MINIMUM_NOISE_LEVEL = 80.0


def generate_html(
    db_path: str,
    device_name: str,
    location: str,
    environment_profile: str,
    generated_at: datetime,
) -> str:
    """Query SQLite for all timeframes and return a complete self-contained HTML string."""
    all_data: Dict[str, Any] = {}
    for tf in TIMEFRAME_HOURS:
        labels, values = _query_timeframe(db_path, tf)
        events = _query_events_for_tf(db_path, tf)
        all_data[tf] = {"labels": labels, "values": values, "events": events}

    return _render_html(
        device_name=device_name,
        location=location,
        environment_profile=environment_profile,
        generated_at=generated_at,
        all_data=all_data,
    )


def _query_timeframe(db_path: str, tf: str) -> Tuple[List[str], List[float]]:
    hours = TIMEFRAME_HOURS[tf]
    limit = TIMEFRAME_LIMIT[tf]
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = query_readings(db_path, since, limit)
    rows = _downsample(rows, DOWNSAMPLE_TARGET)
    labels: List[str] = []
    values: List[float] = []
    for r in rows:
        labels.append(r["timestamp"])  # raw ISO string — parsed by Chart.js time axis
        values.append(r["noise_value"])
    return labels, values


def _query_events_for_tf(db_path: str, tf: str) -> List[Dict[str, Any]]:
    hours = TIMEFRAME_HOURS[tf]
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = query_events(db_path, since, 500)
    result = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(r["timestamp"])
            ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts_str = r["timestamp"]
        result.append(
            {
                "timestamp": ts_str,
                "noise_value": r["noise_value"],
                "peak_value": r["peak_value"],
            }
        )
    return result


def _downsample(rows: List[Any], target: int) -> List[Any]:
    if len(rows) <= target:
        return rows
    step = len(rows) // target
    return rows[::step]


def _level_class(value: float | None) -> str:
    if value is None:
        return ""
    if value >= 90:
        return "alert"
    if value >= 80:
        return "warn"
    return "ok"


def _render_html(
    device_name: str,
    location: str,
    environment_profile: str,
    generated_at: datetime,
    all_data: Dict[str, Any],
) -> str:
    # Derive KPI values from the 1h window
    values_1h = all_data["1h"]["values"]
    events_1h = all_data["1h"]["events"]
    latest = values_1h[-1] if values_1h else None
    session_peak = max(values_1h) if values_1h else None
    event_count_1h = len(events_1h)

    level_class = _level_class(latest)
    latest_str = f"{latest:.1f}" if latest is not None else "—"
    peak_str = f"{session_peak:.1f}" if session_peak is not None else "—"
    generated_str = generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")

    # Initial event table rows (1h)
    event_rows_html = _build_event_rows(events_1h)

    # Escape device name for HTML
    safe_name = device_name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_location = (
        (location or "No location set")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    safe_profile = (
        (environment_profile or "—").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )

    # Embed all timeframe data as a JS constant
    all_data_json = json.dumps(all_data)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>{safe_name} — MUTEq</title>
{_common_styles()}
</head>
<body>
<header>
  <h1>MUTEq Dashboard</h1>
  <p>{safe_name}</p>
</header>
<main>
  <div style="margin-bottom:1.5rem; display:flex; align-items:center; gap:1rem; flex-wrap:wrap;">
    <div>
      <div style="font-size:1.25rem; font-weight:700;">{safe_name}</div>
      <div style="color:#64748b; font-size:0.8rem; margin-top:0.25rem;">
        {safe_location} &nbsp;|&nbsp;
        Profile: {safe_profile} &nbsp;|&nbsp;
        Last updated: <span id="last-updated">{generated_str}</span>
      </div>
    </div>
  </div>

  <div class="cards">
    <div class="card">
      <div class="label">Latest SPL</div>
      <div class="value {level_class}" id="latest-value">{latest_str}</div>
      <div class="unit">dB</div>
    </div>
    <div class="card">
      <div class="label">Peak (1 h)</div>
      <div class="value" id="session-peak">{peak_str}</div>
      <div class="unit">dB</div>
    </div>
    <div class="card">
      <div class="label">Threshold events (1 h)</div>
      <div class="value" id="event-count">{event_count_1h}</div>
    </div>
  </div>

  <div class="chart-wrap">
    <div class="chart-wrap-header">
      <h2 id="chart-title">SPL — last 1 h</h2>
      <div style="display:flex;align-items:center;gap:0.75rem;flex-wrap:wrap;">
        <div class="tf-btns">
          <button class="tf-btn active" data-tf="1h">1h</button>
          <button class="tf-btn" data-tf="1d">1d</button>
          <button class="tf-btn" data-tf="1w">1w</button>
          <button class="tf-btn" data-tf="1m">1m</button>
        </div>
        <div class="chart-nav">
          <button class="nav-btn" id="btn-pan-back" title="Pan backward">&#9664;</button>
          <button class="nav-btn" id="btn-pan-fwd" title="Pan forward">&#9654;</button>
          <button class="nav-btn" id="btn-reset-zoom" title="Jump to latest">Now</button>
        </div>
      </div>
    </div>
    <canvas id="spl-chart" height="100"></canvas>
    <div style="margin-top:0.5rem;font-size:0.7rem;color:#475569;text-align:center;">
      Scroll to zoom &nbsp;·&nbsp; Drag to pan &nbsp;·&nbsp; &#9664; &#9654; step by half-window &nbsp;·&nbsp; Now resets view
    </div>
  </div>

  <h2 style="font-size:1rem; color:#94a3b8; margin-bottom:0.75rem;" id="events-title">Threshold breach events (last 1 h)</h2>
  <table>
    <thead><tr><th>Time (UTC)</th><th>Noise (dB)</th><th>Peak (dB)</th></tr></thead>
    <tbody id="events-tbody">
      {event_rows_html if event_rows_html else '<tr><td colspan="3">No events recorded yet.</td></tr>'}
    </tbody>
  </table>
</main>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/hammerjs@2/hammer.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2/dist/chartjs-plugin-zoom.min.js"></script>
<script>
(function() {{
  const ALL_DATA = {all_data_json};
  const TF_LABELS = {{ '1h': 'last 1 h', '1d': 'last 1 d', '1w': 'last 1 w', '1m': 'last 1 m' }};
  const TF_TIME_UNIT = {{ '1h': 'minute', '1d': 'hour', '1w': 'day', '1m': 'day' }};
  let activeTf = '1h';
  let chart = null;

  // ── Chart ──────────────────────────────────────────────────────────────────
  function buildChart(tf) {{
    const d = ALL_DATA[tf];
    const chartData = d.labels.map((ts, i) => ({{ x: ts, y: d.values[i] }}));
    // Compute data min/max for pan limits
    const tMin = d.labels.length ? new Date(d.labels[0]).getTime() : undefined;
    const tMax = d.labels.length ? new Date(d.labels[d.labels.length - 1]).getTime() : undefined;

    if (chart) {{ chart.destroy(); }}
    const ctx = document.getElementById('spl-chart').getContext('2d');
    chart = new Chart(ctx, {{
      type: 'line',
      data: {{
        datasets: [{{
          label: 'SPL (dB)',
          data: chartData,
          borderColor: '#38bdf8',
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          fill: false,
        }}]
      }},
      options: {{
        animation: false,
        responsive: true,
        scales: {{
          x: {{
            type: 'time',
            time: {{
              unit: TF_TIME_UNIT[tf],
              displayFormats: {{ minute: 'HH:mm', hour: 'MM-dd HH:mm', day: 'MMM dd', week: 'MMM dd' }},
              tooltipFormat: 'yyyy-MM-dd HH:mm:ss',
            }},
            ticks: {{ color: '#64748b', maxTicksLimit: 10 }},
            grid: {{ color: '#1e293b' }},
          }},
          y: {{ ticks: {{ color: '#64748b' }}, grid: {{ color: '#334155' }}, min: 40 }}
        }},
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{ mode: 'index', intersect: false }},
          zoom: {{
            limits: {{
              x: {{ min: tMin, max: tMax, minRange: 60 * 1000 }},
            }},
            pan: {{
              enabled: true,
              mode: 'x',
            }},
            zoom: {{
              wheel: {{ enabled: true, speed: 0.1 }},
              pinch: {{ enabled: true }},
              mode: 'x',
            }},
          }},
        }},
      }},
    }});
  }}

  buildChart('1h');

  // ── Pan buttons ────────────────────────────────────────────────────────────
  function stepView(direction) {{
    if (!chart) return;
    const xScale = chart.scales.x;
    const span = xScale.max - xScale.min;
    chart.zoomScale('x', {{ min: xScale.min + direction * span * 0.5, max: xScale.max + direction * span * 0.5 }}, 'default');
  }}

  document.getElementById('btn-pan-back').addEventListener('click', () => stepView(-1));
  document.getElementById('btn-pan-fwd').addEventListener('click', () => stepView(1));
  document.getElementById('btn-reset-zoom').addEventListener('click', () => chart && chart.resetZoom());

  // ── Timeframe switching ────────────────────────────────────────────────────
  function loadTimeframe(tf) {{
    activeTf = tf;
    const d = ALL_DATA[tf];

    document.querySelectorAll('.tf-btn').forEach(b => {{
      b.classList.toggle('active', b.dataset.tf === tf);
    }});
    document.getElementById('chart-title').textContent = 'SPL — ' + TF_LABELS[tf];
    document.getElementById('events-title').textContent = 'Threshold breach events (' + TF_LABELS[tf] + ')';

    buildChart(tf);

    document.getElementById('event-count').textContent = d.events.length;
    const tbody = document.getElementById('events-tbody');
    if (d.events.length === 0) {{
      tbody.innerHTML = '<tr><td colspan="3">No events in this period.</td></tr>';
    }} else {{
      tbody.innerHTML = d.events.map(e =>
        '<tr><td>' + e.timestamp + '</td>' +
        '<td>' + e.noise_value.toFixed(1) + '</td>' +
        '<td>' + (e.peak_value != null ? e.peak_value.toFixed(1) : '—') + '</td></tr>'
      ).join('');
    }}
  }}

  document.querySelectorAll('.tf-btn').forEach(btn => {{
    btn.addEventListener('click', () => loadTimeframe(btn.dataset.tf));
  }});
}})();
</script>
</body>
</html>"""


def _build_event_rows(events: List[Dict[str, Any]]) -> str:
    rows = []
    for e in events:
        peak = f"{e['peak_value']:.1f}" if e["peak_value"] is not None else "—"
        rows.append(
            f"<tr><td>{e['timestamp']}</td><td>{e['noise_value']:.1f}</td><td>{peak}</td></tr>"
        )
    return "".join(rows)


def _common_styles() -> str:
    return """<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  header { background: #1e293b; padding: 1.5rem 2rem; border-bottom: 1px solid #334155; }
  header h1 { font-size: 1.5rem; font-weight: 700; color: #38bdf8; }
  header p { color: #94a3b8; font-size: 0.9rem; margin-top: 0.25rem; }
  main { padding: 2rem; max-width: 1200px; margin: 0 auto; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 0.75rem; padding: 1.25rem; }
  .card .label { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; margin-bottom: 0.5rem; }
  .card .value { font-size: 2rem; font-weight: 700; color: #f1f5f9; }
  .card .value.ok { color: #4ade80; }
  .card .value.warn { color: #facc15; }
  .card .value.alert { color: #f87171; }
  .card .unit { font-size: 0.9rem; color: #94a3b8; }
  .chart-wrap { background: #1e293b; border: 1px solid #334155; border-radius: 0.75rem; padding: 1.5rem; margin-bottom: 2rem; }
  .chart-wrap-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1rem; flex-wrap: wrap; gap: 0.5rem; }
  .chart-wrap-header h2 { font-size: 1rem; color: #94a3b8; }
  .tf-btns { display: flex; gap: 0.35rem; }
  .tf-btn { background: #0f172a; border: 1px solid #334155; color: #94a3b8; border-radius: 0.4rem; padding: 0.25rem 0.65rem; font-size: 0.75rem; cursor: pointer; transition: background 0.15s, color 0.15s; }
  .tf-btn:hover { background: #1e3a5f; color: #e2e8f0; }
  .tf-btn.active { background: #0369a1; border-color: #38bdf8; color: #f0f9ff; }
  .chart-nav { display: flex; gap: 0.25rem; }
  .nav-btn { background: #0f172a; border: 1px solid #334155; color: #94a3b8; border-radius: 0.4rem; padding: 0.25rem 0.6rem; font-size: 0.75rem; cursor: pointer; transition: background 0.15s, color 0.15s; }
  .nav-btn:hover { background: #1e3a5f; color: #e2e8f0; }
  table { width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 0.75rem; overflow: hidden; }
  th, td { padding: 0.75rem 1rem; text-align: left; border-bottom: 1px solid #334155; font-size: 0.875rem; }
  th { background: #0f172a; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.75rem; }
  tr:last-child td { border-bottom: none; }
  a { color: #38bdf8; text-decoration: none; }
  a:hover { text-decoration: underline; }
</style>"""
