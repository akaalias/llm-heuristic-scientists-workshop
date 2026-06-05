(function () {
  let data = {};
  try { data = JSON.parse(document.getElementById("lineage-data").textContent || "{}"); } catch (e) {}
  const nodes = data.nodes || [];
  const host = document.getElementById("diagram");
  const tip = document.getElementById("tip");
  if (!host) return;
  if (!nodes.length) { host.innerHTML = '<p class="empty">No experiments yet — run discovery.</p>'; return; }

  // esc / fmt / xMark / niceTicks live in utils.js (loaded first).

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
  // extend the canvas to the right by ~half a viewport so the newest node can be
  // scrolled to screen-center instead of clamping against the right edge. width
  // attr stays equal to the viewBox width, so node coordinates are unchanged.
  const viewW = host.clientWidth || window.innerWidth || 1000;
  const trail = Math.max(0, Math.round(viewW / 2) - padX);
  const Wsvg = Wpx + trail;
  host.innerHTML = `<svg id="lin" width="${Wsvg}" height="${H}" viewBox="0 0 ${Wsvg} ${H}">${g}</svg>`;

  // on load, begin parked at the earliest node (far left) so the left-hand
  // history is visible, then a SINGLE smooth scroll right — the content slides
  // left — settling with the newest node centered. runs once; pinning scrollLeft
  // to 0 first also defeats the browser's scroll-restoration on reload/back-nav.
  const latestX = xFor(n - 1);
  const centerTarget = () =>
    Math.max(0, Math.min(latestX - host.clientWidth / 2, host.scrollWidth - host.clientWidth));
  host.scrollLeft = 0;
  let introRan = false;
  const intro = () => {
    if (introRan) return; introRan = true;
    // custom rAF tween instead of native smooth-scroll, which snaps at the tail.
    // easeInOutCubic: gentle start, long soft deceleration into the final frame.
    const start = host.scrollLeft, dist = centerTarget() - start, dur = 1150;
    if (Math.abs(dist) < 1) return;
    const ease = (t) => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    const t0 = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - t0) / dur);
      host.scrollLeft = start + dist * ease(p);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };
  window.addEventListener("load", () => { host.scrollLeft = 0; setTimeout(intro, 350); });

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
  // anchor it at a FIXED height just below the axis, clamped to the viewport.
  const detailCache = {};
  let hoveringKey = null;
  async function getDetail(key){
    if (!(key in detailCache)){
      try { detailCache[key] = await fetch("detail?key=" + encodeURIComponent(key)).then(r => r.ok ? r.json() : null); }
      catch { detailCache[key] = null; }
    }
    return detailCache[key] || {};
  }
  function placePopover(nd){
    // pin to a CONSTANT height — 10px below the x-axis baseline — and center on
    // the node horizontally, independent of how long this node's label is.
    // (svg renders 1:1, but scale defensively in case CSS ever resizes it.)
    const svgRect = svg.getBoundingClientRect(), w = tip.offsetWidth;
    const sx = svgRect.width / Wsvg, sy = svgRect.height / H;
    const cx = svgRect.left + xOf[nd.key] * sx;
    const left = Math.max(8, Math.min(cx - w / 2, window.innerWidth - w - 12));
    const top = svgRect.top + baseY * sy + 10;
    tip.style.left = left + "px"; tip.style.top = top + "px";
  }
  async function showPopover(nd){
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
    placePopover(nd);
  }

  svg.addEventListener("mouseover", e => {
    const m = e.target.closest("[data-key]"); if (!m) return;
    const key = m.getAttribute("data-key"); hoveringKey = key;
    lit(key);
    const nd = byKey[key]; if (nd) showPopover(nd);
  });
  svg.addEventListener("mouseout", e => {
    const m = e.target.closest("[data-key]"); if (!m) return;
    hoveringKey = null; unlit(); tip.style.opacity = "0";
  });
  svg.addEventListener("click", e => { const m = e.target.closest("[data-key]");
    if (m) location.href = "research-dashboard.html#exp=" + encodeURIComponent(m.getAttribute("data-key")); });
})();
;(function(){const h=document.getElementById('diagram');if(!h)return;window.addEventListener('load',()=>setTimeout(()=>{
  const cw=h.clientWidth, sw=h.scrollWidth;
  const dots=document.querySelectorAll('#lin .hit');
  const lastCx=parseFloat(dots[dots.length-1].getAttribute('cx'));     // newest node x in svg coords
  const target=Math.max(0,Math.min(lastCx-cw/2, sw-cw));
  h.scrollTo({left:target, behavior:'auto'});                          // instant, to read final geometry
},900));})();
