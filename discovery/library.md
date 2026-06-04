# Scheduling heuristics — research & inspiration library

Notes to broaden the search for `priority(step, state)` rules. These are
*inspiration*, not requirements: borrow an idea, combine two, or invert one.
The objective is to minimise total lateness (sum of per-order tardiness) across
a battery of order sets, under limited station capacity.

## Classic dispatch rules (single machine / job shop)

- **EDD — Earliest Due Date.** Schedule the most urgent due time first.
  Provably minimises *maximum* lateness on one machine, but can do poorly on
  *total* tardiness when work content varies.
- **SPT / LPT — Shortest / Longest Processing Time.** SPT minimises mean flow
  time and tends to reduce the number of late jobs; LPT helps pack long jobs
  early so they don't end up on the critical path.
- **MST — Minimum Slack Time.** slack = due − now − (remaining work). Pick the
  smallest slack. Reacts to how much work is left, not just the deadline.
- **CR — Critical Ratio.** (due − now) / (remaining work). < 1 means already
  behind; pick the smallest. A normalised urgency-vs-work signal.
- **ATC — Apparent Tardiness Cost** (Vepsalainen & Morton). Combines WSPT with
  an exponential slack term: priority ∝ (1/p)·exp(−max(0, slack)/(k·p̄)),
  where p is processing time, p̄ the average, k a look-ahead constant (~1–3).
  Strong general-purpose weighted-tardiness rule; the exponential makes urgency
  ramp up smoothly as slack shrinks rather than flipping at a hard threshold.
- **COVERT — Cost Over Time.** Ratio of expected tardiness cost to processing
  time; prioritise jobs whose delay is about to start costing.

## Bottleneck / congestion thinking

- **Critical path / longest remaining chain.** Favour steps on the order's
  longest dependency chain — they set the order's earliest possible finish, so
  delaying them delays the whole order.
- **Shifting Bottleneck (Adams, Balas & Zawack).** Identify the most loaded
  station, schedule it well first, then propagate. Inspiration: weight a step
  by how contended *its* station is, and protect the bottleneck station's time.
- **Bottleneck dynamics / resource pricing.** Treat scarce station-time as
  having a "price"; a step's priority reflects the value it creates minus the
  congestion cost it imposes on others competing for the same station.

## Lookahead & combinations

- Blend a **deadline-urgency** term with a **station-congestion** term so the
  rule defers a step when its station is about to be swamped by more urgent work.
- **Two-tier tie-breaking:** rank by urgency, break ties by remaining work (or
  vice-versa) — many rules collapse to the same schedule unless ties are broken
  deliberately.
- Consider the **order-level** view, not just the step: an order finishes only
  when its *last* dish finishes, so racing one dish ahead while another lags
  wastes effort. Balance progress across an order's dishes.

## Ideas worth trying that aren't pure dispatch rules

- **Slack distributed over remaining steps**, so each step "owes" a share of the
  order's urgency rather than all of it landing on the first step.
- **Penalise starting a long step late** when little slack remains (a late start
  on a long chain is unrecoverable).
- **Earliest-finish-first**: prefer steps/orders that can be *completely* finished
  soonest, clearing them out and freeing stations.
