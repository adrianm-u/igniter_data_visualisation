"""Combine an igniter test event log into one interactive HTML visualization.

Usage:
    python main.py "test data/ALL_events.csv" -o igniter_sequence.html

The log is a headerless CSV of OPC-UA style change events, one row per
value change:

    timestamp,type,name,node_id,value

`type` is "LocalVariable" (state/fault/IO/measurement signals) or
"LocalEvent" (a momentary trigger, e.g. FIRE). `value` is "True"/"False"
for bools, "Undefined" for LocalEvent triggers, or numeric otherwise.

The plotted window runs from the rising edge of --start-state (default
STATE_FIRE) to the next rising edge of --end-state (default STATE_IDLE)
after it, rezeroed so the start is t = 0.

Two numeric graphs are drawn, Pressure and Temperature (one line per
signal, e.g. "Pressure Feed", "Temp Ox"). Instantaneous events are listed in
a sidebar "Events" list, and also marked as small triangle ticks right at
the bottom edge of each graph (not drawn across the data itself):
  * --start-state and --end-state turning True (e.g. STATE_FIRE, STATE_IDLE)
  * every signal in IO_SIGNALS turning True (e.g. Igniter PURGE, EXCITER NEG)
  * every FAULT_* signal turning True
LocalEvent triggers (e.g. FIRE) are parsed but not marked — STATE_FIRE
already captures that same instant. Only the on-instant matters for any of
this; off-instants and on-durations aren't shown. Events within
EVENT_CLUSTER_S of each other are grouped into one entry (e.g. a dozen
faults tripping within microseconds of each other), shared by both the
sidebar list and the ticks.

In the page: click a legend entry to show/hide a signal (double-click to show
only that one), drag to zoom, double-click the plot to reset. Click an Events
entry to zoom to that moment. Click any point on the graph to mark it as a
point of interest; points of interest can be exported.
"""
import argparse
import json
import re
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

PLOTLY_CDN = "https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.35.2/plotly.min.js"

# Numeric panels, matched against the signal name in this order
PANELS = [
    ("Pressure", re.compile(r"pressure", re.I)),
    ("Temperature", re.compile(r"temp", re.I)),
]

# Categorical colors, assigned in fixed order per signal (never by rank)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
GROUP_COLORS = {"State": "#2a78d6", "Fault": "#e34948", "IO": "#1baf7a"}

# Specific bool signals (besides STATE_*/FAULT_*) worth marking turning True.
# Listed by exact name, not pattern, so a name missing from the log is just skipped.
IO_SIGNALS = ["Igniter PURGE", "EXCITER NEG", "EXCITER POS"]


def load(path):
    """Read the event log and split signals into numeric, bool, and momentary triggers."""
    df = pd.read_csv(path, header=None, names=["timestamp", "type", "name", "node_id", "value"])
    df["t"] = pd.to_datetime(df["timestamp"])

    numeric, bools, triggers = {}, {}, {}
    for name, rows in df.groupby("name", sort=False):
        rows = rows.sort_values("t")
        if (rows["type"] == "LocalEvent").all():
            triggers[name] = rows["t"]
            continue
        values = rows["value"].astype(str).str.lower()
        if set(values.unique()) <= {"true", "false"}:
            bools[name] = (rows["t"], values.eq("true"))
        else:
            numeric[name] = (rows["t"], pd.to_numeric(rows["value"], errors="coerce"))
    return numeric, bools, triggers, df["t"].max()


def rising_edges(t, on):
    """Timestamps where `on` switches (or starts) True."""
    edge = on & ~on.shift(fill_value=False)
    return t[edge]


def find_window(bools, start_state, end_state, log_end):
    """log_end: the last timestamp anywhere in the log — the fallback window end when
    end_state never rises again after start (e.g. the log just stops mid-burn with no
    recorded recovery). Falling back to end_state's own last logged change instead would
    be wrong here: that's often a change from *before* start (e.g. its initial False
    transition), which would produce a negative-duration window.
    """
    starts = rising_edges(*bools[start_state])
    if starts.empty:
        raise ValueError(f"{start_state} is never True in this log")
    start = starts.iloc[0]

    ends = rising_edges(*bools[end_state])
    ends = ends[ends > start]
    end = ends.iloc[0] if not ends.empty else log_end
    return start, end


def clip(t, values, start, end):
    """Keep only [start, end], rezeroed to seconds since start."""
    mask = (t >= start) & (t <= end)
    return (t[mask] - start).dt.total_seconds(), values[mask]


def event_markers(bools, start, end, start_state, end_state):
    """Instantaneous events, rezeroed to seconds since `start`: start_state and end_state
    turning True, every name in IO_SIGNALS turning True, and every FAULT_* turning True.
    Operates on the raw (unclipped) series and only bounds the upper end — a transition
    can land a few ms before `start` itself, and that's worth keeping visible.
    """
    def rel(timestamps):
        return [float((ti - start).total_seconds()) for ti in timestamps if ti <= end]

    events = []
    for state in (start_state, end_state):
        if state in bools:
            t, on = bools[state]
            events += [{"t": ti, "label": state, "color": GROUP_COLORS["State"]}
                       for ti in rel(rising_edges(t, on))]
    for name in IO_SIGNALS:
        if name in bools:
            t, on = bools[name]
            events += [{"t": ti, "label": name, "color": GROUP_COLORS["IO"]}
                       for ti in rel(rising_edges(t, on))]
    for name, (t, on) in bools.items():
        if name.upper().startswith("FAULT"):
            events += [{"t": ti, "label": name, "color": GROUP_COLORS["Fault"]}
                       for ti in rel(rising_edges(t, on))]
    return events


EVENT_CLUSTER_S = 0.05  # events within this many seconds of each other share one sidebar entry


def cluster_events(events):
    """Group events within EVENT_CLUSTER_S of each other so they share one sidebar list
    entry instead of flooding the list with near-duplicate timestamps."""
    ordered = sorted(events, key=lambda e: e["t"])
    clusters = []
    for e in ordered:
        if clusters and e["t"] - clusters[-1][-1]["t"] <= EVENT_CLUSTER_S:
            clusters[-1].append(e)
        else:
            clusters.append([e])
    return clusters


def build_figure(numeric, event_groups, title):
    panels = []
    for name, pattern in PANELS:
        cols = [c for c in numeric if pattern.search(c) and not any(c in p[1] for p in panels)]
        if cols:
            panels.append((name, cols))

    rows = len(panels)
    # Every panel gets a hidden secondary y-axis fixed to [0, 1] purely to host the event
    # ticks pinned at y=0 — i.e. right at the bottom edge of that panel's own box, not
    # inside the Pressure/Temperature data area.
    specs = [[{"secondary_y": True}] for _ in range(rows)] if event_groups else None

    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True,
                        vertical_spacing=0.03, row_heights=[1 / rows] * rows, specs=specs)

    for row, (panel, cols) in enumerate(panels, start=1):
        for i, col in enumerate(cols):
            t, y = numeric[col]
            fig.add_trace(go.Scattergl(
                x=t, y=y, name=col, mode="lines",
                line=dict(width=2, color=SERIES[i % len(SERIES)]),
                legendgroup=panel, legendgrouptitle_text=panel,
                hovertemplate="%{y:.2f}<extra>" + col + "</extra>",
            ), row=row, col=1)
        fig.update_yaxes(title_text=panel, row=row, col=1, secondary_y=False)
    fig.update_xaxes(title_text="Time [s]", row=rows, col=1)

    if event_groups:
        xs = [e["t"] for e in event_groups]
        colors = [e["color"] for e in event_groups]
        texts = [", ".join(e["labels"]) for e in event_groups]
        for row in range(1, rows + 1):
            fig.add_trace(go.Scatter(
                x=xs, y=[0] * len(xs), mode="markers",
                marker=dict(symbol="triangle-up", size=8, color=colors, line=dict(width=0)),
                hovertext=texts, hovertemplate="%{hovertext}<extra></extra>",
                showlegend=False,
            ), row=row, col=1, secondary_y=True)
            fig.update_yaxes(visible=False, range=[0, 1], fixedrange=True, row=row, col=1, secondary_y=True)

    fig.update_layout(
        title=title, hovermode="x unified", dragmode="zoom",
        legend=dict(groupclick="toggleitem", tracegroupgap=16),
        margin=dict(l=70, r=20, t=60, b=50), template="plotly_white",
        font=dict(family="system-ui, sans-serif", size=12),
    )
    return fig


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
__PLOTLY__
<style>
:root { color-scheme: light; --bg:#fcfcfb; --fg:#0b0b0b; --muted:#52514e; --line:#e4e3df; --accent:#2a78d6; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  color-scheme: dark; --bg:#1a1a19; --fg:#f0efec; --muted:#c3c2b7; --line:#3a3a37; --accent:#3987e5; } }
:root[data-theme="dark"] { color-scheme: dark; --bg:#1a1a19; --fg:#f0efec; --muted:#c3c2b7; --line:#3a3a37; --accent:#3987e5; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:14px system-ui, sans-serif; }
.wrap { display:flex; gap:16px; padding:16px; min-height:100vh; }
#plot { flex:1; min-width:0; height:calc(100vh - 32px); min-height:560px; }
aside { width:260px; flex-shrink:0; border-left:1px solid var(--line); padding-left:16px; }
h2 { font-size:15px; margin:4px 0 8px; }
p.hint { color:var(--muted); font-size:13px; line-height:1.4; margin:0 0 12px; }
ul { list-style:none; padding:0; margin:0 0 12px; }
li { display:flex; gap:8px; align-items:baseline; padding:6px 0; border-bottom:1px solid var(--line); }
li .t { font-variant-numeric: tabular-nums; color:var(--muted); min-width:64px; cursor:pointer; }
li .l { flex:1; cursor:pointer; }
button { font:inherit; background:none; color:var(--fg); border:1px solid var(--line); border-radius:6px; padding:4px 10px; cursor:pointer; }
button:hover { border-color:var(--accent); }
li button { padding:0 6px; border:none; color:var(--muted); }
@media (max-width: 800px) { .wrap { flex-direction:column; } aside { width:auto; border-left:none; padding-left:0; } }
#help-btn { position:fixed; bottom:16px; right:16px; z-index:10; width:30px; height:30px; padding:0;
  border-radius:50%; font-weight:600; }
.modal-backdrop { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.4); z-index:20;
  align-items:center; justify-content:center; }
.modal-backdrop.open { display:flex; }
.modal { position:relative; background:var(--bg); color:var(--fg); border:1px solid var(--line); border-radius:10px;
  padding:20px 24px; max-width:420px; width:calc(100% - 32px); max-height:calc(100vh - 64px); overflow-y:auto; }
.modal h2 { margin-top:0; }
.modal button.close { position:absolute; top:12px; right:12px; padding:2px 8px; }
.modal ul { margin:0; }
.modal li { display:block; border-bottom:1px solid var(--line); padding:8px 0; }
.modal li:last-child { border-bottom:none; }
.modal li b { display:block; margin-bottom:2px; }
</style>
</head>
<body>
<button id="help-btn" title="How to use this page">?</button>
<div class="modal-backdrop" id="help-backdrop">
  <div class="modal">
    <button class="close" id="help-close" title="Close">✕</button>
    <h2>How to use this page</h2>
    <ul>
      <li><b>Legend</b>Click an entry to show/hide that signal. Double-click to isolate it (hide all others).</li>
      <li><b>Zoom</b>Drag on the graph to zoom into a time range. Double-click the graph to reset.</li>
      <li><b>Events</b>Small ticks at the bottom of each graph mark STATE/FAULT/IO signals turning on — hover one to see what happened. The sidebar list shows the same events; click one to zoom to it.</li>
      <li><b>Points of interest</b>Click anywhere on a graph to label that moment. Click a mark in the sidebar list to zoom back to it, or the ✕ to remove it. Marks are saved in this browser and can be exported to CSV.</li>
    </ul>
  </div>
</div>
<div class="wrap">
  <div id="plot"></div>
  <aside>
    <h2>Events</h2>
    <p class="hint">STATE/FAULT/trigger instants from the log. Click one to zoom to it.</p>
    <ul id="events"></ul>
    <h2>Points of interest</h2>
    <p class="hint">Click any point in the graph to mark it. Click a mark below to zoom to it.
    Legend: click to show/hide, double-click to show only one. Drag to zoom, double-click to reset.</p>
    <ul id="pois"></ul>
    <button id="export">Export CSV</button>
    <button id="clear">Clear</button>
  </aside>
</div>
<script>
const fig = __FIGURE__;
const EVENTS = __EVENTS__;
const STORE_KEY = "igniter-pois:" + __TITLE_JSON__;
const plot = document.getElementById("plot");
const baseShapes = fig.layout.shapes || [];
const baseAnnotations = fig.layout.annotations || [];
let pois = [];
try { pois = JSON.parse(localStorage.getItem(STORE_KEY)) || []; } catch (e) {}

function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function themeLayout() {
  const fg = css("--fg"), line = css("--line"), bg = css("--bg");
  const layout = { paper_bgcolor: bg, plot_bgcolor: bg, font: { color: fg } };
  Object.keys(fig.layout).filter(k => k.startsWith("xaxis") || k.startsWith("yaxis"))
    .forEach(k => { layout[k] = Object.assign({}, fig.layout[k], { gridcolor: line, linecolor: line, zerolinecolor: line }); });
  return layout;
}

function poiLayout() {
  const fg = css("--fg");
  return {
    shapes: baseShapes.concat(pois.map(p => ({
      type: "line", xref: "x", yref: "paper", x0: p.t, x1: p.t, y0: 0, y1: 1,
      line: { color: fg, width: 1.5 } }))),
    annotations: baseAnnotations.concat(pois.map(p => ({
      xref: "x", yref: "paper", x: p.t, y: 1.0, yanchor: "bottom", text: p.label,
      showarrow: false, font: { size: 11, color: fg }, textangle: -30 }))),
  };
}

function render() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(pois)); } catch (e) {}
  Plotly.relayout(plot, poiLayout());
  const list = document.getElementById("pois");
  list.innerHTML = "";
  pois.slice().sort((a, b) => a.t - b.t).forEach(p => {
    const li = document.createElement("li");
    li.innerHTML = '<span class="t"></span><span class="l"></span><button title="Remove">✕</button>';
    li.querySelector(".t").textContent = p.t.toFixed(3) + " s";
    li.querySelector(".l").textContent = p.label;
    const zoom = () => Plotly.relayout(plot, { "xaxis.range": [p.t - 0.5, p.t + 0.5] });
    li.querySelector(".t").onclick = zoom;
    li.querySelector(".l").onclick = zoom;
    li.querySelector("button").onclick = () => { pois = pois.filter(q => q !== p); render(); };
    list.appendChild(li);
  });
}

function renderEvents() {
  const list = document.getElementById("events");
  list.innerHTML = "";
  EVENTS.forEach(e => {
    const li = document.createElement("li");
    li.style.borderLeft = "3px solid " + e.color;
    li.style.paddingLeft = "6px";
    li.style.cursor = "pointer";
    li.innerHTML = '<span class="t"></span><span class="l"></span>';
    li.querySelector(".t").textContent = e.t.toFixed(3) + " s";
    li.querySelector(".l").textContent = e.labels.join(", ");
    li.onclick = () => Plotly.relayout(plot, { "xaxis.range": [e.t - 0.5, e.t + 0.5] });
    list.appendChild(li);
  });
}
renderEvents();

const helpBackdrop = document.getElementById("help-backdrop");
document.getElementById("help-btn").onclick = () => helpBackdrop.classList.add("open");
document.getElementById("help-close").onclick = () => helpBackdrop.classList.remove("open");
helpBackdrop.onclick = ev => { if (ev.target === helpBackdrop) helpBackdrop.classList.remove("open"); };
document.addEventListener("keydown", ev => { if (ev.key === "Escape") helpBackdrop.classList.remove("open"); });

Plotly.newPlot(plot, fig.data, Object.assign(fig.layout, themeLayout()), { responsive: true, displaylogo: false })
  .then(() => {
    render();
    plot.on("plotly_click", ev => {
      const x = Number(ev.points[0].x);
      if (Number.isNaN(x)) return;
      const label = prompt("Label for point of interest at " + x.toFixed(3) + " s:");
      if (label) { pois.push({ t: x, label }); render(); }
    });
  });

window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  Plotly.relayout(plot, Object.assign(themeLayout(), poiLayout()));
});

document.getElementById("export").onclick = () => {
  const rows = ["time_s,label"].concat(pois.map(p => p.t + ',"' + p.label.replace(/"/g, '""') + '"'));
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([rows.join("\\n")], { type: "text/csv" }));
  a.download = "points_of_interest.csv";
  a.click();
};
document.getElementById("clear").onclick = () => { if (confirm("Remove all points of interest?")) { pois = []; render(); } };
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", help="headerless event log CSV (timestamp,type,name,node_id,value)")
    ap.add_argument("-o", "--out", default="igniter_sequence.html")
    ap.add_argument("--title", default="Igniter sequence")
    ap.add_argument("--start-state", default="STATE_FIRE", help="bool signal whose rising edge starts the window")
    ap.add_argument("--end-state", default="STATE_IDLE", help="bool signal whose next rising edge ends the window")
    ap.add_argument("--cdn", action="store_true", help="load plotly.js from a CDN instead of embedding it")
    ap.add_argument("--numeric-only", action="store_true",
                     help="skip the STATE/FAULT/IO events sidebar list")
    args = ap.parse_args()

    numeric, bools, _triggers, log_end = load(args.log)
    start, end = find_window(bools, args.start_state, args.end_state, log_end)
    print(f"window: {args.start_state} at {start} -> {args.end_state} at {end} ({(end - start).total_seconds():.3f} s)")

    numeric = {name: clip(t, v, start, end) for name, (t, v) in numeric.items()}
    events = [] if args.numeric_only else event_markers(bools, start, end, args.start_state, args.end_state)
    event_groups = [
        {"t": sum(e["t"] for e in c) / len(c),
         "labels": [e["label"] for e in c],
         "color": c[0]["color"] if len(c) == 1 else "#8a8a86"}
        for c in cluster_events(events)
    ]

    fig = build_figure(numeric, event_groups, args.title)

    if args.cdn:
        plotly_tag = f'<script src="{PLOTLY_CDN}"></script>'
    else:
        from plotly.offline import get_plotlyjs
        plotly_tag = f"<script>{get_plotlyjs()}</script>"

    page = (PAGE.replace("__TITLE_JSON__", json.dumps(args.title))
                .replace("__TITLE__", args.title)
                .replace("__FIGURE__", fig.to_json())
                .replace("__EVENTS__", json.dumps(event_groups))
                .replace("__PLOTLY__", plotly_tag))
    Path(args.out).write_text(page, encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
