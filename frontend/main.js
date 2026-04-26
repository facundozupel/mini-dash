// ============================================================
//  MiniDash Endado — frontend vanilla
//  Consume /metrics/{filter} + /tables/*
// ============================================================

// Mismo origen: el front se sirve desde FastAPI, sin CORS
const API_BASE = window.location.origin;

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
  chart = new Chart(ctx, {
    type: "line",
    data: {
      labels: trend.map((d) => d.date),
      datasets: [
        { label: "Clicks",      data: trend.map((d) => d.clicks),
          borderColor: "#5da8ff", backgroundColor: "#5da8ff22", yAxisID: "y", tension: 0.3, pointRadius: 0 },
        { label: "Impresiones", data: trend.map((d) => d.impressions),
          borderColor: "#facc15", backgroundColor: "#facc1522", yAxisID: "y1", tension: 0.3, pointRadius: 0 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        y:  { position: "left",  title: { display: true, text: "Clicks",      color: "#8b92a3" }, ticks: { color: "#8b92a3" }, grid: { color: "#262b35" } },
        y1: { position: "right", title: { display: true, text: "Impresiones", color: "#8b92a3" }, ticks: { color: "#8b92a3" }, grid: { display: false } },
        x:  { ticks: { color: "#8b92a3", maxRotation: 0, autoSkipPadding: 30 }, grid: { color: "#262b35" } },
      },
      plugins: {
        legend: { labels: { color: "#e7eaf0" } },
        tooltip: { backgroundColor: "#181b22", titleColor: "#e7eaf0", bodyColor: "#e7eaf0", borderColor: "#262b35", borderWidth: 1 },
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
      return `<a href="${u.page}" target="_blank" rel="noopener" title="${u.page}">${path}</a> <span style="color:#8b92a3">(cl ${u.clicks}, pos ${fmt.pos(u.position)})</span>`;
    }).join("<br>");
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
