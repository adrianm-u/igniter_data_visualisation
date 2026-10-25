# data_visualization

Turns an igniter ground-test event log into a single interactive HTML
visualization: pressure and temperature plotted over the firing sequence,
with state/fault/IO events called out alongside the data.

## Setup

Requires [uv](https://docs.astral.sh/uv/). From the project root:

```
uv sync
```

## Usage

```
uv run main.py "path to csv file" -o igniter_sequence.html
```
Example with test data:
```
uv run main.py "test data/ALL_events.csv" -o igniter_sequence.html
```

Open the resulting `igniter_sequence.html` in a browser. No server needed —
it's a self-contained file (Plotly is embedded in it).

### Options

| Flag | Default | Meaning |
|---|---|---|
| `-o`, `--out` | `igniter_sequence.html` | Output HTML path |
| `--title` | `Igniter sequence` | Page/plot title |
| `--start-state` | `STATE_FIRE` | Bool signal whose rising edge starts the plotted window |
| `--end-state` | `STATE_IDLE` | Bool signal whose next rising edge (after start) ends the window |
| `--numeric-only` | off | Drop the events sidebar list and ticks, show just the two graphs |

Example — just the two graphs, no events sidebar/ticks:

```
uv run main.py "test data/ALL_events.csv" --numeric-only -o numeric_only.html
```

## Input format

A headerless CSV log of OPC-UA style change events, one row per value
change:

```
timestamp,type,name,node_id,value
```

- `type` is `LocalVariable` (state/fault/IO/measurement signals) or
  `LocalEvent` (a momentary trigger, e.g. `FIRE`).
- `value` is `True`/`False` for bools, numeric for measurements.
- Only `timestamp`, `name`, and `value` are used — `type` picks the signal
  category and `node_id` is ignored.

`test data/ALL_events.csv` is a real sample log; the other `test data/*_events`
files are category-filtered subsets of the same export (state/fault/IO/
measurements separately).

## What the page shows

- **Two graphs, Pressure and Temperature** — one line per signal (e.g.
  `Pressure Feed`, `Temp Ox`), windowed to the firing sequence and rezeroed
  so the window start is t = 0. Click a legend entry to show/hide a line,
  double-click to isolate it. Drag to zoom, double-click to reset.
- **A row of ticks at the bottom of each graph** marking when a state, IO,
  or fault signal turned True (e.g. `STATE_FIRE`, `Igniter PURGE`, any
  `FAULT_*`). Hover a tick to see what happened there — several events
  landing within the same instant (e.g. a cascade of faults) share one
  tick and list everything that tripped.
- **An "Events" sidebar list** — the same events as the ticks, as a
  clickable list; click one to zoom the chart to that moment.
- **Points of interest** — click any point on either graph to label it.
  Marks persist across reloads (stored in the browser) and can be exported
  to CSV.

## Project structure

- `main.py` — the whole tool (parsing, windowing, plotting, HTML/JS shell).
- `test data/` — sample igniter test logs for manual testing.
- `CLAUDE.md` — notes on how the code works, for anyone (human or AI)
  picking up this codebase.
