// ============================================================
//  MiniDash Endado — frontend vanilla
//  Consume /metrics/{filter} + /tables/*
// ============================================================

// Local (FastAPI sirve front + back) -> mismo origen para GSC.
// La API de bots vive en su propio VPS (Oracle Sao Paulo); apuntamos directo
// porque el local de MICRO-DASH no la sirve.
const API_BASE = (() => {
  const host = window.location.hostname;
  if (host === "localhost" || host === "127.0.0.1") return window.location.origin;
  return "https://api-minidash.facundo.click";
})();
const API_BOT_BASE = "https://api-bots.facundo.click";

const state = {
  from: null,   // YYYY-MM-DD
  to:   null,
  filter: "overall",                  // GSC: overall | products | category | recambios
  granularity: "month",               // GSC trend (day|week|month, default month porque 24m en diario son 730 puntos)
  mode: "gsc",                        // gsc | bots — switcher principal
  botFilter: "all",                   // Bots: all | ai | search | other
  botGranularity: "month",            // Bots trend
  botsLoaded: false,                  // lazy init del modo bots la primera vez que se entra
};

let chart = null;       // chart de GSC
let botChart = null;    // chart de bots

// ============================================================
//  utils
// ============================================================
const fmt = {
  int:  (n) => (n ?? 0).toLocaleString("es-AR"),
  pct:  (n) => (n == null ? "—" : (n >= 0 ? "+" : "") + (n * 100).toFixed(1) + "%"),
  pct2: (n) => (n == null ? "—" : (n * 100).toFixed(2) + "%"),
  pos:  (n) => (n ?? 0).toFixed(2),
  num1: (n) => (n == null ? "—" : Number(n).toFixed(1)),
  // bytes humano-legible (1024 base). 156 GB > 156000000000 B.
  bytes: (n) => {
    if (n == null) return "—";
    const units = ["B", "KB", "MB", "GB", "TB", "PB"];
    let i = 0, v = Number(n);
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
  },
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

async function fetchJSON(path, extraParams = {}, base = API_BASE) {
  const url = new URL(path, base);
  if (state.from) url.searchParams.set("from", state.from);
  if (state.to)   url.searchParams.set("to",   state.to);
  for (const [k, v] of Object.entries(extraParams)) url.searchParams.set(k, v);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

const fetchBotJSON = (path, extraParams = {}) => fetchJSON(path, extraParams, API_BOT_BASE);

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
//  BOTS — KPIs
//  5 cards: hits, total_bytes, distinct_bots, distinct_urls,
//           avg_unique_ips_per_day. Todos lower-is-NOT-better.
// ============================================================
function renderBotKPIsSkeleton() {
  const card = `
    <div class="kpi">
      <div class="kpi-label"><span class="sk sk-text" style="width:60%">&nbsp;</span></div>
      <div class="kpi-value"><span class="sk sk-block" style="width:75%">&nbsp;</span></div>
      <div class="kpi-compare">
        <span><span class="sk sk-text" style="width:55%">&nbsp;</span></span>
        <span><span class="sk sk-text" style="width:60%">&nbsp;</span></span>
      </div>
    </div>`;
  document.getElementById("bot-kpis").innerHTML = card.repeat(5);
}

function renderBotKPIs(data) {
  const { kpis, mom, yoy } = data;
  const cards = [
    kpiCard("Hits",            fmt.int(kpis.hits),
            mom.delta_pct.hits,            yoy.delta_pct.hits,            { lowerIsBetter: false }),
    kpiCard("Bytes servidos",  fmt.bytes(kpis.total_bytes),
            mom.delta_pct.total_bytes,     yoy.delta_pct.total_bytes,     { lowerIsBetter: false }),
    kpiCard("Bots distintos",  fmt.int(kpis.distinct_bots),
            mom.delta_pct.distinct_bots,   yoy.delta_pct.distinct_bots,   { lowerIsBetter: false }),
    kpiCard("URLs distintas",  fmt.int(kpis.distinct_urls),
            mom.delta_pct.distinct_urls,   yoy.delta_pct.distinct_urls,   { lowerIsBetter: false }),
    kpiCard("IPs ún. / día",   fmt.num1(kpis.avg_unique_ips_per_day),
            mom.delta_pct.avg_unique_ips_per_day, yoy.delta_pct.avg_unique_ips_per_day, { lowerIsBetter: false }),
  ];
  document.getElementById("bot-kpis").innerHTML = cards.join("");
}

// ============================================================
//  BOTS — Trend chart (hits + bytes)
// ============================================================
function setBotTrendLoading(on) {
  document.getElementById("bot-trend-loading").classList.toggle("hidden", !on);
}

function renderBotTrend(points) {
  const ctx = document.getElementById("bot-trend-chart");
  if (botChart) botChart.destroy();

  const css = getComputedStyle(document.documentElement);
  const ink     = css.getPropertyValue("--ink").trim()      || "#1a1815";
  const ink2    = css.getPropertyValue("--ink-2").trim()    || "#5a564f";
  const ink3    = css.getPropertyValue("--ink-3").trim()    || "#9b958a";
  const accent  = css.getPropertyValue("--accent").trim()   || "#8b3a2f";
  const rule    = css.getPropertyValue("--rule").trim()     || "#d8d3c5";
  const paper   = css.getPropertyValue("--paper").trim()    || "#f7f5ee";
  const monoFont = '"JetBrains Mono", ui-monospace, monospace';
  const serifFont = '"Fraunces", Georgia, serif';

  botChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: points.map((d) => d.date),
      datasets: [
        { label: "Hits", data: points.map((d) => d.hits),
          borderColor: ink, backgroundColor: "transparent", borderWidth: 1.5,
          yAxisID: "y", tension: 0.35, pointRadius: 0, pointHoverRadius: 4,
          pointHoverBackgroundColor: ink, pointHoverBorderColor: paper, pointHoverBorderWidth: 2 },
        { label: "Bytes", data: points.map((d) => d.total_bytes),
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
        y:  { position: "left",  border: { display: false }, ticks: { color: ink3, font: { family: monoFont, size: 10 }, padding: 10 }, grid: { color: rule, drawTicks: false } },
        y1: { position: "right", border: { display: false }, ticks: { color: ink3, font: { family: monoFont, size: 10 }, padding: 10, callback: (v) => fmt.bytes(v) }, grid: { display: false } },
        x:  { border: { display: false }, ticks: { color: ink3, font: { family: monoFont, size: 10 }, maxRotation: 0, autoSkipPadding: 36, padding: 8 }, grid: { display: false } },
      },
      plugins: {
        legend: { align: "end", labels: { color: ink2, font: { family: monoFont, size: 10 }, usePointStyle: true, pointStyle: "line", boxWidth: 24, padding: 16 } },
        tooltip: {
          backgroundColor: paper, titleColor: ink, bodyColor: ink,
          borderColor: rule, borderWidth: 1, padding: 12, cornerRadius: 0,
          titleFont: { family: monoFont, size: 10, weight: "500" },
          bodyFont:  { family: serifFont, size: 13 },
          displayColors: true, boxWidth: 8, boxHeight: 8, boxPadding: 4,
          callbacks: {
            label: (ctx) => {
              const v = ctx.parsed.y;
              return ctx.dataset.label === "Bytes"
                ? `${ctx.dataset.label}: ${fmt.bytes(v)}`
                : `${ctx.dataset.label}: ${fmt.int(v)}`;
            },
          },
        },
      },
    },
  });
}

// ============================================================
//  BOTS — Tables
// ============================================================
function renderBotTopUrlsTable(rows) {
  const target = document.getElementById("bot-top-urls-table");
  if (!rows?.length) { target.innerHTML = `<div style="padding:16px;color:#8b92a3">Sin datos.</div>`; return; }
  const head = `
    <tr>
      <th>URL</th>
      <th>Sección</th>
      <th class="num">Hits</th>
      <th class="num">MoM</th>
      <th class="num">YoY</th>
      <th class="num">Bytes</th>
      <th class="num">IPs ún. / día</th>
    </tr>`;
  const body = rows.map((r) => `
    <tr>
      <td class="page-cell"><a href="https://www.endado.com${r.url_path}" target="_blank" rel="noopener" title="${escapeHtml(r.url_path)}">${escapeHtml(r.url_path)}</a></td>
      <td>${escapeHtml(r.section_top ?? "—")}</td>
      <td class="num">${fmt.int(r.hits)}</td>
      <td class="num"><span class="delta ${deltaClass(r.mom.delta_pct.hits)}">${fmt.pct(r.mom.delta_pct.hits)}</span></td>
      <td class="num"><span class="delta ${deltaClass(r.yoy.delta_pct.hits)}">${fmt.pct(r.yoy.delta_pct.hits)}</span></td>
      <td class="num">${fmt.bytes(r.total_bytes)}</td>
      <td class="num">${fmt.num1(r.avg_unique_ips)}</td>
    </tr>`).join("");
  target.innerHTML = `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

function renderBotTopBotsTable(rows) {
  const target = document.getElementById("bot-top-bots-table");
  if (!rows?.length) { target.innerHTML = `<div style="padding:16px;color:#8b92a3">Sin datos.</div>`; return; }
  const tag = (r) => {
    if (r.is_ai_bot)     return `<span class="canib-meta">AI</span>`;
    if (r.is_search_bot) return `<span class="canib-meta">Search</span>`;
    return `<span class="canib-meta">${escapeHtml(r.bot_category ?? "—")}</span>`;
  };
  const head = `
    <tr>
      <th>Bot</th>
      <th>Tipo</th>
      <th class="num">Hits</th>
      <th class="num">MoM</th>
      <th class="num">YoY</th>
      <th class="num">Bytes</th>
      <th class="num">URLs distintas</th>
    </tr>`;
  const body = rows.map((r) => `
    <tr>
      <td>${escapeHtml(r.bot_name)}</td>
      <td>${tag(r)}</td>
      <td class="num">${fmt.int(r.hits)}</td>
      <td class="num"><span class="delta ${deltaClass(r.mom.delta_pct.hits)}">${fmt.pct(r.mom.delta_pct.hits)}</span></td>
      <td class="num"><span class="delta ${deltaClass(r.yoy.delta_pct.hits)}">${fmt.pct(r.yoy.delta_pct.hits)}</span></td>
      <td class="num">${fmt.bytes(r.total_bytes)}</td>
      <td class="num">${fmt.int(r.distinct_urls)}</td>
    </tr>`).join("");
  target.innerHTML = `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

function renderBotBySectionTable(rows) {
  const target = document.getElementById("bot-by-section-table");
  if (!rows?.length) { target.innerHTML = `<div style="padding:16px;color:#8b92a3">Sin datos.</div>`; return; }
  const head = `
    <tr>
      <th>Sección</th>
      <th class="num">Hits</th>
      <th class="num">% del total</th>
      <th class="num">Bytes</th>
      <th class="num">URLs</th>
      <th class="num">Bots</th>
    </tr>`;
  const body = rows.map((r) => `
    <tr>
      <td>${escapeHtml(r.section)}</td>
      <td class="num">${fmt.int(r.hits)}</td>
      <td class="num">${fmt.pct2(r.share)}</td>
      <td class="num">${fmt.bytes(r.total_bytes)}</td>
      <td class="num">${fmt.int(r.distinct_urls)}</td>
      <td class="num">${fmt.int(r.distinct_bots)}</td>
    </tr>`).join("");
  target.innerHTML = `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

function renderBotByStatusTable(rows) {
  const target = document.getElementById("bot-by-status-table");
  if (!rows?.length) { target.innerHTML = `<div style="padding:16px;color:#8b92a3">Sin datos.</div>`; return; }
  const head = `
    <tr>
      <th></th>
      <th>Status class</th>
      <th class="num">Hits</th>
      <th class="num">% del total</th>
      <th class="num">Bytes</th>
    </tr>`;
  // Cada fila pintada con su drill-down row hidden debajo.
  // Click en la fila → carga URLs Googlebot con ese status_class.
  const body = rows.map((r) => `
    <tr class="status-row" data-status-class="${escapeHtml(r.status_class)}">
      <td class="caret">▸</td>
      <td>${escapeHtml(r.status_class)}</td>
      <td class="num">${fmt.int(r.hits)}</td>
      <td class="num">${fmt.pct2(r.share)}</td>
      <td class="num">${fmt.bytes(r.total_bytes)}</td>
    </tr>
    <tr class="status-drill hidden" data-status-class="${escapeHtml(r.status_class)}">
      <td colspan="5"><div class="drill-content"><div class="drill-empty">click para cargar…</div></div></td>
    </tr>`).join("");
  target.innerHTML = `<table class="status-table"><thead>${head}</thead><tbody>${body}</tbody></table>`;

  // Wire clicks
  target.querySelectorAll(".status-row").forEach((row) => {
    row.addEventListener("click", () => toggleStatusDrill(row));
  });
}

// Cache local: status_class → rows ya traidas.
const _drillCache = {};

async function toggleStatusDrill(row) {
  const statusClass = row.dataset.statusClass;
  const drillRow = document.querySelector(`#bot-by-status-table .status-drill[data-status-class="${statusClass}"]`);
  if (!drillRow) return;

  const isOpen = !drillRow.classList.contains("hidden");
  if (isOpen) {
    drillRow.classList.add("hidden");
    row.querySelector(".caret").textContent = "▸";
    return;
  }

  drillRow.classList.remove("hidden");
  row.querySelector(".caret").textContent = "▾";

  if (_drillCache[statusClass]) {
    renderDrillContent(drillRow, _drillCache[statusClass], statusClass);
    return;
  }

  // Loading skeleton
  drillRow.querySelector(".drill-content").innerHTML =
    `<div class="drill-loading"><div class="spinner"></div><span>cargando URLs ${statusClass} de Googlebot…</span></div>`;

  try {
    const r = await fetchBotJSON(
      `/tables/urls-by-status/${state.botFilter}/${statusClass}`,
      { bot_name: "Googlebot", limit: 20 }
    );
    _drillCache[statusClass] = r.data;
    renderDrillContent(drillRow, r.data, statusClass);
  } catch (e) {
    drillRow.querySelector(".drill-content").innerHTML =
      `<div class="drill-empty">Error: ${escapeHtml(e.message)}</div>`;
  }
}

function renderDrillContent(drillRow, data, statusClass) {
  const rows = data.rows || [];
  if (!rows.length) {
    drillRow.querySelector(".drill-content").innerHTML =
      `<div class="drill-empty">Sin URLs ${statusClass} de Googlebot en este rango.</div>`;
    return;
  }
  const head = `
    <tr>
      <th>URL</th>
      <th>Sección</th>
      <th class="num">Hits Googlebot</th>
      <th class="num">Bytes prom/hit</th>
      <th class="num">Bytes totales</th>
    </tr>`;
  const body = rows.map((r) => {
    const path = r.url_path;
    return `
      <tr>
        <td class="page-cell"><a href="https://www.endado.com${escapeHtml(path)}" target="_blank" rel="noopener" title="${escapeHtml(path)}">${escapeHtml(path)}</a></td>
        <td>${escapeHtml(r.section_top ?? "—")}</td>
        <td class="num">${fmt.int(r.hits)}</td>
        <td class="num">${fmt.bytes(r.avg_bytes_per_hit)}</td>
        <td class="num">${fmt.bytes(r.total_bytes)}</td>
      </tr>`;
  }).join("");
  drillRow.querySelector(".drill-content").innerHTML =
    `<div class="drill-meta">URLs con <strong>${statusClass}</strong> · bot <strong>Googlebot</strong> · top ${rows.length}</div>` +
    `<table class="drill-table"><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

// Si el user cambia filter o fechas, las drills cacheadas dejan de aplicar.
function _invalidateDrillCache() {
  for (const k of Object.keys(_drillCache)) delete _drillCache[k];
}

// ============================================================
//  BOTS — Loaders
// ============================================================
async function loadBotMetrics() {
  renderBotKPIsSkeleton();
  const m = await fetchBotJSON(`/metrics/${state.botFilter}`);
  renderBotKPIs(m.data);
}

async function loadBotTrend() {
  setBotTrendLoading(true);
  try {
    const t = await fetchBotJSON(`/trend/${state.botFilter}`, { granularity: state.botGranularity });
    renderBotTrend(t.data.points);
  } finally {
    setBotTrendLoading(false);
  }
}

async function loadBotTables() {
  renderTableSkeleton("bot-top-urls-table",   { cols: 7 });
  renderTableSkeleton("bot-top-bots-table",   { cols: 7 });
  renderTableSkeleton("bot-by-section-table", { cols: 6 });
  renderTableSkeleton("bot-by-status-table",  { cols: 4 });

  const [topUrls, topBots, bySection, byStatus] = await Promise.all([
    fetchBotJSON(`/tables/top-urls/${state.botFilter}`,    { limit: 20 }),
    fetchBotJSON(`/tables/top-bots/${state.botFilter}`,    { limit: 20 }),
    fetchBotJSON(`/tables/by-section/${state.botFilter}`),
    fetchBotJSON(`/tables/by-status/${state.botFilter}`),
  ]);
  renderBotTopUrlsTable(topUrls.data.rows);
  renderBotTopBotsTable(topBots.data.rows);
  renderBotBySectionTable(bySection.data.rows);
  renderBotByStatusTable(byStatus.data.rows);
}

async function loadAllBots() {
  setLoading(true);
  try {
    await Promise.all([loadBotMetrics(), loadBotTrend(), loadBotTables()]);
  } catch (e) {
    console.error(e);
    alert("Error: " + e.message);
  } finally {
    setLoading(false);
  }
}

async function reloadBotsByDate() {
  _invalidateDrillCache();
  setLoading(true);
  try { await Promise.all([loadBotMetrics(), loadBotTables()]); }
  catch (e) { console.error(e); alert("Error: " + e.message); }
  finally { setLoading(false); }
}

async function reloadBotsByFilter() {
  _invalidateDrillCache();
  setLoading(true);
  try { await Promise.all([loadBotMetrics(), loadBotTrend(), loadBotTables()]); }
  catch (e) { console.error(e); alert("Error: " + e.message); }
  finally { setLoading(false); }
}

async function reloadBotsByGranularity() {
  setLoading(true);
  try { await loadBotTrend(); }
  catch (e) { console.error(e); alert("Error: " + e.message); }
  finally { setLoading(false); }
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
  if (state.mode === "bots") {
    reloadBotsByDate();
  } else {
    reloadByDate();
    // El modo bots queda con data stale; marcar para recarga al volver a entrar.
    state.botsLoaded = false;
  }
});

// Sub-tabs de GSC (data-filter) y de Bots (data-bot-filter) cohabitan en el DOM,
// pero estan en views distintas y solo una esta visible a la vez.
document.querySelectorAll("[data-filter]").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#gsc-view .tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.filter = btn.dataset.filter;
    reloadByFilter();
  });
});

document.querySelectorAll("[data-bot-filter]").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#bots-view .tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.botFilter = btn.dataset.botFilter;
    reloadBotsByFilter();
  });
});

document.querySelectorAll("[data-gran]").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#gsc-view .seg-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.granularity = btn.dataset.gran;
    reloadByGranularity();
  });
});

document.querySelectorAll("[data-bot-gran]").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#bots-view .seg-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.botGranularity = btn.dataset.botGran;
    reloadBotsByGranularity();
  });
});

// Switcher GSC / Bots — toggle de views, lazy load la primera vez que se entra a bots.
// Cada modo tiene un data lag distinto. GSC reescribe datos hasta ~3 dias atras;
// los logs Apache son inmutables y la pipeline tiene 1 dia de lag.
// Si el user no toco fechas manualmente, ajustamos al lag del modo activo
// para caer sobre el rango prewarmeado en cada API.
const DEFAULT_LAG = { gsc: 3, bots: 1 };

function setDefaultRangeForMode(mode) {
  const lag = DEFAULT_LAG[mode] ?? 1;
  const defaultTo = daysAgo(lag);
  const defaultFrom = daysAgo(lag + 29);
  state.from = defaultFrom;
  state.to = defaultTo;
  document.getElementById("from").value = defaultFrom;
  document.getElementById("to").value = defaultTo;
}

document.querySelectorAll(".mode").forEach((btn) => {
  btn.addEventListener("click", () => {
    const newMode = btn.dataset.mode;
    if (newMode === state.mode) return;
    state.mode = newMode;
    document.querySelectorAll(".mode").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("gsc-view").classList.toggle("hidden", newMode !== "gsc");
    document.getElementById("bots-view").classList.toggle("hidden", newMode !== "bots");
    document.getElementById("footer-source").textContent =
      newMode === "bots" ? "endado.com · logs Apache (bots)" : "endado.com · datos GSC";

    // Ajusta fechas al lag del modo nuevo asi siempre caemos en el rango
    // prewarmeado (sub-segundo). Si el user las toco manualmente despues
    // del init, igual las sobreescribimos — es el costo de no romper el cache hit.
    setDefaultRangeForMode(newMode);

    if (newMode === "bots" && !state.botsLoaded) {
      state.botsLoaded = true;
      loadAllBots();
    } else if (newMode === "bots") {
      reloadBotsByDate();
    } else {
      reloadByDate();
    }
  });
});

// ============================================================
//  Init — arranca en modo GSC con su default lag
// ============================================================
(function init() {
  setDefaultRangeForMode(state.mode);
  loadAll();
})();
