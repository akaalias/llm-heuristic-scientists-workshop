// ============================================================================
// Small helpers shared by the hand-rolled inline-SVG charts (dashboard.js and
// lineage.js). Loaded as a plain script before them, so these become globals.
// Keep this dependency-free.
// ============================================================================

// "nice" axis ticks: round the raw step up to a 1/2/5/10 multiple of its
// magnitude, then walk from the first multiple at or above min to max.
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

// integer → "3", float → one decimal "3.4"
const fmt = (t) => Number.isInteger(t) ? String(t) : t.toFixed(1);

// escape for HTML/SVG text and attributes (quotes included, so it is safe in both)
const esc = (s) => String(s == null ? "" : s)
  .replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

// SVG path for an "×" mark centred at (x, y) with arm radius r
const xMark = (x, y, r) => `M${x-r},${y-r}L${x+r},${y+r}M${x+r},${y-r}L${x-r},${y+r}`;
