# data_visualization

Single-script tool that turns an igniter test event log into one interactive
HTML visualization for aerospace ground tests.

## Running it

Always run through uv, not bare `python`/`python3` (the system interpreter has
no packages installed):

```
uv run main.py "test data/ALL_events.csv" -o igniter_sequence.html
```

Add dependencies with `uv add <package>`, never edit `pyproject.toml`
dependencies by hand.

## Input format

`ALL_events.csv` is a real sample export. The other `test data/*_events.csv`
files are synthetic, generated to exercise scenarios the real sample
doesn't: `second_test_events.csv` (an ordinary cycle with different
peak values/fault count), `no_end_state_events.csv` (`STATE_FIRE` triggers
but `STATE_IDLE` never rises again — tests `find_window()`'s fallback),
`no_faults_events.csv` (zero `FAULT_*` signals), and `multi_fire_events.csv`
(three separate `STATE_FIRE`/`STATE_IDLE` cycles — only the first is ever
plotted; see "Known limitations" below). All are headerless, long-format
OPC-UA change logs, one row per value change:

```
timestamp,type,name,node_id,value
```

- `type` is `LocalVariable` (state/fault/IO/measurement signals) or
  `LocalEvent` (a momentary trigger, e.g. `FIRE`, value always `Undefined`).
- `value` is `True`/`False` for bools, numeric for measurements.
- Signals are identified by `name` alone (the `node_id` is just the OPC-UA
  source id and isn't used).

## How it works

- `load()` groups the log by `name` and classifies each signal: all-bool
  values → bool signal, `LocalEvent` type → momentary trigger, else numeric.
  It also returns the log's overall last timestamp (`log_end`), used only as
  `find_window()`'s fallback — see below.
- The plotted window is derived automatically: it runs from the rising edge
  of `--start-state` (default `STATE_FIRE`) to the next rising edge of
  `--end-state` (default `STATE_IDLE`) after it, rezeroed so the start is
  t = 0. Numeric signals are clipped to that window before plotting. If
  `--end-state` never rises again after start (log cuts off mid-burn, e.g.
  `no_end_state_events.csv`), the window instead ends at `log_end` — the
  last timestamp anywhere in the log. It deliberately does *not* fall back
  to end_state's own last logged change: that's often a change from
  *before* start (e.g. its initial False transition on the way into the
  burn), which produced a negative-duration window until this was caught.
- **Known limitation**: `find_window()` only ever finds the *first*
  `--start-state`/`--end-state` pair. A log with multiple fire cycles (e.g.
  `multi_fire_events.csv`) only ever plots the first one — later cycles are
  silently clipped away, not an error. Not yet addressed; if asked for, the
  likely design is overlaying all cycles rezeroed to their own t=0,
  color/legend-grouped by cycle number, rather than one shared window.
- Exactly two numeric graphs are drawn, stacked with a shared x-axis:
  Pressure and Temperature (one line per signal, e.g. `Pressure Feed`,
  `Temp Ox` — see `PANELS`). Each line is independently toggleable via the
  legend.
- The chart itself carries no event markup inside the data area (no vlines,
  no bars) — just the two clean numeric graphs plus a row of ticks at each
  one's bottom edge (see below). `event_markers()` computes specific
  instants: `--start-state` and `--end-state` turning True, every name in
  `IO_SIGNALS` turning True (currently `Igniter PURGE`, `EXCITER NEG`,
  `EXCITER POS`), and every `FAULT_*` turning True. Durations and
  off-instants are not tracked — only the on-instant matters here.
  `LocalEvent` triggers (e.g. `FIRE`, parsed by `load()` but unused here)
  are deliberately not marked — `STATE_FIRE` already captures essentially
  the same instant, so including both was redundant clutter.
  `IO_SIGNALS` is matched by exact name, not a prefix like `FAULT_*`, since
  these are specific signals asked for one at a time rather than a whole
  naming convention — a name missing from a given log (e.g. `EXCITER POS`,
  which doesn't exist in `ALL_events.csv`) is just silently skipped, not an
  error. `Igniter FUEL`/`Igniter OX` exist in the log with the same shape
  but aren't in the list unless asked.
- `event_markers()` works on the raw (unclipped) series and only bounds the
  upper end (`t <= end`), not the lower end. This is deliberate: OPC-UA
  change-logs only record transitions, so a state flip can land a few ms
  before `start` itself — clipping that edge away would silently drop a
  real event.
- `cluster_events()` groups events within `EVENT_CLUSTER_S` (0.05s) of each
  other into one entry (e.g. the ALL_events.csv sample has one cluster of
  ~20 faults tripping within microseconds, near the end of the window) —
  `main()` turns each cluster into a `{t, labels, color}` `event_groups`
  list, used two ways:
  - Embedded in the page as `EVENTS` and rendered into a sidebar "Events"
    list (next to "Points of interest"). Clicking an entry zooms the chart
    to that moment, same interaction as a point of interest.
  - Drawn in `build_figure()` as small triangle ticks pinned to y=0 on a
    hidden secondary y-axis (`specs=[{"secondary_y": True}, ...]`) added to
    *every* panel, so each graph gets its own row of ticks right at its
    bottom edge rather than one shared lane.
- Output is a self-contained HTML file (Plotly embedded by default; `--cdn`
  to load it from a CDN instead). In the browser: click a legend entry to
  show/hide a numeric signal, double-click to isolate it, drag to zoom,
  hover a tick or click an Events entry to see/jump to that moment, click a
  point on the graph to mark it as a point of interest (stored in
  `localStorage`, exportable to CSV). A `?` button fixed top-right opens a
  modal with the same instructions in one place (closes via its ✕, clicking
  outside it, or Escape) — plain HTML/CSS/JS, no Plotly involvement.
- `--numeric-only` skips `event_markers()` entirely (empty Events list and
  no ticks), useful for checking the numeric panels in isolation.

## Conventions

- Keep it a single-file script — no new modules/packages unless the script
  genuinely outgrows one file.
- Panel classification (`PANELS`) is regex/name-based by design; prefer
  extending those patterns over adding special cases elsewhere.
- `test data/` holds real sample logs for manual testing — don't commit
  generated output HTML files.
