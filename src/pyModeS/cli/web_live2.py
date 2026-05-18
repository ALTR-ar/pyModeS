"""Web dashboard for ``modes live --web``.

Serves a real-time browser UI when ``--web`` is passed to ``modes live``.
The page lets the user pick an aircraft from a live dropdown, pick one or
more fields from that aircraft's decoded state, and see a rolling time-series
chart updated every second.

Architecture
------------
- A daemon worker thread drains ``NetworkSource``, calls
  ``PipeDecoder.decode``, and appends each decoded value into a per-ICAO,
  per-field deque of (timestamp, value) pairs (max 3 600 points, ~1 hour at
  1 msg/s).
- A Flask app (or stdlib ``http.server`` if Flask is absent) serves three
  endpoints:

    GET /             → the single-page dashboard (HTML/JS)
    GET /api/aircraft → JSON list of active ICAO + callsign pairs
    GET /api/series?icao=ABCDEF&field=altitude
                      → JSON array of [ts, value] for the last N points

Thread safety is the same as _tui.py: single-key dict writes from the worker
are GIL-atomic under CPython; reads snapshot with ``list(…)``.

Dependencies
------------
- Flask is used when available (``pip install "pyModeS[web]"``), otherwise
  the stdlib ``http.server`` is used as a zero-dependency fallback.
- Plotly.js is loaded from cdnjs — no extra Python packages needed for the
  frontend.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import threading
import time
from collections import deque
from typing import Any

from pyModeS import PipeDecoder
from pyModeS.cli._source import NetworkSource, UnsupportedStreamError

# Maximum time-series points kept per (icao, field). At one decoded message
# per second this covers ~1 hour. Older points are silently dropped.
_MAX_POINTS: int = 3_600

# Aircraft not seen in this many seconds are hidden from the /api/aircraft
# list.  Matches the TUI eviction window.
_EXPIRE_SECONDS: float = 300.0

# All numeric (or numeric-convertible) fields that may appear in decoded
# messages.  The UI only offers fields for which at least one data point
# exists for the selected aircraft.
_NUMERIC_FIELDS: tuple[str, ...] = (
    "altitude",
    "selected_altitude_mcp",
    "selected_altitude_fms",
    "groundspeed",
    "true_airspeed",
    "indicated_airspeed",
    "mach",
    "vertical_rate",
    "baro_vertical_rate",
    "inertial_vertical_rate",
    "track",
    "true_track",
    "heading",
    "magnetic_heading",
    "roll",
    "nac_p",
    "latitude",
    "longitude",
)


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

class _Store:
    """Thread-safe-enough store for aircraft time-series data.

    Each aircraft gets a dict of field → deque of (ts_float, float_value).
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def ingest(self, icao: str, decoded: dict[str, Any], ts: float) -> None:
        state = self._data.get(icao)
        if state is None:
            state = {"_last_seen": ts, "_callsign": ""}
            self._data[icao] = state
        state["_last_seen"] = ts
        cs = decoded.get("callsign")
        if cs:
            state["_callsign"] = cs
        for field in _NUMERIC_FIELDS:
            val = decoded.get(field)
            if val is None:
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            if field not in state:
                state[field] = deque(maxlen=_MAX_POINTS)
            state[field].append((ts, fval))

    def active_aircraft(self) -> list[dict[str, str]]:
        cutoff = time.time() - _EXPIRE_SECONDS
        out = []
        for icao, state in list(self._data.items()):
            if state.get("_last_seen", 0.0) < cutoff:
                continue
            out.append({"icao": icao.upper(), "callsign": state.get("_callsign", "")})
        out.sort(key=lambda x: x["icao"])
        return out

    def available_fields(self, icao: str) -> list[str]:
        state = self._data.get(icao.lower()) or self._data.get(icao)
        if state is None:
            return []
        # return [f for f in _NUMERIC_FIELDS if f in state and len(state[f]) > 0]
        return [f for f, v in state.items() if hasattr(v, "__len__") and len(v) > 0]
      
    def series(self, icao: str, field: str) -> list[tuple[float, float]]:
        state = self._data.get(icao.lower()) or self._data.get(icao)
        if state is None or field not in state:
            return []
        return list(state[field])


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

def _worker(
    source: NetworkSource,
    pipe: PipeDecoder,
    store: _Store,
    stop: threading.Event,
    error_box: list[BaseException],
    csv_sink: Any,
) -> None:
    try:
        for hex_msg, ts in source:
            if stop.is_set():
                break
            try:
                decoded = pipe.decode(hex_msg, timestamp=ts)
            except Exception:
                continue
            if csv_sink is not None:
                with contextlib.suppress(Exception):
                    row = dict(decoded)
                    row["timestamp"] = ts
                    row["raw_msg"] = hex_msg
                    csv_sink.write(row)
            icao = decoded.get("icao")
            if icao:
                store.ingest(icao, decoded, ts)
    except UnsupportedStreamError as e:
        error_box.append(e)
    except Exception as e:
        if not stop.is_set():
            error_box.append(e)


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>pyModeS live</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.27.0/plotly.min.js"></script>
<link rel="icon" href="data:,">
<style>
  :root {
    --bg: #090d13;
    --surface: #0f1520;
    --border: #1e2d42;
    --accent: #00d4ff;
    --accent3: #7fff6e;
    --text: #c8d8e8;
    --muted: #4a6070;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; overflow: hidden; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'JetBrains Mono', 'Fira Mono', monospace;
    font-size: 13px;
    display: flex;
    flex-direction: column;
  }
  header {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 12px 20px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    flex-shrink: 0;
  }
  .logo { font-weight: 700; font-size: 16px; color: var(--accent); letter-spacing: 0.06em; }
  .logo span { color: var(--text); }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent3); box-shadow: 0 0 6px var(--accent3); animation: pulse 2s infinite; flex-shrink: 0; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.35} }
  .status { color: var(--muted); font-size: 11px; }
  .status strong { color: var(--accent3); }
  .spacer { flex: 1; }
  .ac-count { font-size: 11px; color: var(--muted); }
  .ac-count strong { color: var(--accent); }

  .layout {
    display: flex;
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }

  aside {
    width: 260px;
    flex-shrink: 0;
    border-right: 1px solid var(--border);
    background: var(--surface);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .section { padding: 14px 16px; border-bottom: 1px solid var(--border); }
  .label { font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); margin-bottom: 8px; }

  select {
    width: 100%;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 7px 10px;
    font-family: inherit;
    font-size: 12px;
    cursor: pointer;
    outline: none;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%234a6070' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round' fill='none'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 10px center;
    padding-right: 28px;
  }
  select:focus { border-color: var(--accent); }
  select option { background: var(--surface); }

  .fields-list {
    flex: 1;
    overflow-y: auto;
    padding: 10px 16px;
    scrollbar-width: thin;
    scrollbar-color: var(--border) transparent;
  }
  .field-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 0;
    cursor: pointer;
    user-select: none;
  }
  .field-item:hover { color: #fff; }
  .field-item input[type=checkbox] {
    appearance: none;
    width: 14px; height: 14px;
    border: 1px solid var(--border);
    border-radius: 3px;
    background: var(--bg);
    cursor: pointer;
    flex-shrink: 0;
    position: relative;
    transition: background 0.15s;
  }
  .field-item input[type=checkbox]:checked { background: var(--accent); border-color: var(--accent); }
  .field-item input[type=checkbox]:checked::after {
    content: '';
    position: absolute;
    left: 3px; top: 1px;
    width: 5px; height: 8px;
    border: 2px solid #090d13;
    border-top: none; border-left: none;
    transform: rotate(45deg);
  }
  .no-fields { color: var(--muted); font-size: 11px; padding: 6px 0; }

  .chart-col {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 18px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .sel-label { font-weight: 700; font-size: 15px; color: #fff; }
  .sel-label span { color: var(--muted); font-weight: 400; font-size: 11px; margin-left: 8px; }
  .win-select { width: auto; padding: 5px 24px 5px 8px; font-size: 11px; margin-left: auto; }

  #chart-container {
    flex: 1;
    min-height: 0;
    width: 100%;
    position: relative;
  }

  #plotly-div {
    width: 100%;
    height: 100%;
    display: none;
  }

  .empty {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    color: var(--muted);
    gap: 8px;
    font-size: 12px;
    letter-spacing: 0.08em;
  }
  .empty-icon { font-size: 32px; opacity: 0.25; }
</style>
</head>
<body>

<header>
  <div class="logo">py<span>ModeS</span> live</div>
  <div class="dot"></div>
  <div class="status">RECEIVING · <strong id="msg-count">0</strong> msgs</div>
  <div class="spacer"></div>
  <div class="ac-count"><strong id="ac-count">0</strong> aircraft</div>
</header>

<div class="layout">
  <aside>
    <div class="section">
      <div class="label">Aircraft</div>
      <select id="ac-select">
        <option value="">— waiting for data —</option>
      </select>
    </div>
    <div class="section" style="padding-bottom:8px">
      <div class="label">Fields to plot</div>
    </div>
    <div class="fields-list" id="fields-list">
      <div class="no-fields">Select an aircraft first</div>
    </div>
  </aside>

  <div class="chart-col">
    <div class="toolbar">
      <div class="sel-label" id="selected-label">—<span>no aircraft selected</span></div>
      <select class="win-select" id="window-select">
        <option value="60">1 min</option>
        <option value="300" selected>5 min</option>
        <option value="900">15 min</option>
        <option value="1800">30 min</option>
        <option value="3600">1 hour</option>
      </select>
    </div>

    <div id="chart-container">
      <div class="empty" id="empty-state">
        <div class="empty-icon">✈</div>
        <div>SELECT AN AIRCRAFT AND AT LEAST ONE FIELD</div>
      </div>
      <div id="plotly-div"></div>
    </div>
  </div>
</div>

<script>
const COLORS = ['#00d4ff','#ff6b35','#7fff6e','#ffcc00','#c084fc','#fb7185','#34d399','#60a5fa'];

let selectedIcao = '';
let checkedFields = new Set();
let windowSec = 300;

const acSelect   = document.getElementById('ac-select');
const fieldsList = document.getElementById('fields-list');
const winSelect  = document.getElementById('window-select');
const emptyEl    = document.getElementById('empty-state');
const plotlyDiv  = document.getElementById('plotly-div');

async function api(path) {
  const r = await fetch(path);
  return r.json();
}

// ── Show / hide ───────────────────────────────────────────
function showPlot(yes) {
  plotlyDiv.style.display = yes ? 'block' : 'none';
  emptyEl.style.display   = yes ? 'none'  : 'flex';
}

function clearPlot() {
  Plotly.purge(plotlyDiv);
  showPlot(false);
}

// ── Aircraft list ─────────────────────────────────────────
async function refreshAircraft() {
  const data = await api('/api/aircraft');
  const list = data.aircraft || [];
  document.getElementById('ac-count').textContent = list.length;
  const prev = acSelect.value;
  acSelect.innerHTML = '<option value="">— select aircraft —</option>';
  for (const ac of list) {
    const opt = document.createElement('option');
    opt.value = ac.icao;
    opt.textContent = ac.callsign ? `${ac.icao}  ${ac.callsign}` : ac.icao;
    if (ac.icao === prev) opt.selected = true;
    acSelect.appendChild(opt);
  }
}

acSelect.addEventListener('change', async () => {
  checkedFields.clear();
  clearPlot();
  fieldsList.innerHTML = '<div class="no-fields">Select an aircraft first</div>';

  selectedIcao = acSelect.value;

  if (!selectedIcao) {
    document.getElementById('selected-label').innerHTML = '—<span>no aircraft selected</span>';
    return;
  }

  document.getElementById('selected-label').innerHTML =
    `${selectedIcao}<span>${acSelect.options[acSelect.selectedIndex]?.text || ''}</span>`;

  await refreshFields();
});

// ── Fields list ───────────────────────────────────────────
async function refreshFields() {
  if (!selectedIcao) return;
  const data = await api(`/api/fields?icao=${selectedIcao}`);
  const fields = data.fields || [];
  fieldsList.innerHTML = '';
  if (!fields.length) {
    fieldsList.innerHTML = '<div class="no-fields">No numeric fields yet</div>';
    return;
  }

  // Pre-check all available fields
  fields.forEach((field, i) => {
    const lbl = document.createElement('label');
    lbl.className = 'field-item';
    const cb = document.createElement('input');
    cb.type = 'checkbox';


    cb.addEventListener('change', () => {
      if (cb.checked) checkedFields.add(field);
      else            checkedFields.delete(field);
      if (checkedFields.size === 0) { clearPlot(); return; }
    });

    const swatch = document.createElement('span');
    swatch.style.cssText = `display:inline-block;width:8px;height:8px;border-radius:50%;background:${COLORS[i % COLORS.length]};flex-shrink:0;`;

    const span = document.createElement('span');
    span.textContent = field;
    span.style.fontSize = '12px';

    lbl.appendChild(cb);
    lbl.appendChild(swatch);
    lbl.appendChild(span);
    fieldsList.appendChild(lbl);
  });

  scheduleRefresh();
}

// ── Chart ─────────────────────────────────────────────────
let refreshPending = false;
function scheduleRefresh() {
  if (!refreshPending) {
    refreshPending = true;
    requestAnimationFrame(doRefresh);
  }
}

async function doRefresh() {
  refreshPending = false;
  if (!selectedIcao || checkedFields.size === 0) { clearPlot(); return; }

  const now    = Date.now() / 1000;
  const cutoff = now - windowSec;

  // Snapshot fields in stable order (insertion order of Set)
  const fields = [...checkedFields];

  const traces = await Promise.all(fields.map(async (field, i) => {
    const pts      = await api(`/api/series?icao=${selectedIcao}&field=${encodeURIComponent(field)}`);
    const filtered = pts.filter(p => p.ts >= cutoff);
    const color    = COLORS[i % COLORS.length];
    return {
      x:    filtered.map(p => new Date(p.ts * 1000)),
      y:    filtered.map(p => p.value),
      mode: 'lines+markers',
      name: field,
      line: { color, width: 1.5 },
      marker: {
        symbol: 'cross',
        size: 7,
        color: 'rgba(0,0,0,0)',       // transparent fill — cross is open
        line: { color, width: 1.5 },  // stroke gives the visible cross
      },
      yaxis: i === 0 ? 'y' : `y${i + 1}`,
    };
  }));

  const layout = {
    paper_bgcolor: 'transparent',
    plot_bgcolor:  '#090d13',
    margin: { l: 60, r: fields.length > 1 ? 60 * (fields.length - 1) : 20, t: 12, b: 48 },
    font:   { family: 'JetBrains Mono, monospace', size: 11, color: '#c8d8e8' },
    xaxis:  { type: 'date', gridcolor: '#1e2d42', linecolor: '#1e2d42', tickcolor: '#1e2d42' },
    legend: { orientation: 'h', y: -0.14, font: { size: 11 }, bgcolor: 'transparent' },
    autosize: true,
  };

  fields.forEach((field, i) => {
    const key   = i === 0 ? 'yaxis' : `yaxis${i + 1}`;
    const color = COLORS[i % COLORS.length];
    layout[key] = {
      title:      { text: field, font: { size: 10, color } },
      gridcolor:  i === 0 ? '#1e2d42' : 'transparent',
      linecolor:  '#1e2d42',
      tickcolor:  '#1e2d42',
      tickfont:   { color },
      color,
      overlaying: i === 0 ? undefined : 'y',
      side:       i % 2 === 0 ? 'left' : 'right',
      showgrid:   i === 0,
      zeroline:   false,
    };
  });

  showPlot(true);
  Plotly.purge(plotlyDiv);
  Plotly.newPlot(plotlyDiv, traces, layout, {
    responsive:     true,
    displayModeBar: false,
    scrollZoom:     true,
  });
}

// ── Polling ───────────────────────────────────────────────
winSelect.addEventListener('change', () => {
  windowSec = parseInt(winSelect.value);
  scheduleRefresh();
});

setInterval(refreshAircraft, 3000);
setInterval(scheduleRefresh, 1000);
setInterval(async () => {
  try {
    const s = await api('/api/status');
    document.getElementById('msg-count').textContent = s.msg_count ?? 0;
  } catch {}
}, 1000);

refreshAircraft();
showPlot(false);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Flask server
# ---------------------------------------------------------------------------

def _make_flask_app(store: _Store, msg_counter: list[int]) -> Any:
    """Build and return a Flask WSGI app."""
    from flask import Flask, jsonify, request, Response

    app = Flask(__name__)
    app.logger.setLevel("ERROR")

    @app.route("/")
    def index() -> Response:
        return Response(_DASHBOARD_HTML, mimetype="text/html")

    @app.route("/api/aircraft")
    def api_aircraft() -> Response:
        return jsonify({"aircraft": store.active_aircraft()})

    @app.route("/api/fields")
    def api_fields() -> Response:
        icao = request.args.get("icao", "")
        return jsonify({"fields": store.available_fields(icao)})

    @app.route("/api/series")
    def api_series() -> Response:
        icao = request.args.get("icao", "")
        field = request.args.get("field", "")
        pts = store.series(icao, field)
        return jsonify([{"ts": ts, "value": v} for ts, v in pts])

    @app.route("/api/status")
    def api_status() -> Response:
        return jsonify({"msg_count": msg_counter[0]})

    return app


# ---------------------------------------------------------------------------
# Fallback: stdlib http.server
# ---------------------------------------------------------------------------

def _make_stdlib_handler(store: _Store, msg_counter: list[int]):
    """Return a BaseHTTPRequestHandler subclass using only stdlib."""
    from http.server import BaseHTTPRequestHandler
    from urllib.parse import urlparse, parse_qs

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send_json(self, obj: Any, status: int = 200) -> None:
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            path = parsed.path

            if path == "/":
                body = _DASHBOARD_HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/aircraft":
                self._send_json({"aircraft": store.active_aircraft()})
            elif path == "/api/fields":
                icao = qs.get("icao", [""])[0]
                self._send_json({"fields": store.available_fields(icao)})
            elif path == "/api/series":
                icao = qs.get("icao", [""])[0]
                field = qs.get("field", [""])[0]
                pts = store.series(icao, field)
                self._send_json([{"ts": ts, "value": v} for ts, v in pts])
            elif path == "/api/status":
                self._send_json({"msg_count": msg_counter[0]})
            else:
                self.send_response(404)
                self.end_headers()

    return _Handler


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_web_app(
    args: argparse.Namespace,
    pipe: PipeDecoder,
    source: NetworkSource,
    bind_host: str = "0.0.0.0",
    bind_port: int = 8080,
) -> int:
    """Start the web dashboard and block until Ctrl-C.

    Returns an exit code (0 = clean, 1 = worker error, 2 = stream error).
    """
    import sys

    store = _Store()
    msg_counter: list[int] = [0]
    stop = threading.Event()
    error_box: list[BaseException] = []

    csv_sink = None
    if getattr(args, "csv", None):
        try:
            from pyModeS.cli._sink import CsvSink
            csv_sink = CsvSink(args.csv, ac_filter=getattr(args, "ac_filter", None))
        except Exception:
            pass

    _orig_ingest = store.ingest

    def _counting_ingest(icao: str, decoded: dict, ts: float) -> None:
        _orig_ingest(icao, decoded, ts)

    store.ingest = _counting_ingest  # type: ignore[method-assign]

    def _worker_wrapper() -> None:
        try:
            for hex_msg, ts in source:
                if stop.is_set():
                    break
                try:
                    decoded = pipe.decode(hex_msg, timestamp=ts)
                except Exception:
                    msg_counter[0] += 1
                    continue
                msg_counter[0] += 1
                if csv_sink is not None:
                    with contextlib.suppress(Exception):
                        row = dict(decoded)
                        row["timestamp"] = ts
                        row["raw_msg"] = hex_msg
                        csv_sink.write(row)
                icao = decoded.get("icao")
                if icao:
                    store.ingest(icao, decoded, ts)
        except UnsupportedStreamError as e:
            error_box.append(e)
        except Exception as e:
            if not stop.is_set():
                error_box.append(e)

    worker = threading.Thread(target=_worker_wrapper, daemon=True, name="pymodes-web-worker")
    worker.start()

    try:
        from flask import Flask  # noqa: F401
        flask_available = True
    except ImportError:
        flask_available = False

    print(
        f"[pyModeS.web] dashboard → http://{bind_host if bind_host != '0.0.0.0' else 'localhost'}:{bind_port}/",
        file=sys.stderr,
    )

    try:
        if flask_available:
            flask_app = _make_flask_app(store, msg_counter)
            from werkzeug.serving import make_server
            srv = make_server(bind_host, bind_port, flask_app)
            srv.timeout = 1.0
            try:
                while not stop.is_set():
                    srv.handle_request()
            except KeyboardInterrupt:
                pass
        else:
            from http.server import HTTPServer
            handler = _make_stdlib_handler(store, msg_counter)
            srv = HTTPServer((bind_host, bind_port), handler)
            srv.timeout = 1.0
            try:
                while not stop.is_set():
                    srv.handle_request()
            except KeyboardInterrupt:
                pass
    finally:
        stop.set()
        sock = getattr(source, "_sock", None)
        if sock is not None:
            with contextlib.suppress(Exception):
                sock.close()
        if csv_sink is not None:
            with contextlib.suppress(Exception):
                csv_sink.close()

    if error_box:
        err = error_box[0]
        if isinstance(err, UnsupportedStreamError):
            print(f"modes live: error: {err}", file=sys.stderr)
            return 2
        print(f"modes live: error: {err}", file=sys.stderr)
        return 1
    return 0