import json
from datetime import UTC, datetime, timedelta
from typing import Any

from .db import query_daily_stats, query_events, query_hourly_event_counts, query_readings

TIMEFRAMES: dict[str, timedelta] = {
    "10m": timedelta(minutes=10),
    "1h": timedelta(hours=1),
    "1d": timedelta(hours=24),
    "1w": timedelta(hours=168),
    "1m": timedelta(hours=720),
}
TIMEFRAME_LIMIT: dict[str, int] = {
    "10m": 600,
    "1h": 1800,
    "1d": 1440,
    "1w": 1008,
    "1m": 1440,
}
DOWNSAMPLE_TARGET = 600
MINIMUM_NOISE_LEVEL = 70.0


def generate_html(
    db_path: str,
    device_name: str,
    location: str,
    environment_profile: str,
    generated_at: datetime,
) -> str:
    """Query SQLite for all timeframes and return a complete self-contained HTML string."""
    all_data: dict[str, Any] = {}
    for tf in TIMEFRAMES:
        labels, values = _query_timeframe(db_path, tf)
        events = _query_events_for_tf(db_path, tf)
        all_data[tf] = {
            "labels": labels,
            "values": values,
            "events": events,
            "percentiles": _compute_percentiles(values),
            "histogram": _compute_histogram(values),
        }

    heatmap_counts = _query_heatmap(db_path)
    daily_stats = _query_daily(db_path)

    return _render_html(
        device_name=device_name,
        location=location,
        environment_profile=environment_profile,
        generated_at=generated_at,
        all_data=all_data,
        heatmap_counts=heatmap_counts,
        daily_stats=daily_stats,
    )


def _query_timeframe(db_path: str, tf: str) -> tuple[list[str], list[float]]:
    since = (datetime.now(UTC) - TIMEFRAMES[tf]).isoformat()
    rows = query_readings(db_path, since, TIMEFRAME_LIMIT[tf])
    rows = _downsample(rows, DOWNSAMPLE_TARGET)
    labels: list[str] = []
    values: list[float] = []
    for r in rows:
        labels.append(r["timestamp"])
        values.append(r["noise_value"])
    return labels, values


def _query_events_for_tf(db_path: str, tf: str) -> list[dict[str, Any]]:
    since = (datetime.now(UTC) - TIMEFRAMES[tf]).isoformat()
    rows = query_events(db_path, since, 500)
    result = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(r["timestamp"])
            ts_str = ts.astimezone().strftime("%Y-%m-%d %H:%M:%S")
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


def _downsample(rows: list[Any], target: int) -> list[Any]:
    if len(rows) <= target:
        return rows
    step = len(rows) // target
    return rows[::step]


def _compute_percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p90": None, "p99": None}
    s = sorted(values)

    def pct(p: int) -> float:
        return round(s[min(int(len(s) * p / 100), len(s) - 1)], 1)

    return {"p50": pct(50), "p90": pct(90), "p99": pct(99)}


def _compute_histogram(values: list[float]) -> dict[str, Any]:
    """5 dB bins from 40 to 100, with a >=100 overflow bucket."""
    bin_starts = list(range(40, 100, 5))
    labels = [f"{b}-{b + 5}" for b in bin_starts] + ["\u2265100"]
    counts = [0] * len(labels)
    for v in values:
        if v < 40:
            continue
        elif v >= 100:
            counts[-1] += 1
        else:
            idx = int((v - 40) / 5)
            counts[min(idx, len(bin_starts) - 1)] += 1
    return {"labels": labels, "counts": counts}


def _query_heatmap(db_path: str) -> list[int]:
    """Return list of 24 event counts (index=hour 0-23) over the past 7 days."""
    since = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    rows = query_hourly_event_counts(db_path, since)
    counts = [0] * 24
    for r in rows:
        counts[r["hour"]] = r["count"]
    return counts


def _query_daily(db_path: str) -> list[dict[str, Any]]:
    since = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    return query_daily_stats(db_path, since)


def _level_class(value: float | None) -> str:
    if value is None:
        return ""
    if value >= 90:
        return "alert"
    if value >= 70:
        return "warn"
    return "ok"


def _render_html(
    device_name: str,
    location: str,
    environment_profile: str,
    generated_at: datetime,
    all_data: dict[str, Any],
    heatmap_counts: list[int],
    daily_stats: list[dict[str, Any]],
) -> str:
    values_1h = all_data["1h"]["values"]
    events_1h = all_data["1h"]["events"]
    latest = values_1h[-1] if values_1h else None
    session_peak = max(values_1h) if values_1h else None
    event_count_1h = len(events_1h)
    p90_1h = all_data["1h"]["percentiles"]["p90"]

    level_class = _level_class(latest)
    latest_str = f"{latest:.1f}" if latest is not None else "\u2014"
    peak_str = f"{session_peak:.1f}" if session_peak is not None else "\u2014"
    p90_str = f"{p90_1h:.1f}" if p90_1h is not None else "\u2014"
    generated_str = generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")

    safe_name = device_name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_location = (location or "No location set").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_profile = (environment_profile or "\u2014").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    all_data_json = json.dumps(all_data)
    heatmap_json = json.dumps(heatmap_counts)

    daily_rows_html = _build_daily_rows(daily_stats)
    event_rows_html = _build_event_rows(events_1h)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>{safe_name} \u2014 MUTEq</title>
{_common_styles()}
</head>
<body>
<header>
  <div class="header-inner">
    <div>
      <h1>MUTEq Dashboard</h1>
      <p class="header-sub">{safe_name}</p>
    </div>
    <div class="header-meta">
      <span>\U0001f4cd {safe_location}</span>
      <span class="sep">\u00b7</span>
      <span>{safe_profile}</span>
      <span class="sep">\u00b7</span>
      <span>Updated: {generated_str}</span>
    </div>
  </div>
</header>

<main>
  <!-- KPI cards -->
  <div class="cards">
    <div class="card">
      <div class="label">Latest SPL</div>
      <div class="value {level_class}" id="latest-value">{latest_str}</div>
      <div class="unit">dB</div>
    </div>
    <div class="card">
      <div class="label">Peak (1h)</div>
      <div class="value" id="session-peak">{peak_str}</div>
      <div class="unit">dB</div>
    </div>
    <div class="card">
      <div class="label">Events (1h)</div>
      <div class="value" id="event-count">{event_count_1h}</div>
      <div class="unit">\u226570 dB</div>
    </div>
    <div class="card">
      <div class="label">p90 (1h)</div>
      <div class="value" id="p90-value">{p90_str}</div>
      <div class="unit">dB</div>
    </div>
  </div>

  <!-- Main time series -->
  <div class="panel">
    <div class="panel-header">
      <h2 id="chart-title">SPL \u2014 last 10 min</h2>
      <div class="tf-btns">
        <button class="tf-btn active" data-tf="10m">10m</button>
        <button class="tf-btn" data-tf="1h">1h</button>
        <button class="tf-btn" data-tf="1d">1d</button>
        <button class="tf-btn" data-tf="1w">1w</button>
        <button class="tf-btn" data-tf="1m">1m</button>
      </div>
    </div>
    <div id="chart-main" style="height:280px"></div>
  </div>

  <!-- Histogram + Events by hour -->
  <div class="two-col">
    <div class="panel">
      <div class="panel-header"><h2>Noise Distribution</h2></div>
      <div id="chart-hist" style="height:220px"></div>
    </div>
    <div class="panel">
      <div class="panel-header"><h2>Events by Hour (7d)</h2></div>
      <div id="chart-heatmap" style="height:220px"></div>
    </div>
  </div>

  <!-- Daily stats table -->
  <div class="panel">
    <div class="panel-header"><h2>Daily Statistics (last 30 days)</h2></div>
    <table>
      <thead><tr><th>Date</th><th>Avg (dB)</th><th>Peak (dB)</th><th>Events (\u226570 dB)</th></tr></thead>
      <tbody id="daily-tbody">
        {daily_rows_html if daily_rows_html else '<tr><td colspan="4">No data yet.</td></tr>'}
      </tbody>
    </table>
  </div>

  <!-- Threshold events table -->
  <div class="panel">
    <div class="panel-header">
      <h2 id="events-title">Threshold Breach Events \u2014 last 10 min</h2>
    </div>
    <table>
      <thead><tr><th>Time (local)</th><th>Noise (dB)</th><th>Peak (dB)</th></tr></thead>
      <tbody id="events-tbody">
        {event_rows_html if event_rows_html else '<tr><td colspan="3">No events recorded yet.</td></tr>'}
      </tbody>
    </table>
  </div>
</main>

<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<script>
(function() {{
  const ALL_DATA = {all_data_json};
  const HEATMAP = {heatmap_json};
  const TF_LABELS = {{'10m':'last 10 min','1h':'last 1h','1d':'last 1d','1w':'last 1w','1m':'last 1m'}};

  // ── Colour helpers ────────────────────────────────────────────────────────
  function splColor(v) {{
    if (v >= 90) return '#f87171';
    if (v >= 70) return '#facc15';
    return '#4ade80';
  }}

  // ── Main time-series chart ────────────────────────────────────────────────
  const mainChart = echarts.init(document.getElementById('chart-main'), 'dark');

  function buildMainOption(tf) {{
    const d = ALL_DATA[tf];
    const pts = d.labels.map((ts, i) => [ts, d.values[i]]);
    const pct = d.percentiles;
    const markLines = [];
    if (pct.p50 != null) markLines.push({{ yAxis: pct.p50, name: 'p50', lineStyle: {{ color: '#94a3b8', type: 'dashed', width: 1 }}, label: {{ formatter: 'p50 {{c}}', color: '#94a3b8', fontSize: 11 }} }});
    if (pct.p90 != null) markLines.push({{ yAxis: pct.p90, name: 'p90', lineStyle: {{ color: '#facc15', type: 'dashed', width: 1 }}, label: {{ formatter: 'p90 {{c}}', color: '#facc15', fontSize: 11 }} }});
    if (pct.p99 != null) markLines.push({{ yAxis: pct.p99, name: 'p99', lineStyle: {{ color: '#f87171', type: 'dashed', width: 1 }}, label: {{ formatter: 'p99 {{c}}', color: '#f87171', fontSize: 11 }} }});

    return {{
      backgroundColor: 'transparent',
      grid: {{ top: 20, right: 60, bottom: 80, left: 55 }},
      xAxis: {{
        type: 'time',
        axisLabel: {{ color: '#64748b', fontSize: 11 }},
        axisLine: {{ lineStyle: {{ color: '#334155' }} }},
        splitLine: {{ lineStyle: {{ color: '#1e293b' }} }},
      }},
      yAxis: {{
        type: 'value',
        name: 'dB',
        nameTextStyle: {{ color: '#64748b', fontSize: 11 }},
        min: 40,
        axisLabel: {{ color: '#64748b', fontSize: 11 }},
        splitLine: {{ lineStyle: {{ color: '#334155' }} }},
      }},
      dataZoom: [
        {{ type: 'inside', xAxisIndex: 0, filterMode: 'filter' }},
        {{ type: 'slider', xAxisIndex: 0, bottom: 8, height: 22,
           borderColor: '#334155', fillerColor: 'rgba(56,189,248,0.15)',
           handleStyle: {{ color: '#38bdf8' }}, textStyle: {{ color: '#64748b', fontSize: 10 }} }},
      ],
      series: [{{
        type: 'line',
        data: pts,
        smooth: 0.3,
        symbol: 'none',
        lineStyle: {{ color: '#38bdf8', width: 2 }},
        areaStyle: {{ color: {{ type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{{ offset: 0, color: 'rgba(56,189,248,0.25)' }}, {{ offset: 1, color: 'rgba(56,189,248,0)' }}] }} }},
        markLine: {{
          silent: true,
          symbol: 'none',
          data: markLines,
        }},
      }}],
      tooltip: {{
        trigger: 'axis',
        axisPointer: {{ type: 'cross', label: {{ backgroundColor: '#1e293b' }} }},
        backgroundColor: '#1e293b',
        borderColor: '#334155',
        textStyle: {{ color: '#e2e8f0', fontSize: 12 }},
        formatter: function(params) {{
          if (!params.length) return '';
          const ts = new Date(params[0].value[0]);
          const pad = n => String(n).padStart(2,'0');
          const dstr = ts.getFullYear() + '-' + pad(ts.getMonth()+1) + '-' + pad(ts.getDate()) +
            ' ' + pad(ts.getHours()) + ':' + pad(ts.getMinutes()) + ':' + pad(ts.getSeconds());
          const v = params[0].value[1];
          return dstr + '<br/><b>' + v.toFixed(1) + ' dB</b>';
        }},
      }},
    }};
  }}

  mainChart.setOption(buildMainOption('10m'));

  // ── Histogram ─────────────────────────────────────────────────────────────
  const histChart = echarts.init(document.getElementById('chart-hist'), 'dark');

  function buildHistOption(tf) {{
    const h = ALL_DATA[tf].histogram;
    const colors = h.labels.map((_, i) => {{
      const frac = i / (h.labels.length - 1);
      if (frac < 0.5) return '#4ade80';
      if (frac < 0.75) return '#facc15';
      return '#f87171';
    }});
    return {{
      backgroundColor: 'transparent',
      grid: {{ top: 10, right: 10, bottom: 55, left: 45 }},
      xAxis: {{
        type: 'category',
        data: h.labels,
        axisLabel: {{ color: '#64748b', fontSize: 10, rotate: 45 }},
        axisLine: {{ lineStyle: {{ color: '#334155' }} }},
      }},
      yAxis: {{
        type: 'value',
        name: 'count',
        nameTextStyle: {{ color: '#64748b', fontSize: 11 }},
        axisLabel: {{ color: '#64748b', fontSize: 11 }},
        splitLine: {{ lineStyle: {{ color: '#334155' }} }},
      }},
      series: [{{
        type: 'bar',
        data: h.counts.map((v, i) => ({{ value: v, itemStyle: {{ color: colors[i] }} }})),
        barMaxWidth: 30,
      }}],
      tooltip: {{
        trigger: 'axis',
        backgroundColor: '#1e293b',
        borderColor: '#334155',
        textStyle: {{ color: '#e2e8f0', fontSize: 12 }},
      }},
    }};
  }}

  histChart.setOption(buildHistOption('10m'));

  // ── Events-by-hour chart ──────────────────────────────────────────────────
  const hmChart = echarts.init(document.getElementById('chart-heatmap'), 'dark');
  const hmOption = {{
    backgroundColor: 'transparent',
    grid: {{ top: 10, right: 10, bottom: 40, left: 45 }},
    xAxis: {{
      type: 'category',
      data: Array.from({{length: 24}}, (_, i) => String(i).padStart(2,'0') + ':00'),
      axisLabel: {{ color: '#64748b', fontSize: 10, rotate: 45 }},
      axisLine: {{ lineStyle: {{ color: '#334155' }} }},
    }},
    yAxis: {{
      type: 'value',
      name: 'events',
      nameTextStyle: {{ color: '#64748b', fontSize: 11 }},
      axisLabel: {{ color: '#64748b', fontSize: 11 }},
      splitLine: {{ lineStyle: {{ color: '#334155' }} }},
    }},
    series: [{{
      type: 'bar',
      data: HEATMAP.map(v => ({{ value: v, itemStyle: {{ color: v === 0 ? '#1e3a5f' : '#f87171' }} }})),
      barMaxWidth: 22,
    }}],
    tooltip: {{
      trigger: 'axis',
      backgroundColor: '#1e293b',
      borderColor: '#334155',
      textStyle: {{ color: '#e2e8f0', fontSize: 12 }},
    }},
  }};
  hmChart.setOption(hmOption);

  // ── Timeframe switching ───────────────────────────────────────────────────
  const TF_EVENT_LABELS = {{'10m':'last 10 min','1h':'last 1h','1d':'last 1d','1w':'last 1w','1m':'last 1m'}};

  function loadTimeframe(tf) {{
    document.querySelectorAll('.tf-btn').forEach(b => b.classList.toggle('active', b.dataset.tf === tf));
    document.getElementById('chart-title').textContent = 'SPL \u2014 ' + TF_LABELS[tf];
    document.getElementById('events-title').textContent = 'Threshold Breach Events \u2014 ' + TF_EVENT_LABELS[tf];

    mainChart.setOption(buildMainOption(tf), {{ notMerge: true }});
    histChart.setOption(buildHistOption(tf), {{ notMerge: true }});

    const d = ALL_DATA[tf];
    document.getElementById('event-count').textContent = d.events.length;

    const tbody = document.getElementById('events-tbody');
    if (d.events.length === 0) {{
      tbody.innerHTML = '<tr><td colspan="3">No events in this period.</td></tr>';
    }} else {{
      tbody.innerHTML = d.events.map(e =>
        '<tr><td>' + e.timestamp + '</td>' +
        '<td>' + e.noise_value.toFixed(1) + '</td>' +
        '<td>' + (e.peak_value != null ? e.peak_value.toFixed(1) : '\u2014') + '</td></tr>'
      ).join('');
    }}
  }}

  document.querySelectorAll('.tf-btn').forEach(btn => {{
    btn.addEventListener('click', () => loadTimeframe(btn.dataset.tf));
  }});

  // ── Responsive resize ─────────────────────────────────────────────────────
  window.addEventListener('resize', () => {{
    mainChart.resize();
    histChart.resize();
    hmChart.resize();
  }});
}})();
</script>
</body>
</html>"""


def _build_event_rows(events: list[dict[str, Any]]) -> str:
    rows = []
    for e in events:
        peak = f"{e['peak_value']:.1f}" if e["peak_value"] is not None else "\u2014"
        rows.append(
            f"<tr><td>{e['timestamp']}</td><td>{e['noise_value']:.1f}</td><td>{peak}</td></tr>"
        )
    return "".join(rows)


def _build_daily_rows(stats: list[dict[str, Any]]) -> str:
    rows = []
    for s in stats:
        avg = f"{s['avg_noise']:.1f}" if s["avg_noise"] is not None else "\u2014"
        peak = f"{s['peak_noise']:.1f}" if s["peak_noise"] is not None else "\u2014"
        rows.append(
            f"<tr><td>{s['day']}</td><td>{avg}</td><td>{peak}</td><td>{s['event_count']}</td></tr>"
        )
    return "".join(rows)


def _common_styles() -> str:
    return """<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  header { background: #1e293b; padding: 1.25rem 2rem; border-bottom: 1px solid #334155; }
  .header-inner { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 0.75rem; }
  header h1 { font-size: 1.4rem; font-weight: 700; color: #38bdf8; }
  header .header-sub { color: #94a3b8; font-size: 0.85rem; margin-top: 0.2rem; }
  .header-meta { color: #64748b; font-size: 0.78rem; display: flex; gap: 0.4rem; flex-wrap: wrap; align-items: center; }
  .sep { color: #334155; }
  main { padding: 1.5rem 2rem; max-width: 1300px; margin: 0 auto; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; margin-bottom: 1.25rem; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 0.75rem; padding: 1.25rem; }
  .card .label { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; color: #64748b; margin-bottom: 0.4rem; }
  .card .value { font-size: 2rem; font-weight: 700; color: #f1f5f9; }
  .card .value.ok { color: #4ade80; }
  .card .value.warn { color: #facc15; }
  .card .value.alert { color: #f87171; }
  .card .unit { font-size: 0.82rem; color: #94a3b8; margin-top: 0.2rem; }
  .panel { background: #1e293b; border: 1px solid #334155; border-radius: 0.75rem; padding: 1.25rem; margin-bottom: 1.25rem; }
  .panel-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.75rem; flex-wrap: wrap; gap: 0.5rem; }
  .panel-header h2 { font-size: 0.9rem; color: #94a3b8; font-weight: 600; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 1.25rem; margin-bottom: 1.25rem; }
  @media (max-width: 700px) { .two-col { grid-template-columns: 1fr; } .two-col .panel { margin-bottom: 0; } }
  .tf-btns { display: flex; gap: 0.3rem; }
  .tf-btn { background: #0f172a; border: 1px solid #334155; color: #94a3b8; border-radius: 0.4rem; padding: 0.2rem 0.55rem; font-size: 0.72rem; cursor: pointer; transition: background 0.15s, color 0.15s; }
  .tf-btn:hover { background: #1e3a5f; color: #e2e8f0; }
  .tf-btn.active { background: #0369a1; border-color: #38bdf8; color: #f0f9ff; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 0.6rem 1rem; text-align: left; border-bottom: 1px solid #334155; font-size: 0.85rem; }
  th { color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.72rem; }
  tr:last-child td { border-bottom: none; }
</style>"""
