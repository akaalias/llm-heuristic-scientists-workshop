(function () {
  let data = {};
  try { data = JSON.parse(document.getElementById("lineage-data").textContent || "{}"); } catch (e) {}
  const nodes = data.nodes || [];
  const host = document.getElementById("diagram");
  const tip = document.getElementById("tip");
  if (!host) return;
  if (!nodes.length) { host.innerHTML = '<p class="empty">No experiments yet — run discovery.</p>'; return; }

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const fmt = (t) => Number.isInteger(t) ? String(t) : t.toFixed(1);
  const xMark = (x, y, r) => `M${x-r},${y-r}L${x+r},${y+r}M${x+r},${y-r}L${x-r},${y+r}`;
  function niceTicks(min, max, c){ if (max <= min) max = min + 1;
    const raw = (max-min)/Math.max(1,c-1), mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1,2,5,10].map(m => m*mag).find(s => s >= raw); const out = [];
    for (let v = Math.ceil(min/step)*step; v <= max+1e-9; v += step) out.push(Math.round(v*1000)/1000);
    return out; }

  // adjacency
  const byKey = {}; nodes.forEach(n => byKey[n.key] = n);
  const childrenOf = {}; nodes.forEach(n => childrenOf[n.key] = []);
  const parentsOf  = {}; nodes.forEach(n => parentsOf[n.key] = (n.parents || []).filter(p => byKey[p]));
  nodes.forEach(n => parentsOf[n.key].forEach(p => childrenOf[p].push(n.key)));
  const walk = (k, nbr, acc) => { (nbr[k] || []).forEach(x => { if (!acc.has(x)) { acc.add(x); walk(x, nbr, acc); } }); return acc; };

  // full-bleed arc diagram: experiments on one baseline (discovery order),
  // fixed node spacing (so it scrolls sideways with many), semicircular arcs
  // above linking each to its parent(s).
  const n = nodes.length;
  const padX = 28, topPad = 14, CAP = 260, minSpacing = 26;
  const labelOf = (nd) => nd.symbol || ("#" + nd.n);
  const maxLabelLen = Math.max(1, ...nodes.map(nd => labelOf(nd).length));
  const labelH = Math.min(280, Math.round(maxLabelLen * 5.9) + 14);   // room for the vertical labels
  const avail = Math.max(320, (host.clientWidth || 1000) - 2 * padX - 8);
  const spacing = n > 1 ? Math.max(minSpacing, avail / (n - 1)) : 0;
  const Wpx = Math.round(2 * padX + (n - 1) * spacing);
  const xFor = (i) => padX + i * spacing;
  const xOf = {}; nodes.forEach((nd, i) => xOf[nd.key] = xFor(i));

  const edges = [];
  nodes.forEach(nd => parentsOf[nd.key].forEach(pk =>
    edges.push({ c: nd.key, p: pk, dx: Math.abs(xOf[nd.key] - xOf[pk]) })));
  const maxRy = edges.length ? Math.min(CAP, Math.max(...edges.map(e => e.dx / 2))) : 18;
  const baseY = topPad + maxRy;
  const H = Math.ceil(baseY + 12 + labelH);

  let g = "";
  // arcs above the row (capped half-ellipse for very long-range links); no baseline
  edges.forEach(e => {
    const a = Math.min(xOf[e.p], xOf[e.c]), b = Math.max(xOf[e.p], xOf[e.c]);
    const rx = (b - a) / 2, ry = Math.min(CAP, rx);
    g += `<path class="edge" data-c="${esc(e.c)}" data-p="${esc(e.p)}" `
       + `d="M${a.toFixed(1)},${baseY} A${rx.toFixed(1)},${ry.toFixed(1)} 0 0 1 ${b.toFixed(1)},${baseY}"/>`;
  });
  nodes.forEach((nd, i) => {
    const x = xFor(i).toFixed(1);
    const cls = nd.kind === "failed" ? "nf" : nd.kind === "kept" ? "nk" : "ndd";
    const r = nd.kind === "kept" ? 4 : 3;
    g += `<circle class="nd ${cls}" data-key="${esc(nd.key)}" cx="${x}" cy="${baseY}" r="${r}"/>`;
    if (nd.pivot) g += `<circle class="nd np" data-key="${esc(nd.key)}" cx="${x}" cy="${baseY}" r="6.5"/>`;
    g += `<circle class="hit" data-key="${esc(nd.key)}" cx="${x}" cy="${baseY}" r="9" fill="transparent"/>`;
    // label reads top→bottom (rotate 90, anchored just below the node)
    g += `<text class="nlab" data-key="${esc(nd.key)}" x="${x}" y="${baseY + 8}" text-anchor="start" `
       + `transform="rotate(90 ${x} ${baseY + 8})">${esc(labelOf(nd))}</text>`;
  });
  host.innerHTML = `<svg id="lin" width="${Wpx}" height="${H}" viewBox="0 0 ${Wpx} ${H}">${g}</svg>`;

  // interaction: hover a node → light its whole bloodline; click → dashboard
  const svg = document.getElementById("lin");
  const q = (k) => CSS.escape(k);
  // hover → trace the full ancestry: follow ALL parents back to the root (so a
  // node with two "derived from" parents lights both), highlighting the actual
  // parent edges walked — not every edge among the ancestors.
  function lit(key){
    const nodeSet = new Set([key]); const edgeSet = new Set();
    const stack = [key];
    while (stack.length){
      const cur = stack.pop();
      (parentsOf[cur] || []).forEach(p => {
        edgeSet.add(cur + "|" + p);
        if (!nodeSet.has(p)){ nodeSet.add(p); stack.push(p); }
      });
    }
    const direct = new Set([key, ...(parentsOf[key] || [])]);   // hovered + immediate parents
    svg.classList.add("dim");
    svg.querySelectorAll(".lit,.lit-direct").forEach(el => el.classList.remove("lit", "lit-direct"));
    nodeSet.forEach(k => svg.querySelectorAll(`[data-key="${q(k)}"]`).forEach(el =>
      el.classList.add(direct.has(k) ? "lit-direct" : "lit")));
    svg.querySelectorAll(".edge").forEach(e => {
      const c = e.getAttribute("data-c"), p = e.getAttribute("data-p");
      if (edgeSet.has(c + "|" + p)) e.classList.add(c === key ? "lit-direct" : "lit");
    });
  }
  function unlit(){ svg.classList.remove("dim"); svg.querySelectorAll(".lit,.lit-direct").forEach(el => el.classList.remove("lit", "lit-direct")); }

  // popover: lazy-load the experiment's detail (thumbnail, rule, parents) and
  // anchor it just below the hovered node, clamped to the viewport.
  const detailCache = {};
  let hoveringKey = null;
  async function getDetail(key){
    if (!(key in detailCache)){
      try { detailCache[key] = await fetch("detail?key=" + encodeURIComponent(key)).then(r => r.ok ? r.json() : null); }
      catch { detailCache[key] = null; }
    }
    return detailCache[key] || {};
  }
  function placePopover(anchorEl){
    const r = anchorEl.getBoundingClientRect(), w = tip.offsetWidth;
    const left = Math.max(8, Math.min(r.left + r.width / 2 - w / 2, window.innerWidth - w - 12));
    // always anchor below the node — never flip above, so the popover can't cover the graph
    const top = r.bottom + 12;
    tip.style.left = left + "px"; tip.style.top = top + "px";
  }
  async function showPopover(anchorEl, nd){
    const d = await getDetail(nd.key);
    if (hoveringKey !== nd.key) return;                       // moved away before it loaded
    const lat = nd.lateness != null ? `lateness ${fmt(nd.lateness)}` : "failed";
    const parents = (d.parents || []).map(p =>
      `<span class="pop-parent">↳ <span class="ps" title="${esc(p.symbol)}">${esc(p.symbol)}</span> <span class="pn">#${esc(p.n)}</span></span>`).join("");
    tip.innerHTML =
      `<div class="pop-title">${esc(nd.title)}</div>`
      + `<div class="pop-meta">#${esc(nd.n)} · ${esc(nd.symbol || "")} · ${lat}</div>`
      + `<div class="pop-grid">`
      +   (d.thumb_svg ? `<div class="pop-diagram">${d.thumb_svg}</div>` : "")
      +   `<div class="pop-body">`
      +     (nd.summary ? `<div class="pop-sec"><div class="pop-h">Priority rule</div><p>${esc(nd.summary)}</p></div>` : "")
      +     (parents ? `<div class="pop-sec"><div class="pop-h">Derived from</div><div class="pop-parents">${parents}</div></div>` : "")
      +   `</div></div>`;
    tip.style.opacity = "1";
    placePopover(anchorEl);
  }

  svg.addEventListener("mouseover", e => {
    const m = e.target.closest("[data-key]"); if (!m) return;
    const key = m.getAttribute("data-key"); hoveringKey = key;
    lit(key);
    const nd = byKey[key]; if (nd) showPopover(m, nd);
  });
  svg.addEventListener("mouseout", e => {
    const m = e.target.closest("[data-key]"); if (!m) return;
    hoveringKey = null; unlit(); tip.style.opacity = "0";
  });
  svg.addEventListener("click", e => { const m = e.target.closest("[data-key]");
    if (m) location.href = "/research-dashboard#exp=" + encodeURIComponent(m.getAttribute("data-key")); });
})();
