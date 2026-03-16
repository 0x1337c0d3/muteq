/**
 * MUTEq Dashboard — TradingView Lightweight Charts
 *
 * Features:
 *  - SPL line/area chart with timeframe buttons (10m / 1h / 1d / 1w / 1m)
 *  - Lazy scroll-back: fetches older batches when the user scrolls left
 *  - Auto-refresh every 30 s: appends new readings and refreshes stats
 *  - CSS bar charts for noise distribution histogram + hourly event heatmap
 *  - KPI cards, daily stats table, threshold events table
 */

'use strict';

// ── Constants ────────────────────────────────────────────────────────────────

const BATCH_SIZE = 2000;
const PREFETCH_BARS = 50;          // trigger history-load this many bars from left edge
const REFRESH_INTERVAL_MS = 30_000;

/** How many seconds each timeframe window covers. */
const TF_SECONDS = {
  '1d': 24 * 60 * 60,
  '1w':  7 * 24 * 60 * 60,
};

// ── State ────────────────────────────────────────────────────────────────────

let chart     = null;
let splSeries = null;

let currentTf     = '10m';
let loadedData    = [];   // [{time: unix_seconds, value: dB}], sorted ascending
let isLoading     = false;
let noMoreHistory = false;
let refreshTimer  = null;

// ── DOM helpers ──────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

function showLoading(on) {
  $('loading-overlay').classList.toggle('hidden', !on);
}

function showEmpty(on) {
  $('empty-state').classList.toggle('hidden', !on);
}

function setStatus(text, type = 'ok') {
  const pill = $('status-pill');
  pill.textContent = text;
  pill.className = `pill pill--${type}`;
}

// ── Chart initialisation ─────────────────────────────────────────────────────

function createChart() {
  const container = $('chart-container');

  chart = LightweightCharts.createChart(container, {
    width:  container.clientWidth,
    height: container.clientHeight,
    layout: {
      background: { color: '#131722' },
      textColor: '#d1d4dc',
    },
    grid: {
      vertLines: { color: '#1e2130' },
      horzLines: { color: '#1e2130' },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: '#758696', labelBackgroundColor: '#2a2e3e' },
      horzLine: { color: '#758696', labelBackgroundColor: '#2a2e3e' },
    },
    rightPriceScale: {
      borderColor: '#2a2e3e',
      textColor: '#d1d4dc',
    },
    timeScale: {
      borderColor: '#2a2e3e',
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 12,
      // Render all axis tick labels in the browser's local timezone (e.g. AEST)
      tickMarkFormatter: (ts, tickMarkType) => {
        const d = new Date(ts * 1000);
        const TT = LightweightCharts.TickMarkType;
        switch (tickMarkType) {
          case TT.Year:
            return d.toLocaleDateString([], { year: 'numeric' });
          case TT.Month:
            return d.toLocaleDateString([], { month: 'short', year: 'numeric' });
          case TT.DayOfMonth:
            return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
          case TT.Time:
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
          case TT.TimeWithSeconds:
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
          default:
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }
      },
    },
    handleScroll: { mouseWheel: true, pressedMouseMove: true },
    handleScale:  { mouseWheel: true, pinch: true },
  });

  splSeries = chart.addAreaSeries({
    topColor:              'rgba(56, 189, 248, 0.28)',
    bottomColor:           'rgba(56, 189, 248, 0.02)',
    lineColor:             '#38bdf8',
    lineWidth:             2,
    priceLineVisible:      false,
    lastValueVisible:      true,
    crosshairMarkerVisible: true,
    crosshairMarkerRadius: 4,
    priceFormat: { type: 'price', precision: 1, minMove: 0.1 },
  });

  // 60 dB lower band line
  splSeries.createPriceLine({
    price:       60,
    color:       '#4ade80',
    lineWidth:   1,
    lineStyle:   LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true,
    title:       '60 dB',
  });

  // 70 dB threshold line
  splSeries.createPriceLine({
    price:       70,
    color:       '#f87171',
    lineWidth:   1,
    lineStyle:   LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true,
    title:       '70 dB',
  });

  // Crosshair tooltip
  chart.subscribeCrosshairMove(updateTooltip);

  // Keep chart filling its container on resize
  new ResizeObserver(() => {
    chart.applyOptions({
      width:  container.clientWidth,
      height: container.clientHeight,
    });
  }).observe(container);

  // Lazy-load older history when scrolling left
  chart.timeScale().subscribeVisibleLogicalRangeChange(onRangeChange);
}

function updateTooltip(param) {
  const tooltip = $('spl-tooltip');
  if (!param || !param.time || !splSeries) {
    tooltip.classList.add('hidden');
    return;
  }
  const bar = param.seriesData && param.seriesData.get(splSeries);
  if (!bar) {
    tooltip.classList.add('hidden');
    return;
  }

  const dt    = new Date(bar.time * 1000);
  const color = bar.value >= 90 ? '#f87171' : bar.value >= 70 ? '#facc15' : '#4ade80';

  tooltip.innerHTML =
    `<span class="tt-time">${dt.toLocaleString()}</span>` +
    `<span class="tt-val" style="color:${color}">${bar.value.toFixed(1)} dB</span>`;
  tooltip.classList.remove('hidden');
}

// ── API ───────────────────────────────────────────────────────────────────────

async function apiFetch(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}

async function fetchReadings(fromTs = null, toTs = null, limit = BATCH_SIZE) {
  const p = new URLSearchParams({ limit });
  if (fromTs !== null) p.set('from_ts', fromTs);
  if (toTs   !== null) p.set('to_ts',   toTs);
  return apiFetch(`/api/readings?${p}`);
}

async function fetchEvents(fromTs = null) {
  const p = new URLSearchParams({ limit: 500 });
  if (fromTs !== null) p.set('from_ts', fromTs);
  return apiFetch(`/api/events?${p}`);
}

async function fetchStats(fromTs = null) {
  const p = new URLSearchParams();
  if (fromTs !== null) p.set('from_ts', fromTs);
  return apiFetch(`/api/stats?${p}`);
}

// ── Data management ───────────────────────────────────────────────────────────

function mergeData(newData) {
  if (!newData.length) return;
  if (!loadedData.length) {
    loadedData = newData;
    return;
  }
  const times   = new Set(loadedData.map((d) => d.time));
  const deduped = newData.filter((d) => !times.has(d.time));
  if (!deduped.length) return;

  if (deduped[0].time < loadedData[0].time) {
    loadedData = [...deduped, ...loadedData];
  } else {
    loadedData = [...loadedData, ...deduped];
  }
}

function applyDataToChart() {
  if (splSeries) splSeries.setData(loadedData);
}

// ── Load / reload ─────────────────────────────────────────────────────────────

/** Full reload for new timeframe selection. */
async function loadTimeframe(tf) {
  if (isLoading) return;
  isLoading     = true;
  noMoreHistory = false;
  loadedData    = [];
  currentTf     = tf;

  // Update button + title
  document.querySelectorAll('.tf-btn').forEach((b) => {
    b.classList.toggle('active', b.dataset.tf === tf);
  });
  const activeBtn = document.querySelector(`.tf-btn[data-tf="${tf}"]`);
  if (activeBtn) $('chart-title').textContent = `SPL \u2014 ${activeBtn.dataset.label}`;

  showLoading(true);
  showEmpty(false);
  setStatus('Loading\u2026', 'loading');

  const fromTs = Math.floor(Date.now() / 1000) - TF_SECONDS[tf];

  try {
    const data = await fetchReadings(fromTs, null, BATCH_SIZE);
    if (!data.length) {
      showEmpty(true);
      setStatus('No data', 'warn');
      return;
    }
    mergeData(data);
    applyDataToChart();
    chart.timeScale().scrollToRealTime();
    setStatus('Live', 'ok');
    markUpdated();

    await refreshStats(fromTs, tf);
  } catch (err) {
    console.error('loadTimeframe error:', err);
    setStatus('Error', 'error');
  } finally {
    showLoading(false);
    isLoading = false;
  }
}

/** Fetch older candles when the user scrolls left. */
async function loadOlderHistory() {
  if (isLoading || noMoreHistory || !loadedData.length) return;
  isLoading = true;

  const oldestTime = loadedData[0].time;
  try {
    const data = await fetchReadings(null, oldestTime - 1, BATCH_SIZE);
    if (!data.length) {
      noMoreHistory = true;
      return;
    }
    mergeData(data);
    applyDataToChart();
  } catch (err) {
    console.error('loadOlderHistory error:', err);
  } finally {
    isLoading = false;
  }
}

/** Poll for new readings and refresh stats. */
async function refreshLatest() {
  if (isLoading || !loadedData.length) return;

  const latestTime = loadedData[loadedData.length - 1].time;
  const fromTs     = Math.floor(Date.now() / 1000) - TF_SECONDS[currentTf];

  try {
    const data = await fetchReadings(latestTime + 1, null, BATCH_SIZE);
    if (data.length) {
      mergeData(data);
      applyDataToChart();
      markUpdated();
    }
    await refreshStats(fromTs, currentTf);
    setStatus('Live', 'ok');
  } catch (err) {
    console.error('refreshLatest error:', err);
    setStatus('Error', 'error');
  }
}

async function refreshStats(fromTs, tf) {
  try {
    const [stats, events] = await Promise.all([
      fetchStats(fromTs),
      fetchEvents(fromTs),
    ]);
    updateKpi(stats);
    renderHistogram(stats.histogram);
    renderHeatmap(stats.heatmap);
    renderDailyTable(stats.daily_stats);
    renderEventsTable(events, tf);
  } catch (err) {
    console.error('refreshStats error:', err);
  }
}

function onRangeChange(range) {
  if (!range || isLoading || noMoreHistory || !loadedData.length) return;
  if (range.from <= PREFETCH_BARS) {
    loadOlderHistory();
  }
}

function markUpdated() {
  $('last-updated').textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

// ── KPI cards ─────────────────────────────────────────────────────────────────

function updateKpi(stats) {
  const fmt = (v) => (v != null ? v.toFixed(1) : '\u2014');

  $('kpi-latest').textContent = fmt(stats.latest);
  $('kpi-peak').textContent   = fmt(stats.peak);
  $('kpi-events').textContent = stats.event_count;
  $('kpi-p90').textContent    = fmt(stats.percentiles.p90);

  const v = stats.latest;
  $('kpi-latest').className =
    'card-value' + (v == null ? '' : v >= 90 ? ' alert' : v >= 70 ? ' warn' : ' ok');
}

// ── Bar charts ────────────────────────────────────────────────────────────────

function renderHistogram(hist) {
  const max = Math.max(...hist.counts, 1);
  $('histogram-chart').innerHTML = hist.labels
    .map((label, i) => {
      const pct   = Math.round((hist.counts[i] / max) * 100);
      const frac  = i / (hist.labels.length - 1);
      const color = frac < 0.5 ? '#4ade80' : frac < 0.75 ? '#facc15' : '#f87171';
      return barRow(label, pct, color, hist.counts[i]);
    })
    .join('');
}

function renderHeatmap(heatmap) {
  const max = Math.max(...heatmap, 1);
  $('heatmap-chart').innerHTML = heatmap
    .map((count, hour) => {
      const pct   = Math.round((count / max) * 100);
      const color = count === 0 ? '#1e3a5f' : '#f87171';
      const label = String(hour).padStart(2, '0') + ':00';
      return barRow(label, pct, color, count);
    })
    .join('');
}

function barRow(label, pct, color, count) {
  return (
    `<div class="bar-row">` +
    `<span class="bar-label">${label}</span>` +
    `<div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>` +
    `<span class="bar-count">${count}</span>` +
    `</div>`
  );
}

// ── Tables ────────────────────────────────────────────────────────────────────

function renderDailyTable(daily) {
  const tbody = $('daily-tbody');
  if (!daily.length) {
    tbody.innerHTML = '<tr><td colspan="4">No data yet.</td></tr>';
    return;
  }
  tbody.innerHTML = daily
    .map(
      (d) =>
        `<tr><td>${d.day}</td><td>${d.avg_noise ?? '\u2014'}</td>` +
        `<td>${d.peak_noise ?? '\u2014'}</td><td>${d.event_count}</td></tr>`
    )
    .join('');
}

function renderEventsTable(events, tf) {
  const activeBtn = document.querySelector(`.tf-btn[data-tf="${tf}"]`);
  const label     = activeBtn ? activeBtn.dataset.label : tf;
  $('events-title').textContent = `Threshold Events \u2014 ${label}`;

  const tbody = $('events-tbody');
  if (!events.length) {
    tbody.innerHTML = '<tr><td colspan="3">No events in this period.</td></tr>';
    return;
  }
  tbody.innerHTML = events
    .map(
      (e) =>
        `<tr><td>${new Date(e.time * 1000).toLocaleString()}</td>` +
        `<td>${e.noise.toFixed(1)}</td>` +
        `<td>${e.peak != null ? e.peak.toFixed(1) : '\u2014'}</td></tr>`
    )
    .join('');
}

// ── Boot ─────────────────────────────────────────────────────────────────────

async function boot() {
  createChart();

  // Load device config for header
  try {
    const cfg = await apiFetch('/api/config');
    document.title = `${cfg.device_name} \u2014 MUTEq`;
    $('device-name').textContent = cfg.device_name;
    if (cfg.location)            $('location').textContent   = `\u{1F4CD} ${cfg.location}`;
    if (cfg.environment_profile) $('env-profile').textContent = cfg.environment_profile;
  } catch (err) {
    console.warn('Could not load config:', err);
  }

  // Timeframe button listeners
  document.querySelectorAll('.tf-btn').forEach((btn) => {
    btn.addEventListener('click', () => loadTimeframe(btn.dataset.tf));
  });

  // Initial load
  await loadTimeframe('1d');

  // Auto-refresh
  refreshTimer = setInterval(refreshLatest, REFRESH_INTERVAL_MS);
}

document.addEventListener('DOMContentLoaded', boot);
