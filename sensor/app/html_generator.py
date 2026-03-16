"""HTML shell generator.

Produces a self-contained dashboard page that:
  1. Fetches live chart data from the MUTEq Lambda query API.
  2. Renders the main SPL time-series with TradingView lightweight-charts.
  3. Renders histogram / events-by-hour with pure CSS bar charts (no ECharts).
  4. Auto-polls the API every 60 s.

The Pi uploads this shell to S3 once at startup (or on config change). All
actual data comes from the Lambda API — nothing is embedded at build time.
"""

from datetime import datetime


def generate_html(
    device_name: str,
    location: str,
    environment_profile: str,
    api_endpoint: str,
    device_id: str,
) -> str:
    """Return a complete self-contained HTML string for the dashboard shell."""
    safe_name = _esc(device_name)
    safe_location = _esc(location or "No location set")
    safe_profile = _esc(environment_profile or "—")
    safe_api = _esc(api_endpoint or "")
    safe_device_id = _esc(device_id or "")
    generated_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_name} — MUTEq</title>
{_styles()}
</head>
<body>
<header>
  <div class="header-inner">
    <div>
      <h1>MUTEq Dashboard</h1>
      <p class="header-sub" id="device-name">{safe_name}</p>
    </div>
    <div class="header-meta">
      <span>📍 <span id="hdr-location">{safe_location}</span></span>
      <span class="sep">·</span>
      <span id="hdr-profile">{safe_profile}</span>
      <span class="sep">·</span>
      <span>Updated: <span id="hdr-updated">—</span></span>
    </div>
  </div>
</header>

<main>
  <div id="status-bar" class="status-bar hidden"></div>

  <!-- KPI cards -->
  <div class="cards">
    <div class="card">
      <div class="label">Latest SPL</div>
      <div class="value" id="kpi-latest">—</div>
      <div class="unit">dB</div>
    </div>
    <div class="card">
      <div class="label">Peak (current view)</div>
      <div class="value" id="kpi-peak">—</div>
      <div class="unit">dB</div>
    </div>
    <div class="card">
      <div class="label">Events (≥70 dB)</div>
      <div class="value" id="kpi-events">—</div>
      <div class="unit">current view</div>
    </div>
    <div class="card">
      <div class="label">p90</div>
      <div class="value" id="kpi-p90">—</div>
      <div class="unit">dB</div>
    </div>
  </div>

  <!-- Main time-series chart -->
  <div class="panel">
    <div class="panel-header">
      <h2 id="chart-title">SPL — last 10 min</h2>
      <div class="tf-btns">
        <button class="tf-btn active" data-tf="10m">10m</button>
        <button class="tf-btn" data-tf="1h">1h</button>
        <button class="tf-btn" data-tf="1d">1d</button>
        <button class="tf-btn" data-tf="1w">1w</button>
        <button class="tf-btn" data-tf="1m">1m</button>
      </div>
    </div>
    <div id="chart-main" style="position:relative;height:300px;">
      <div id="chart-loading" class="chart-placeholder">Loading…</div>
    </div>
  </div>

  <!-- Histogram + Events by hour -->
  <div class="two-col">
    <div class="panel">
      <div class="panel-header"><h2>Noise Distribution</h2></div>
      <div id="chart-hist" class="bar-chart-wrap" style="height:200px"></div>
    </div>
    <div class="panel">
      <div class="panel-header"><h2>Events by Hour — UTC (7d)</h2></div>
      <div id="chart-heatmap" class="bar-chart-wrap" style="height:200px"></div>
    </div>
  </div>

  <!-- Daily stats table -->
  <div class="panel">
    <div class="panel-header"><h2>Daily Statistics (last 30 days)</h2></div>
    <table>
      <thead><tr><th>Date</th><th>Avg (dB)</th><th>Peak (dB)</th><th>Events (≥70 dB)</th></tr></thead>
      <tbody id="daily-tbody"><tr><td colspan="4">Loading…</td></tr></tbody>
    </table>
  </div>

  <!-- Threshold events table -->
  <div class="panel">
    <div class="panel-header">
      <h2 id="events-title">Threshold Breach Events — last 10 min</h2>
    </div>
    <table>
      <thead><tr><th>Time (UTC)</th><th>Noise (dB)</th><th>Peak (dB)</th></tr></thead>
      <tbody id="events-tbody"><tr><td colspan="3">Loading…</td></tr></tbody>
    </table>
  </div>
</main>

<!-- TradingView lightweight-charts v4 -->
<script src="https://unpkg.com/lightweight-charts@4/dist/lightweight-charts.standalone.production.js"></script>
<script>
(function () {{
  'use strict';

  // ── Config (embedded at generation time) ──────────────────────────────────
  const API_ENDPOINT = '{safe_api}';
  const DEVICE_ID    = '{safe_device_id}';
  const POLL_MS      = 60_000;

  const TF_LABELS = {{
    '10m': 'last 10 min', '1h': 'last 1h',
    '1d': 'last 1d', '1w': 'last 1w', '1m': 'last 1m',
  }};

  // ── State ──────────────────────────────────────────────────────────────────
  let allData   = null;
  let activeTf  = '10m';
  let lwChart   = null;
  let areaSeries = null;
  let priceLines = [];

  // ── Lightweight-charts init ───────────────────────────────────────────────
  function initChart() {{
    const container = document.getElementById('chart-main');
    lwChart = LightweightCharts.createChart(container, {{
      width:  container.clientWidth,
      height: 300,
      layout: {{
        background: {{ type: 'solid', color: '#0f172a' }},
        textColor: '#94a3b8',
      }},
      grid: {{
        vertLines: {{ color: '#1e293b' }},
        horzLines: {{ color: '#1e293b' }},
      }},
      crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
      rightPriceScale: {{
        borderColor: '#334155',
        scaleMargins: {{ top: 0.15, bottom: 0.1 }},
      }},
      timeScale: {{
        borderColor: '#334155',
        timeVisible: true,
        secondsVisible: true,
      }},
    }});

    areaSeries = lwChart.addAreaSeries({{
      topColor:    'rgba(56,189,248,0.25)',
      bottomColor: 'rgba(56,189,248,0)',
      lineColor:   '#38bdf8',
      lineWidth: 2,
      priceLineVisible: false,
      crosshairMarkerVisible: true,
      crosshairMarkerRadius: 4,
    }});

    window.addEventListener('resize', () => {{
      lwChart.applyOptions({{ width: container.clientWidth }});
    }});
  }}

  // ── Helpers ───────────────────────────────────────────────────────────────
  function splColor(v) {{
    if (v >= 90) return '#f87171';
    if (v >= 70) return '#facc15';
    return '#4ade80';
  }}

  function fmtDec(v, d=1) {{
    return v != null ? v.toFixed(d) : '—';
  }}

  function setStatusBar(msg, isError) {{
    const bar = document.getElementById('status-bar');
    bar.textContent = msg;
    bar.className = 'status-bar ' + (isError ? 'error' : 'info');
  }}

  // ── Bar chart (histogram + heatmap) ──────────────────────────────────────
  function renderBarChart(containerId, labels, values, colorFn) {{
    const el = document.getElementById(containerId);
    const maxVal = Math.max(...values, 1);
    el.innerHTML = '<div class="bar-chart">' +
      values.map((v, i) => `
        <div class="bar-item" title="${{labels[i]}}: ${{v}}">
          <div class="bar" style="height:${{Math.round(v / maxVal * 100)}}%;background:${{colorFn(i, v)}}"></div>
          <div class="bar-lbl">${{labels[i]}}</div>
        </div>`).join('') +
      '</div>';
  }}

  function histColor(i, _v) {{
    const frac = i / 12;           // 13 bins total
    if (frac < 0.46) return '#4ade80';
    if (frac < 0.69) return '#facc15';
    return '#f87171';
  }}

  function heatColor(_i, v) {{
    return v === 0 ? '#1e3a5f' : '#f87171';
  }}

  // ── Render a loaded timeframe ─────────────────────────────────────────────
  function applyTimeframe(tf) {{
    if (!allData) return;
    const d = allData.timeframes[tf];

    // Update title
    document.getElementById('chart-title').textContent = 'SPL — ' + TF_LABELS[tf];
    document.getElementById('events-title').textContent =
      'Threshold Breach Events — ' + TF_LABELS[tf];
    document.querySelectorAll('.tf-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.tf === tf));

    // KPI cards
    const latest = d.latest;
    const latestEl = document.getElementById('kpi-latest');
    latestEl.textContent = fmtDec(latest);
    latestEl.className = 'value ' + (latest != null ? (latest >= 90 ? 'alert' : latest >= 70 ? 'warn' : 'ok') : '');
    document.getElementById('kpi-peak').textContent   = fmtDec(d.peak);
    document.getElementById('kpi-events').textContent = d.event_count ?? '—';
    document.getElementById('kpi-p90').textContent    = fmtDec(d.percentiles.p90);

    // Lightweight-charts: set data
    areaSeries.setData(d.readings);   // [{time: unix_s, value: float}]

    // Price lines for percentiles
    priceLines.forEach(pl => areaSeries.removePriceLine(pl));
    priceLines = [];
    const LS = LightweightCharts.LineStyle;
    [
      [d.percentiles.p50, '#94a3b8', 'p50'],
      [d.percentiles.p90, '#facc15', 'p90'],
      [d.percentiles.p99, '#f87171', 'p99'],
    ].forEach(([price, color, title]) => {{
      if (price != null) {{
        priceLines.push(areaSeries.createPriceLine({{
          price, color, lineWidth: 1, lineStyle: LS.Dashed,
          axisLabelVisible: true, title,
        }}));
      }}
    }});

    if (d.readings.length > 0) {{
      lwChart.timeScale().fitContent();
    }}

    // Histogram
    const h = d.histogram;
    renderBarChart('chart-hist', h.labels, h.counts, histColor);

    // Events table
    const events = d.events || [];
    const eTbody = document.getElementById('events-tbody');
    eTbody.innerHTML = events.length === 0
      ? '<tr><td colspan="3">No events in this period.</td></tr>'
      : events.map(e =>
          `<tr><td>${{e.timestamp}}</td>` +
          `<td style="color:${{splColor(e.noise_value)}}">${{fmtDec(e.noise_value)}}</td>` +
          `<td>${{e.peak_value != null ? fmtDec(e.peak_value) : '—'}}</td></tr>`
        ).join('');
  }}

  // ── Fetch data from Lambda API ────────────────────────────────────────────
  async function fetchData() {{
    if (!API_ENDPOINT) {{
      setStatusBar('api_endpoint not configured — set it in config_client.json', true);
      return;
    }}
    const url = `${{API_ENDPOINT}}/data?device_id=${{DEVICE_ID}}`;
    try {{
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${{resp.status}}`);
      const data = await resp.json();

      allData = data;

      // Update header metadata from API response
      if (data.device_name) document.getElementById('device-name').textContent = data.device_name;
      if (data.location)    document.getElementById('hdr-location').textContent = data.location;
      if (data.environment_profile) document.getElementById('hdr-profile').textContent = data.environment_profile;
      document.getElementById('hdr-updated').textContent =
        new Date().toISOString().replace('T', ' ').slice(0, 19) + ' UTC';

      // Remove loading placeholder
      const loading = document.getElementById('chart-loading');
      if (loading) loading.remove();

      // Heatmap (not timeframe-dependent)
      const hm = data.heatmap || new Array(24).fill(0);
      const hmLabels = Array.from({{length: 24}}, (_, i) => String(i).padStart(2, '0'));
      renderBarChart('chart-heatmap', hmLabels, hm, heatColor);

      // Daily stats table
      const daily = data.daily_stats || [];
      const dTbody = document.getElementById('daily-tbody');
      dTbody.innerHTML = daily.length === 0
        ? '<tr><td colspan="4">No data yet.</td></tr>'
        : daily.map(s =>
            `<tr><td>${{s.day}}</td><td>${{fmtDec(s.avg_noise)}}</td>` +
            `<td>${{fmtDec(s.peak_noise)}}</td><td>${{s.event_count ?? 0}}</td></tr>`
          ).join('');

      // Apply current timeframe
      applyTimeframe(activeTf);

      const bar = document.getElementById('status-bar');
      bar.className = 'status-bar hidden';
    }} catch (err) {{
      setStatusBar(`Failed to load data: ${{err.message}}`, true);
      console.error(err);
    }}
  }}

  // ── Timeframe buttons ─────────────────────────────────────────────────────
  document.querySelectorAll('.tf-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      activeTf = btn.dataset.tf;
      applyTimeframe(activeTf);
    }});
  }});

  // ── Boot ──────────────────────────────────────────────────────────────────
  initChart();
  fetchData();
  setInterval(fetchData, POLL_MS);
}})();
</script>
</body>
</html>"""


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _styles() -> str:
    return """<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  header { background: #1e293b; padding: 1.25rem 2rem; border-bottom: 1px solid #334155; }
  .header-inner { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: .75rem; }
  header h1 { font-size: 1.4rem; font-weight: 700; color: #38bdf8; }
  .header-sub { color: #94a3b8; font-size: .85rem; margin-top: .2rem; }
  .header-meta { color: #64748b; font-size: .78rem; display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; }
  .sep { color: #334155; }
  main { padding: 1.5rem 2rem; max-width: 1300px; margin: 0 auto; }

  .status-bar { padding: .6rem 1rem; border-radius: .5rem; font-size: .82rem; margin-bottom: 1rem; }
  .status-bar.hidden { display: none; }
  .status-bar.error { background: #450a0a; border: 1px solid #b91c1c; color: #fca5a5; }
  .status-bar.info  { background: #0c1a2e; border: 1px solid #1e40af; color: #93c5fd; }

  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; margin-bottom: 1.25rem; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: .75rem; padding: 1.25rem; }
  .card .label { font-size: .72rem; text-transform: uppercase; letter-spacing: .06em; color: #64748b; margin-bottom: .4rem; }
  .card .value { font-size: 2rem; font-weight: 700; color: #f1f5f9; }
  .card .value.ok    { color: #4ade80; }
  .card .value.warn  { color: #facc15; }
  .card .value.alert { color: #f87171; }
  .card .unit { font-size: .82rem; color: #94a3b8; margin-top: .2rem; }

  .panel { background: #1e293b; border: 1px solid #334155; border-radius: .75rem; padding: 1.25rem; margin-bottom: 1.25rem; }
  .panel-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: .75rem; flex-wrap: wrap; gap: .5rem; }
  .panel-header h2 { font-size: .9rem; color: #94a3b8; font-weight: 600; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 1.25rem; margin-bottom: 1.25rem; }
  @media (max-width: 700px) { .two-col { grid-template-columns: 1fr; } }

  .tf-btns { display: flex; gap: .3rem; }
  .tf-btn { background: #0f172a; border: 1px solid #334155; color: #94a3b8; border-radius: .4rem; padding: .2rem .55rem; font-size: .72rem; cursor: pointer; transition: background .15s, color .15s; }
  .tf-btn:hover { background: #1e3a5f; color: #e2e8f0; }
  .tf-btn.active { background: #0369a1; border-color: #38bdf8; color: #f0f9ff; }

  .chart-placeholder { display: flex; align-items: center; justify-content: center; height: 100%; color: #475569; font-size: .85rem; }

  /* Pure-CSS bar charts */
  .bar-chart-wrap { overflow: hidden; }
  .bar-chart { display: flex; align-items: flex-end; height: 160px; gap: 2px; padding-bottom: 22px; position: relative; }
  .bar-item { display: flex; flex-direction: column; align-items: center; flex: 1; height: 100%; justify-content: flex-end; cursor: default; }
  .bar { width: 100%; min-height: 2px; border-radius: 2px 2px 0 0; transition: height .3s; }
  .bar-lbl { font-size: 8px; color: #475569; margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; width: 100%; text-align: center; }

  table { width: 100%; border-collapse: collapse; }
  th, td { padding: .6rem 1rem; text-align: left; border-bottom: 1px solid #334155; font-size: .85rem; }
  th { color: #64748b; text-transform: uppercase; letter-spacing: .05em; font-size: .72rem; }
  tr:last-child td { border-bottom: none; }
</style>"""
