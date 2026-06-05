// ============================================================================
// Live updates via Server-Sent Events, plus a hand-rolled inline-SVG chart.
// Progressive enhancement: the table is server-rendered, so it shows current
// state with JS disabled; the chart + live stream are pure enhancement.
// ============================================================================

// Source for row-expand detail. null → live server (`/detail?key=…`). The static
// export (tools/export_site.py) rewrites this to a baked "details.json" map so
// the row-expand works on GitHub Pages, where there is no live endpoint.
const DETAILS_URL = "details.json";

// ---- chart geometry & helpers ---------------------------------------------
const VB = {W:1000, H:288, L:68, R:20, T:14, B:30};
const chartSeen = new Set();   // keys already drawn — so only new dots animate in

function niceNum(range, round){
  const exp = Math.floor(Math.log10(range || 1));
  const f = (range || 1) / Math.pow(10, exp);
  const nf = round ? (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10)
                   : (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10);
  return nf * Math.pow(10, exp);
}
function niceTicks(min, max, count){
  if (max <= min) max = min + 1;
  const step = niceNum((max - min) / Math.max(1, count - 1), true);
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step)
    out.push(Math.round(v * 1000) / 1000);
  return out;
}
const fmt = (t) => Number.isInteger(t) ? String(t) : t.toFixed(1);
const esc = (s) => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function xMark(x, y, r){ return `M${x-r},${y-r}L${x+r},${y+r}M${x+r},${y-r}L${x-r},${y+r}`; }

function renderChart(points, target, animate){
  const svg = document.getElementById("chart");
  if (!svg) return;
  points = points || [];
  const {W,H,L,R,T,B} = VB, x0=L, x1=W-R, y0=T, y1=H-B;

  if (!points.length){
    svg.innerHTML = `<text x="${W/2}" y="${H/2}" text-anchor="middle" class="chart-empty">`
                  + `no experiments yet</text>`;
    return;
  }

  const tgt  = (typeof target === "number") ? target : 0;
  const succ = points.filter(p => p.y != null).map(p => p.y);
  const lo   = Math.min(0, tgt);
  const hi   = Math.max(succ.length ? Math.max(...succ) : 1, tgt);
  const span = (hi - lo) || 1;
  const dmin = lo - span * 0.08;   // headroom so 0 / target / best lift off the axis
  const dmax = hi + span * 0.10;
  const n = points.length;
  const xFor = (i) => n <= 1 ? (x0 + x1) / 2 : x0 + (x1 - x0) * i / (n - 1);
  const yFor = (v) => y1 - (v - dmin) / (dmax - dmin) * (y1 - y0);

  let g = "";
  // horizontal gridlines + y labels (skip the one that coincides with the
  // target — its own dashed line + label already carries that value)
  for (const t of niceTicks(lo, hi, 4)){
    if (Math.abs(t - tgt) < span * 1e-3) continue;
    const y = yFor(t);
    g += `<line class="grid" x1="${x0}" y1="${y}" x2="${x1}" y2="${y}"/>`
       + `<text x="${x0-9}" y="${y+4}" text-anchor="end">${fmt(t)}</text>`;
  }
  // axes
  g += `<line class="axis" x1="${x0}" y1="${y0}" x2="${x0}" y2="${y1}"/>`
     + `<line class="axis" x1="${x0}" y1="${y1}" x2="${x1}" y2="${y1}"/>`;
  // (the target threshold is drawn LAST, below, so it sits on top of the
  //  best line and dots instead of being painted over by them)
  // running-best step line (carried forward across discarded/failed)
  let best = null; const bp = [];
  points.forEach((p, i) => {
    if (p.y != null) best = (best == null) ? p.y : Math.min(best, p.y);
    if (best != null) bp.push([xFor(i), yFor(best)]);
  });
  if (bp.length){
    const R = 6;   // corner radius for the step joints
    let d = `M${bp[0][0].toFixed(1)},${bp[0][1].toFixed(1)}`;
    for (let i = 1; i < bp.length; i++){
      const px = bp[i-1][0], py = bp[i-1][1], cx = bp[i][0], cy = bp[i][1];
      const dy = cy - py;
      const r = Math.min(R, (cx - px) / 2, Math.abs(dy) / 2);
      if (dy === 0 || r < 0.5){
        d += `H${cx.toFixed(1)}`;                 // flat run — no corner
        if (dy !== 0) d += `V${cy.toFixed(1)}`;
      } else {
        const sy = Math.sign(dy);                 // round the H→V joint with a quadratic fillet
        d += `H${(cx - r).toFixed(1)}`
           + `Q${cx.toFixed(1)},${py.toFixed(1)} ${cx.toFixed(1)},${(py + sy * r).toFixed(1)}`
           + `V${cy.toFixed(1)}`;
      }
    }
    g += `<path class="bestline" d="${d}"/>`;
  }
  // points: kept (ink), discarded (faint), failed (rust × pinned to the top =
  // worst). Each carries its row's data-key and a generous transparent hit
  // circle so it's easy to hover/click despite the small visible mark.
  points.forEach((p, i) => {
    const x = xFor(i);
    const y = (p.y != null) ? yFor(p.y) : y0 + 6;
    const k = esc(p.key || "");
    const appear = (animate && p.key && !chartSeen.has(p.key)) ? " appear" : "";
    if (p.kind === "failed")
      g += `<path class="pt-failed dot${appear}" data-key="${k}" d="${xMark(x, y, 3.5)}"/>`;
    else if (p.kind === "kept")
      g += `<circle class="pt-kept dot${appear}" data-key="${k}" cx="${x}" cy="${y}" r="4"/>`;
    else
      g += `<circle class="pt-disc dot${appear}" data-key="${k}" cx="${x}" cy="${y}" r="2.6"/>`;
    if (p.pivot) g += `<circle class="pt-pivot" cx="${x}" cy="${y}" r="7"/>`;   // pivot ring
    g += `<circle class="hit" data-key="${k}" cx="${x}" cy="${y}" r="10" fill="transparent"/>`;
  });
  // remember what we've drawn, so only genuinely-new dots animate next time
  points.forEach(p => { if (p.key) chartSeen.add(p.key); });
  // axis captions
  const ymid = (y0 + y1) / 2;
  g += `<text class="xlab" x="${(x0+x1)/2}" y="${H-7}" text-anchor="middle">experiment →</text>`
     + `<text class="xlab" x="16" y="${ymid}" text-anchor="middle" `
     + `transform="rotate(-90 16 ${ymid})">total lateness</text>`;

  // target threshold — line drawn LAST so it sits on top of the best line and
  // dots; its label sits to the LEFT of the y-axis (like a tick label at the
  // target's level) so it never covers recent points near the right edge.
  const yt = yFor(tgt);
  g += `<line class="target" x1="${x0}" y1="${yt}" x2="${x1}" y2="${yt}"/>`
     + `<text class="tlab" x="${x0 - 9}" y="${yt + 4}" text-anchor="end">target ${fmt(tgt)}</text>`;

  svg.innerHTML = g;
}

// ---- live wiring + interaction --------------------------------------------
(function () {
  const chart = document.getElementById("chart");
  const tbody = document.getElementById("rows");
  const sub   = document.querySelector(".sub");
  const upd   = document.getElementById("updated");
  const live  = document.getElementById("live");

  // cross-highlight + sticky selection between a chart dot and its table row.
  let selectedKey = null;
  const rowEl = (k) => tbody && tbody.querySelector(`tr[data-key="${CSS.escape(k)}"]`);
  const dotEl = (k) => chart && chart.querySelector(`.dot[data-key="${CSS.escape(k)}"]`);

  function setHover(k, on){
    const r = rowEl(k); if (r) r.classList.toggle("row-hi", on);
    const d = dotEl(k); if (d) d.classList.toggle("hi", on);
  }
  function applySelection(){     // re-assert the sticky selection after a re-render
    chart && chart.querySelectorAll(".dot.sel").forEach(d => d.classList.remove("sel"));
    tbody && tbody.querySelectorAll(".row-selected").forEach(r => r.classList.remove("row-selected"));
    if (!selectedKey) return;
    const d = dotEl(selectedKey); if (d) d.classList.add("sel");
    const r = rowEl(selectedKey); if (r) r.classList.add("row-selected");
  }
  function select(k){
    selectedKey = k;
    applySelection();
    const r = rowEl(k);
    if (r) r.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  if (chart){
    chart.addEventListener("mouseover", e => { const m = e.target.closest("[data-key]"); if (m) setHover(m.dataset.key, true);  });
    chart.addEventListener("mouseout",  e => { const m = e.target.closest("[data-key]"); if (m) setHover(m.dataset.key, false); });
    chart.addEventListener("click",     e => { const m = e.target.closest("[data-key]"); if (m) select(m.dataset.key); });
  }

  // popover over schedule (Gantt) bars — anchored centred above the hovered bar
  const gtip = document.getElementById("gtip");
  if (gtip){
    document.addEventListener("mouseover", e => {
      const bar = e.target.closest && e.target.closest("svg.gantt rect[data-tip]");
      if (!bar) return;
      const r = bar.getBoundingClientRect();
      gtip.textContent = bar.getAttribute("data-tip");
      gtip.style.left = (r.left + r.width / 2) + "px";
      gtip.style.top  = (r.top - 8) + "px";        // sits just above the bar
      gtip.style.opacity = "1";
    });
    document.addEventListener("mouseout", e => {
      if (e.target.closest && e.target.closest("svg.gantt rect[data-tip]")) gtip.style.opacity = "0";
    });
  }

  // ---- click-to-expand experiment detail ----------------------------------
  const detailCache = {};       // key → built HTML (null = fetch in flight)
  let expandedKey = null;

  function buildDetail(d){
    const failed = (d.status || "").startsWith("failed");
    const time = (d.time || "").includes("T") ? d.time.split("T")[1] : (d.time || "—");
    const lateness = failed ? "failed" : (d.lateness || "—");

    const lineage = (d.parents && d.parents.length)
      ? (d.parents || []).map(p =>
          `<span class="d-parent" data-key="${esc(p.key)}" title="${esc(p.title)}">`
          + `↳ ${esc(p.symbol)} <span class="pn">#${esc(p.n)}</span></span>`).join("")
      : `<div class="d-genesis">genesis — no parent</div>`;

    // run-level CLI inputs — what this experiment actually ran with
    const p = d.params || {};
    const paramRows = [
      ["model",       p.model],
      ["api",         p.api || "Hugging Face"],
      ["iterations",  p.iterations],
      ["patience",    p.patience],
      ["meta-pivots", p.meta_pivots],
      ["library",     p.library || "none"],
    ];
    const params =
      `<div class="d-params"><div class="d-lineage-h">Run parameters</div>`
      + paramRows.map(([k, v]) =>
          `<div class="d-fact"><span class="d-k">${esc(k)}</span>`
          + `<span class="d-v">${esc(v || "—")}</span></div>`).join("")
      + `</div>`;

    const meta =
      `<aside class="d-meta">`
      + `<div class="d-symbol">${esc(d.symbol)}</div>`
      + `<div class="d-facts">`
      +   `<div class="d-fact"><span class="d-k">Experiment</span><span class="d-v">#${esc(d.n)}</span></div>`
      +   `<div class="d-fact"><span class="d-k">Lateness</span><span class="d-v${failed ? " bad" : ""}">${esc(lateness)}</span></div>`
      +   `<div class="d-fact"><span class="d-k">Time</span><span class="d-v">${esc(time)}</span></div>`
      + `</div>`
      + params
      + `<div class="d-lineage"><div class="d-lineage-h">Derived from</div>${lineage}</div>`
      + `</aside>`;

    // every battery sample's schedule, listed vertically below the code
    const scheds = (d.schedules && d.schedules.length)
      ? `<div class="d-code-h d-sched-h">Schedules across the battery</div>`
        + `<div class="d-cap">colour = order · number = dish · dotted = arrival · dashed = due</div>`
        + `<div class="d-scheds">`
        + d.schedules.map(s =>
            `<div class="d-sched">`
            + `<div class="d-sched-cap"><span class="d-sched-name">${esc(s.title || s.name || "sample")}</span>`
            +   (s.title && s.name && s.title !== s.name ? `<span class="d-sched-id">${esc(s.name)}</span>` : "")
            +   `<span class="d-sched-lat">lateness ${esc(s.lateness)}</span></div>`
            + s.svg                                                    // already HTML (inline SVG)
            + `</div>`).join("")
        + `</div>`
      : "";

    const main =
      `<div class="d-main">`
      + `<h3 class="d-title">${esc(d.title)}</h3>`
      + (d.summary
          ? `<div class="d-rule-h">Priority rule</div><p class="d-summary">${esc(d.summary)}</p>`
          : "")
      + (d.code_html
          ? `<div class="d-code-h">Heuristic</div><pre class="code">${d.code_html}</pre>`  // already HTML
          : `<p class="d-loading">no code saved</p>`)
      + scheds
      + `</div>`;

    return `<div class="detail-inner">${meta}${main}</div>`;
  }
  function collapseDetail(){
    document.querySelectorAll("#rows tr.detail-row").forEach(d => d.remove());
    document.querySelectorAll("#rows tr.expanded").forEach(r => r.classList.remove("expanded"));
  }
  function insertDetail(key){
    collapseDetail();
    const row = rowEl(key);
    if (!row) return;
    row.classList.add("expanded");
    const n = document.querySelectorAll("table thead th").length || 7;
    const tr = document.createElement("tr");
    tr.className = "detail-row";
    tr.innerHTML = `<td colspan="${n}">${detailCache[key]
        || '<div class="detail-inner"><p class="d-loading">loading…</p></div>'}</td>`;
    row.after(tr);
  }
  // static mode (GitHub Pages): one baked JSON map of every experiment's detail,
  // fetched once and cached, instead of a per-key call to the live server.
  let staticDetails = null;
  function loadStaticDetails(){
    if (!staticDetails)
      staticDetails = fetch(DETAILS_URL).then(r => r.ok ? r.json() : {}).catch(() => ({}));
    return staticDetails;
  }
  const NOT_FOUND = '<div class="detail-inner"><p class="d-loading">not found</p></div>';

  async function openDetail(key, scroll){
    expandedKey = key;
    insertDetail(key);
    if (scroll){ const r = rowEl(key); if (r) r.scrollIntoView({ behavior: "smooth", block: "center" }); }
    if (detailCache[key] === undefined){
      detailCache[key] = null;   // in flight — don't double-fetch
      try {
        if (DETAILS_URL){
          const d = (await loadStaticDetails())[key];
          detailCache[key] = d ? buildDetail(d) : NOT_FOUND;
        } else {
          const res = await fetch("detail?key=" + encodeURIComponent(key));
          detailCache[key] = res.ok ? buildDetail(await res.json()) : NOT_FOUND;
        }
      } catch (e) {
        detailCache[key] = '<div class="detail-inner"><p class="d-loading">failed to load</p></div>';
      }
      if (expandedKey === key) insertDetail(key);   // swap loading → content if still open
    }
  }
  function toggleDetail(key){
    if (expandedKey === key){ expandedKey = null; collapseDetail(); return; }
    openDetail(key, false);
  }
  function reapplyExpansion(){ if (expandedKey) insertDetail(expandedKey); }

  if (tbody){
    tbody.addEventListener("click", e => {
      const pb = e.target.closest(".d-parent");
      if (pb && pb.dataset.key){ openDetail(pb.dataset.key, true); return; }
      const tr = e.target.closest("tr.row");
      if (tr && tr.dataset.key) toggleDetail(tr.dataset.key);
    });
  }

  // initial render from the data embedded by the server (works even if SSE fails)
  let init = {};
  try { init = JSON.parse(document.getElementById("chart-data").textContent || "{}"); } catch (e) {}
  renderChart(init.points, init.target, false);   // seed existing dots without animating
  applySelection();

  // deep link from the schedule grid: /#exp=<key> opens that experiment
  const deep = location.hash.match(/exp=([^&]+)/);
  if (deep) { try { openDetail(decodeURIComponent(deep[1]), true); } catch (e) {} }

  // live stream
  if (!tbody || !window.EventSource) return;
  const keysOf = () =>
    new Set([...tbody.querySelectorAll("tr[data-key]")].map(r => r.dataset.key));
  let known = keysOf();

  const es = new EventSource("events");
  es.onopen  = () => { live.textContent = "live";          live.className = "on";  };
  es.onerror = () => { live.textContent = "reconnecting…"; live.className = "off"; };
  es.onmessage = (e) => {
    const d = JSON.parse(e.data);
    tbody.innerHTML = d.rows;
    if (sub) sub.textContent = d.sub;
    if (upd) upd.textContent = d.updated;
    renderChart(d.chart, d.target, true);   // animate only the newly-arrived dots
    // flash only rows we hadn't seen before (the new experiments)
    tbody.querySelectorAll("tr[data-key]").forEach(r => {
      if (!known.has(r.dataset.key)) r.classList.add("row-new");
    });
    known = keysOf();
    applySelection();      // the live re-render wiped class state — re-apply selection
    reapplyExpansion();    // …and re-open the detail panel if one was expanded
  };
})();
