"""Live trace console -- run this in its own terminal, on its own port.

    python log_server.py

It tails ``logs/trace.jsonl`` (written to by logbus.py from every backend
process: web_app.py, chatbot.py, and the tools.py MCP server subprocess) and
streams every event to the browser over a websocket. This is where you see
*everything* -- HTTP calls to CivicDataSpace, MCP tool calls and results,
LLM requests/responses, judge verdicts, and full error tracebacks -- not
just the plain fetch()/GET lines the in-page debug panel shows.

Runs independently of the main app: even if web_app.py or the chatbot crash,
this keeps serving whatever was already written to the log file, and will
pick back up as soon as new lines appear.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import HTMLResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from drishti import config

LOG_FILE = config.PROJECT_DIR / "logs" / "trace.jsonl"
BACKLOG_LINES = 300
POLL_SECONDS = 0.4


async def index(request) -> HTMLResponse:
    return HTMLResponse(CONSOLE_HTML)


def _read_backlog() -> list[str]:
    if not LOG_FILE.exists():
        return []
    try:
        lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return lines[-BACKLOG_LINES:]


async def _send_line(websocket: WebSocket, line: str) -> bool:
    line = line.strip()
    if not line:
        return True
    try:
        json.loads(line)  # validate before forwarding, skip partial/corrupt lines
    except json.JSONDecodeError:
        return True
    try:
        await websocket.send_text(line)
    except RuntimeError:
        return False
    return True


async def ws_logs(websocket: WebSocket) -> None:
    await websocket.accept()

    for line in _read_backlog():
        if not await _send_line(websocket, line):
            return

    position = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0
    try:
        while True:
            await asyncio.sleep(POLL_SECONDS)
            if not LOG_FILE.exists():
                continue
            size = LOG_FILE.stat().st_size
            if size < position:
                position = 0  # file was rotated/truncated -- start over
            if size > position:
                with LOG_FILE.open("r", encoding="utf-8") as f:
                    f.seek(position)
                    new_data = f.read()
                    position = f.tell()
                for line in new_data.splitlines():
                    if not await _send_line(websocket, line):
                        return
    except WebSocketDisconnect:
        return


CONSOLE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<title>CivicDataSpace &mdash; Live Trace Console</title>
<style>
  :root {
    --bg: #0b0e12; --panel: #11151b; --line: #1c222b; --text: #d6dbe1;
    --dim: #6b7480; --accent: #5fb0ff;
    --lvl-debug: #6b7480; --lvl-info: #7fd68c; --lvl-warning: #e0b23c; --lvl-error: #ff7a7a;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  header {
    position: sticky; top: 0; z-index: 5; display: flex; align-items: center; gap: 12px;
    padding: 10px 14px; background: var(--panel); border-bottom: 1px solid var(--line);
  }
  header h1 { font-size: 14px; margin: 0; font-weight: 600; letter-spacing: .3px; }
  #status { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--dim); }
  #dot { width: 8px; height: 8px; border-radius: 50%; background: var(--dim); }
  #dot.on { background: var(--lvl-info); box-shadow: 0 0 6px var(--lvl-info); }
  #dot.off { background: var(--lvl-error); }
  .spacer { flex: 1; }
  input[type=text] {
    background: #0e1116; border: 1px solid var(--line); color: var(--text);
    padding: 6px 10px; border-radius: 6px; font: inherit; width: 220px;
  }
  select {
    background: #0e1116; border: 1px solid var(--line); color: var(--text);
    padding: 6px 8px; border-radius: 6px; font: inherit;
  }
  button {
    background: #1a2029; border: 1px solid var(--line); color: var(--text);
    padding: 6px 10px; border-radius: 6px; font: inherit; cursor: pointer;
  }
  button:hover { border-color: var(--accent); }
  #log { padding: 6px 14px 60px; }
  .row {
    display: grid; grid-template-columns: 78px 92px 130px 1fr; gap: 10px;
    padding: 5px 6px; border-bottom: 1px solid var(--line); align-items: start; cursor: pointer;
  }
  .row:hover { background: #0e1319; }
  .time { color: var(--dim); }
  .lvl { font-weight: 700; text-transform: uppercase; font-size: 11px; }
  .lvl.debug { color: var(--lvl-debug); }
  .lvl.info { color: var(--lvl-info); }
  .lvl.warning { color: var(--lvl-warning); }
  .lvl.error { color: var(--lvl-error); }
  .evt { color: var(--accent); }
  .summary { color: var(--text); white-space: pre-wrap; word-break: break-word; }
  .detail {
    display: none; grid-column: 1 / -1; white-space: pre-wrap; word-break: break-word;
    background: #0e1116; border: 1px solid var(--line); border-radius: 6px;
    padding: 8px 10px; margin-top: 4px; color: #b9c2cc;
  }
  .row.open .detail { display: block; }
  .row.error { background: rgba(255, 122, 122, 0.06); }
  .row.warning { background: rgba(224, 178, 60, 0.05); }
  #empty { color: var(--dim); padding: 30px 14px; }
</style>
</head>
<body>
<header>
  <h1>Live Trace Console</h1>
  <div id="status"><span id="dot"></span><span id="status-text">connecting...</span></div>
  <div class="spacer"></div>
  <select id="level-filter">
    <option value="">All levels</option>
    <option value="debug">Debug+</option>
    <option value="info">Info+</option>
    <option value="warning">Warning+</option>
    <option value="error">Error only</option>
  </select>
  <input type="text" id="filter" placeholder="filter by text / event name..." />
  <button id="clear-btn">Clear</button>
  <button id="pause-btn">Pause</button>
</header>
<div id="log"></div>
<div id="empty">Waiting for events&hellip; trigger a chat request to see traffic here.</div>

<script>
  const logEl = document.getElementById("log");
  const emptyEl = document.getElementById("empty");
  const dot = document.getElementById("dot");
  const statusText = document.getElementById("status-text");
  const filterInput = document.getElementById("filter");
  const levelFilter = document.getElementById("level-filter");
  const pauseBtn = document.getElementById("pause-btn");
  const LEVEL_ORDER = { debug: 0, info: 1, warning: 2, error: 3 };

  let paused = false;
  const buffer = [];
  const MAX_ROWS = 2000;

  pauseBtn.addEventListener("click", () => {
    paused = !paused;
    pauseBtn.textContent = paused ? "Resume" : "Pause";
  });

  document.getElementById("clear-btn").addEventListener("click", () => {
    buffer.length = 0;
    logEl.innerHTML = "";
    emptyEl.style.display = "block";
  });

  function fmtTime(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString(undefined, { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
  }

  function summarize(record) {
    const skip = new Set(["id", "ts", "event", "level", "traceback"]);
    const parts = [];
    for (const k of Object.keys(record)) {
      if (skip.has(k)) continue;
      let v = record[k];
      if (typeof v === "object") v = JSON.stringify(v);
      let s = String(v);
      if (s.length > 300) s = s.slice(0, 300) + "...<truncated>";
      parts.push(k + "=" + s);
    }
    return parts.join("  ");
  }

  function render(record) {
    emptyEl.style.display = "none";
    const row = document.createElement("div");
    row.className = "row " + (record.level || "info");
    row.dataset.text = (JSON.stringify(record)).toLowerCase();
    row.dataset.level = record.level || "info";

    const time = document.createElement("div");
    time.className = "time";
    time.textContent = fmtTime(record.ts || Date.now() / 1000);

    const lvl = document.createElement("div");
    lvl.className = "lvl " + (record.level || "info");
    lvl.textContent = record.level || "info";

    const evt = document.createElement("div");
    evt.className = "evt";
    evt.textContent = record.event || "event";

    const summary = document.createElement("div");
    summary.className = "summary";
    summary.textContent = summarize(record);

    const detail = document.createElement("pre");
    detail.className = "detail";
    detail.textContent = JSON.stringify(record, null, 2);

    row.append(time, lvl, evt, summary, detail);
    row.addEventListener("click", () => row.classList.toggle("open"));
    logEl.appendChild(row);

    while (logEl.children.length > MAX_ROWS) {
      logEl.removeChild(logEl.firstChild);
    }
    applyFilter(row);
    window.scrollTo(0, document.body.scrollHeight);
  }

  function applyFilter(row) {
    const text = filterInput.value.trim().toLowerCase();
    const lvl = levelFilter.value;
    let show = true;
    if (text && !row.dataset.text.includes(text)) show = false;
    if (lvl && LEVEL_ORDER[row.dataset.level] < LEVEL_ORDER[lvl]) show = false;
    row.style.display = show ? "" : "none";
  }

  function reapplyFilters() {
    document.querySelectorAll("#log .row").forEach(applyFilter);
  }
  filterInput.addEventListener("input", reapplyFilters);
  levelFilter.addEventListener("change", reapplyFilters);

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(proto + "://" + location.host + "/ws");

    ws.onopen = () => { dot.className = "on"; statusText.textContent = "connected"; };
    ws.onclose = () => { dot.className = "off"; statusText.textContent = "disconnected -- retrying..."; setTimeout(connect, 1500); };
    ws.onerror = () => ws.close();
    ws.onmessage = (event) => {
      if (paused) return;
      try {
        const record = JSON.parse(event.data);
        render(record);
      } catch (e) { /* ignore malformed lines */ }
    };
  }
  connect();
</script>
</body>
</html>
"""

app = Starlette(
    debug=False,
    routes=[
        Route("/", index, methods=["GET"]),
        WebSocketRoute("/ws", ws_logs),
    ],
)

if __name__ == "__main__":
    import uvicorn

    print(f"Live trace console: http://127.0.0.1:{config.LOG_SERVER_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=config.LOG_SERVER_PORT)
