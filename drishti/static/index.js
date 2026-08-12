// Debug console: captures browser logs, window errors, unhandled promise
// rejections, and fetch requests/responses.
(function () {
  const body = document.getElementById("debug-body");
  const dot = document.getElementById("debug-dot");
  const panel = document.getElementById("debug-console");
  const MAX_LINES = 300;

  function pad(n) { return n.toString().padStart(2, "0"); }

  function timestamp() {
    const d = new Date();
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${d.getMilliseconds().toString().padStart(3, "0")}`;
  }

  function stringify(arg) {
    if (typeof arg === "string") return arg;
    if (arg instanceof Error) return arg.stack || arg.message;
    try {
      return JSON.stringify(arg, null, 2);
    } catch (e) {
      return String(arg);
    }
  }

  function writeLine(level, args, tag) {
    const line = document.createElement("div");
    line.className = "dbg-line " + level;

    const time = document.createElement("span");
    time.className = "dbg-time";
    time.textContent = timestamp();
    line.appendChild(time);

    if (tag) {
      const tagEl = document.createElement("span");
      tagEl.className = "dbg-tag";
      tagEl.textContent = "[" + tag + "] ";
      line.appendChild(tagEl);
    }

    const msg = document.createElement("span");
    msg.textContent = Array.from(args).map(stringify).join(" ");
    line.appendChild(msg);
    body.appendChild(line);

    while (body.children.length > MAX_LINES) {
      body.removeChild(body.firstChild);
    }
    body.scrollTop = body.scrollHeight;

    if (level === "error") {
      dot.classList.add("has-error");
    }
  }

  window.trace = function (tag, ...args) {
    writeLine("trace", args, tag);
  };

  ["log", "info", "warn", "error"].forEach((method) => {
    const original = console[method].bind(console);
    console[method] = function (...args) {
      original(...args);
      writeLine(method === "log" ? "info" : method, args);
    };
  });

  window.addEventListener("error", (event) => {
    writeLine("error", [event.message + " (" + event.filename + ":" + event.lineno + ")"]);
  });

  window.addEventListener("unhandledrejection", (event) => {
    writeLine("error", ["Unhandled promise rejection:", event.reason]);
  });

  const originalFetch = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : input.url;
    const method = (init && init.method) || "GET";
    writeLine("trace", [method + " " + url + (init && init.body ? " " + init.body : "")], "fetch>>");
    const started = performance.now();

    return originalFetch(input, init)
      .then((response) => {
        const elapsed = (performance.now() - started).toFixed(1);
        writeLine("trace", [`${response.status} ${url} (${elapsed}ms)`], "fetch<<");
        return response;
      })
      .catch((err) => {
        const elapsed = (performance.now() - started).toFixed(1);
        writeLine("error", [`FAILED ${url} after ${elapsed}ms: ${err}`], "fetch!!");
        throw err;
      });
  };

  document.getElementById("debug-clear").addEventListener("click", () => {
    body.innerHTML = "";
    dot.classList.remove("has-error");
  });

  document.getElementById("debug-full-trace").addEventListener("click", (event) => {
    event.stopPropagation();
    const port = window.__logConsolePort || 8001;
    window.open(`${location.protocol}//${location.hostname}:${port}/`, "_blank");
  });

  document.getElementById("debug-header").addEventListener("click", (event) => {
    if (event.target.id === "debug-clear") return;
    panel.classList.toggle("collapsed");
  });

  console.log("Debug console ready.");
})();

const sessionId = crypto.randomUUID();
const log = document.getElementById("log");
const form = document.getElementById("chat-form");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send-btn");

function renderMarkdown(text) {
  if (window.marked && window.DOMPurify) {
    return DOMPurify.sanitize(marked.parse(text));
  }
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function addMessage(text, cls, isMarkdown) {
  const div = document.createElement("div");
  div.className = "msg " + cls;
  if (isMarkdown) {
    div.innerHTML = renderMarkdown(text);
  } else {
    div.textContent = text;
  }
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function appendVerdictBadge(msgEl, verdict) {
  if (!verdict || verdict.verified === null || verdict.verified === undefined) return;
  const badge = document.createElement("div");
  badge.className = "verdict-badge " + (verdict.verified ? "ok" : "bad");
  const label = verdict.verified
    ? "Verified against source" + (verdict.confidence != null ? ` (confidence ${verdict.confidence})` : "")
    : "Not verified" + (verdict.issues ? `: ${verdict.issues}` : "");
  badge.textContent = label;
  msgEl.appendChild(badge);
}

async function sendMessage(message) {
  document.body.classList.add("chatting");
  addMessage(message, "user", false);
  sendBtn.disabled = true;
  input.disabled = true;

  const thinking = addMessage("Looking up the data", "bot thinking", false);
  window.trace("chat", "sending message:", message);

  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message })
    });
    const data = await res.json();
    window.trace("chat", "reply received:", data);
    if (data.log_console_port) window.__logConsolePort = data.log_console_port;
    thinking.classList.remove("thinking");
    thinking.innerHTML = renderMarkdown(data.reply || "No reply returned.");
    appendVerdictBadge(thinking, data.verdict);
  } catch (err) {
    console.error("Chat request failed:", err);
    thinking.textContent = "Something went wrong reaching the server.";
    thinking.classList.remove("thinking");
  }

  sendBtn.disabled = false;
  input.disabled = false;
  input.focus();
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  sendMessage(message);
});

document.querySelectorAll(".chip.try").forEach((btn) => {
  btn.addEventListener("click", () => sendMessage(btn.dataset.q));
});
