// ============================================================
//  MiniDash Endado — frontend vanilla
//  Consume /metrics/{filter} + /tables/*
// ============================================================

// Local (FastAPI sirve front + back) -> mismo origen.
// Prod (Render Static) -> API en VPS.
const API_BASE = (() => {
  const host = window.location.hostname;
  if (host === "localhost" || host === "127.0.0.1") return window.location.origin;
  return "https://api-minidash.facundo.click";
})();

const state = {
  from: null,   // YYYY-MM-DD
  to:   null,
  filter: "overall",
  granularity: "month",  // day | week | month (default "month" porque 24m en diario son 730 puntos)
};

let chart = null;

// ============================================================
//  utils
// ============================================================
const fmt = {
  int:  (n) => (n ?? 0).toLocaleString("es-AR"),
  pct:  (n) => (n == null ? "—" : (n >= 0 ? "+" : "") + (n * 100).toFixed(1) + "%"),
  pct2: (n) => (n == null ? "—" : (n * 100).toFixed(2) + "%"),
  pos:  (n) => (n ?? 0).toFixed(2),
};

function deltaClass(val, { lowerIsBetter = false } = {}) {
  if (val == null || Math.abs(val) < 0.005) return "flat";
  const up = val > 0;
  return (up && !lowerIsBetter) || (!up && lowerIsBetter) ? "up" : "down";
}

function daysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function setLoading(on, text = "cargando…") {
  document.getElementById("loading").classList.toggle("hidden", !on);
  document.getElementById("loading-text").textContent = text;
  document.getElementById("apply").disabled = on;
}

// ============================================================
//  Skeletons (placeholders mientras carga)
//  Cada loadX() llama su skeleton ANTES del fetch, asi nunca
//  se ve data vieja mientras llega data nueva.
// ============================================================
function renderKPIsSkeleton() {
  const card = `
    <div class="kpi">
      <div class="kpi-label"><span class="sk sk-text" style="width:60%">&nbsp;</span></div>
      <div class="kpi-value"><span class="sk sk-block" style="width:75%">&nbsp;</span></div>
      <div class="kpi-compare">
        <span><span class="sk sk-text" style="width:55%">&nbsp;</span></span>
        <span><span class="sk sk-text" style="width:60%">&nbsp;</span></span>
      </div>
    </div>`;
  document.getElementById("kpis").innerHTML = card.repeat(4);
}

function setTrendLoading(on) {
  document.getElementById("trend-loading").classList.toggle("hidden", !on);
}

function renderTableSkeleton(containerId, { rows = 6, cols = 6 } = {}) {
  const tr = `<tr>${Array.from({ length: cols }).map(() =>
    `<td><span class="sk sk-text" style="width:80%">&nbsp;</span></td>`
  ).join("")}</tr>`;
  document.getElementById(containerId).innerHTML =
    `<table><tbody>${tr.repeat(rows)}</tbody></table>`;
}

async function fetchJSON(path, extraParams = {}) {
  const url = new URL(path, API_BASE);
  if (state.from) url.searchParams.set("from", state.from);
  if (state.to)   url.searchParams.set("to",   state.to);
  for (const [k, v] of Object.entries(extraParams)) url.searchParams.set(k, v);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

// ============================================================
//  KPIs
// ============================================================
function renderKPIs(data) {
  const { kpis, mom, yoy } = data;
  const cards = [
    kpiCard("Clicks", fmt.int(kpis.clicks),
            mom.delta_pct.clicks, yoy.delta_pct.clicks, { lowerIsBetter: false }),
    kpiCard("Impresiones", fmt.int(kpis.impressions),
            mom.delta_pct.impressions, yoy.delta_pct.impressions, { lowerIsBetter: false }),
    kpiCard("CTR", fmt.pct2(kpis.ctr),
            mom.delta_pct.ctr, yoy.delta_pct.ctr, { lowerIsBetter: false }),
    kpiCard("Posición", fmt.pos(kpis.position),
            mom.delta_pct.position, yoy.delta_pct.position, { lowerIsBetter: true }),
  ];
  document.getElementById("kpis").innerHTML = cards.join("");
}

function kpiCard(label, value, momDelta, yoyDelta, opts) {
  return `
    <div class="kpi">
      <div class="kpi-label">${label}</div>
      <div class="kpi-value">${value}</div>
      <div class="kpi-compare">
        <span>MoM <span class="delta ${deltaClass(momDelta, opts)}">${fmt.pct(momDelta)}</span></span>
        <span>YoY <span class="delta ${deltaClass(yoyDelta, opts)}">${fmt.pct(yoyDelta)}</span></span>
      </div>
    </div>
  `;
}

// ============================================================
//  Trend chart
// ============================================================
function renderTrend(trend) {
  const ctx = document.getElementById("trend-chart");
  if (chart) chart.destroy();

  // Editorial palette — read from CSS vars so el chart respeta el theme
  const css = getComputedStyle(document.documentElement);
  const ink     = css.getPropertyValue("--ink").trim()      || "#1a1815";
  const ink2    = css.getPropertyValue("--ink-2").trim()    || "#5a564f";
  const ink3    = css.getPropertyValue("--ink-3").trim()    || "#9b958a";
  const accent  = css.getPropertyValue("--accent").trim()   || "#8b3a2f";
  const rule    = css.getPropertyValue("--rule").trim()     || "#d8d3c5";
  const paper   = css.getPropertyValue("--paper").trim()    || "#f7f5ee";
  const monoFont = '"JetBrains Mono", ui-monospace, monospace';
  const serifFont = '"Fraunces", Georgia, serif';

  chart = new Chart(ctx, {
    type: "line",
    data: {
      labels: trend.map((d) => d.date),
      datasets: [
        { label: "Clicks",      data: trend.map((d) => d.clicks),
          borderColor: ink, backgroundColor: "transparent", borderWidth: 1.5,
          yAxisID: "y", tension: 0.35, pointRadius: 0, pointHoverRadius: 4,
          pointHoverBackgroundColor: ink, pointHoverBorderColor: paper, pointHoverBorderWidth: 2 },
        { label: "Impresiones", data: trend.map((d) => d.impressions),
          borderColor: accent, backgroundColor: "transparent", borderWidth: 1,
          borderDash: [3, 4], yAxisID: "y1", tension: 0.35, pointRadius: 0, pointHoverRadius: 4,
          pointHoverBackgroundColor: accent, pointHoverBorderColor: paper, pointHoverBorderWidth: 2 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        y:  { position: "left",
              border: { display: false },
              title: { display: false },
              ticks: { color: ink3, font: { family: monoFont, size: 10 }, padding: 10 },
              grid:  { color: rule, drawTicks: false } },
        y1: { position: "right",
              border: { display: false },
              title: { display: false },
              ticks: { color: ink3, font: { family: monoFont, size: 10 }, padding: 10 },
              grid:  { display: false } },
        x:  { border: { display: false },
              ticks: { color: ink3, font: { family: monoFont, size: 10 }, maxRotation: 0, autoSkipPadding: 36, padding: 8 },
              grid:  { display: false } },
      },
      plugins: {
        legend: {
          align: "end",
          labels: {
            color: ink2,
            font: { family: monoFont, size: 10 },
            usePointStyle: true,
            pointStyle: "line",
            boxWidth: 24,
            padding: 16,
          },
        },
        tooltip: {
          backgroundColor: paper,
          titleColor: ink,
          bodyColor: ink,
          borderColor: rule,
          borderWidth: 1,
          padding: 12,
          cornerRadius: 0,
          titleFont: { family: monoFont, size: 10, weight: "500" },
          bodyFont:  { family: serifFont, size: 13 },
          displayColors: true,
          boxWidth: 8,
          boxHeight: 8,
          boxPadding: 4,
        },
      },
    },
  });
}

// ============================================================
//  Tables
// ============================================================
function renderTopPagesTable(containerId, rows, { showCategory = false } = {}) {
  if (!rows?.length) {
    document.getElementById(containerId).innerHTML = `<div style="padding:16px;color:#8b92a3">Sin datos.</div>`;
    return;
  }
  const head = `
    <tr>
      <th>URL</th>
      ${showCategory ? "<th>Categoría</th>" : ""}
      <th class="num">Clicks</th>
      <th class="num">MoM</th>
      <th class="num">YoY</th>
      <th class="num">Impr</th>
      <th class="num">CTR</th>
      <th class="num">Pos</th>
    </tr>
  `;
  const body = rows.map((r) => {
    const path = r.page.replace(/^https?:\/\/www\.endado\.com/, "") || "/";
    return `
      <tr>
        <td class="page-cell"><a href="${r.page}" target="_blank" rel="noopener" title="${r.page}">${path}</a></td>
        ${showCategory ? `<td>${escapeHtml(r.cat ?? "—")}</td>` : ""}
        <td class="num">${fmt.int(r.clicks)}</td>
        <td class="num"><span class="delta ${deltaClass(r.mom.delta_pct.clicks)}">${fmt.pct(r.mom.delta_pct.clicks)}</span></td>
        <td class="num"><span class="delta ${deltaClass(r.yoy.delta_pct.clicks)}">${fmt.pct(r.yoy.delta_pct.clicks)}</span></td>
        <td class="num">${fmt.int(r.impressions)}</td>
        <td class="num">${fmt.pct2(r.ctr)}</td>
        <td class="num">${fmt.pos(r.position)}</td>
      </tr>
    `;
  }).join("");
  document.getElementById(containerId).innerHTML = `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

function renderOpportunitiesTable(rows) {
  if (!rows?.length) {
    document.getElementById("opportunities-table").innerHTML = `<div style="padding:16px;color:#8b92a3">Sin datos.</div>`;
    return;
  }
  const head = `
    <tr>
      <th>Query</th>
      <th>URL destino</th>
      <th class="num">Impresiones</th>
      <th class="num">Clicks</th>
      <th class="num">Pos</th>
      <th class="num">YoY impr</th>
    </tr>
  `;
  const body = rows.map((r) => {
    const path = r.page.replace(/^https?:\/\/www\.endado\.com/, "") || "/";
    return `
      <tr>
        <td>${escapeHtml(r.query)}</td>
        <td class="page-cell"><a href="${r.page}" target="_blank" rel="noopener" title="${r.page}">${path}</a></td>
        <td class="num">${fmt.int(r.impressions)}</td>
        <td class="num">${fmt.int(r.clicks)}</td>
        <td class="num">${fmt.pos(r.position)}</td>
        <td class="num"><span class="delta ${deltaClass(r.yoy.delta_pct.impressions)}">${fmt.pct(r.yoy.delta_pct.impressions)}</span></td>
      </tr>
    `;
  }).join("");
  document.getElementById("opportunities-table").innerHTML = `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

function renderCannibalizationTable(rows) {
  if (!rows?.length) {
    document.getElementById("cannibalization-table").innerHTML = `<div style="padding:16px;color:#8b92a3">Sin datos.</div>`;
    return;
  }
  const head = `<tr><th>Query</th><th class="num"># URLs</th><th class="num">Clicks</th><th class="num">Impr</th><th>URLs que canibalizan</th></tr>`;
  const body = rows.map((r) => {
    const urlsList = (r.urls || []).slice(0, 5).map((u) => {
      const path = u.page.replace(/^https?:\/\/www\.endado\.com/, "") || "/";
      return `<div class="canib-url"><a href="${u.page}" target="_blank" rel="noopener" title="${u.page}">${path}</a> <span class="canib-meta">cl ${u.clicks} · pos ${fmt.pos(u.position)}</span></div>`;
    }).join("");
    return `
      <tr>
        <td>${escapeHtml(r.query)}</td>
        <td class="num">${r.total_urls}</td>
        <td class="num">${fmt.int(r.clicks)}</td>
        <td class="num">${fmt.int(r.impressions)}</td>
        <td>${urlsList}</td>
      </tr>
    `;
  }).join("");
  document.getElementById("cannibalization-table").innerHTML = `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

function escapeHtml(s) {
  return (s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

// ============================================================
//  Loader principal
// ============================================================
// Carga SOLO KPIs (dependen de filter + date picker)
async function loadMetrics() {
  renderKPIsSkeleton();
  const m = await fetchJSON(`/metrics/${state.filter}`);
  renderKPIs(m.data);
}

// Carga SOLO trend (depende de filter + granularity, rango fijo 24m)
async function loadTrend() {
  setTrendLoading(true);
  try {
    const t = await fetchJSON(`/trend/${state.filter}`, { granularity: state.granularity });
    renderTrend(t.data.points);
  } finally {
    setTrendLoading(false);
  }
}

// Carga SOLO tablas (dependen de date picker, no de filter ni granularity)
async function loadTables() {
  // Skeleton por tabla (cols aproximadas, no es critico que coincida exacto).
  renderTableSkeleton("top-products-table",   { cols: 8 });
  renderTableSkeleton("top-categories-table", { cols: 7 });
  renderTableSkeleton("opportunities-table",  { cols: 6 });
  renderTableSkeleton("cannibalization-table",{ cols: 5 });

  const [topProducts, topCategories, opportunities, cannibalization] = await Promise.all([
    fetchJSON("/tables/top-products", { limit: 20 }),
    fetchJSON("/tables/top-categories", { limit: 20 }),
    fetchJSON("/tables/opportunities", { limit: 50, min_imp: 500 }),
    fetchJSON("/tables/cannibalization", { limit: 50 }),
  ]);
  renderTopPagesTable("top-products-table",   topProducts.data.rows,   { showCategory: true });
  renderTopPagesTable("top-categories-table", topCategories.data.rows, { showCategory: false });
  renderOpportunitiesTable(opportunities.data.rows);
  renderCannibalizationTable(cannibalization.data.rows);
}

// Cambio de fecha → refresca KPIs + tablas (NO trend)
async function reloadByDate() {
  setLoading(true);
  try {
    await Promise.all([loadMetrics(), loadTables()]);
  } catch (e) {
    console.error(e);
    alert("Error: " + e.message);
  } finally {
    setLoading(false);
  }
}

// Cambio de tab → refresca KPIs + trend
async function reloadByFilter() {
  setLoading(true);
  try {
    await Promise.all([loadMetrics(), loadTrend()]);
  } catch (e) {
    console.error(e);
    alert("Error: " + e.message);
  } finally {
    setLoading(false);
  }
}

// Cambio de granularidad → solo trend
async function reloadByGranularity() {
  setLoading(true);
  try {
    await loadTrend();
  } catch (e) {
    console.error(e);
    alert("Error: " + e.message);
  } finally {
    setLoading(false);
  }
}

// Init — carga todo la primera vez
async function loadAll() {
  setLoading(true);
  try {
    await Promise.all([loadMetrics(), loadTrend(), loadTables()]);
  } catch (e) {
    console.error(e);
    alert("Error: " + e.message);
  } finally {
    setLoading(false);
  }
}

// ============================================================
//  Events
// ============================================================
document.getElementById("apply").addEventListener("click", () => {
  state.from = document.getElementById("from").value || null;
  state.to   = document.getElementById("to").value   || null;
  reloadByDate();    // solo KPIs + tablas, NO trend
});

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.filter = btn.dataset.filter;
    reloadByFilter();   // KPIs + trend, NO tablas
  });
});

document.querySelectorAll(".seg-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".seg-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.granularity = btn.dataset.gran;
    reloadByGranularity();   // solo trend
  });
});

// ============================================================
//  Init — default: ultimos 30d ending today-3 (GSC lag)
// ============================================================
(function init() {
  const defaultTo   = daysAgo(3);
  const defaultFrom = daysAgo(3 + 29);
  state.from = defaultFrom; state.to = defaultTo;
  document.getElementById("from").value = defaultFrom;
  document.getElementById("to").value   = defaultTo;
  loadAll();
})();
