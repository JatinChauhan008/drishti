"""Shared structured tracing for the whole app.

Every process (the web app, the chatbot loop, and the MCP tool server, which
runs as its own subprocess) imports this module and calls ``log_event`` /
``log_error``. Events are:

1. Printed to stderr (so you still see them in whichever terminal you're
   running things from), and
2. Appended as JSON lines to ``logs/trace.jsonl``.

``log_server.py`` tails that file and streams it to a browser console on a
separate port, so you get one live view of *everything* -- HTTP calls to
CivicDataSpace, MCP tool calls, LLM requests, judge verdicts, and errors --
regardless of which process produced them.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_DIR / "logs"
LOG_FILE = LOG_DIR / "trace.jsonl"

_lock = threading.Lock()
_MAX_LOG_BYTES = 10 * 1024 * 1024  # rotate at 10MB so the file never grows unbounded

_console_logger = logging.getLogger("civicdataspace")
_console_logger.setLevel(logging.DEBUG)
if not _console_logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", "%H:%M:%S"))
    _console_logger.addHandler(_handler)
    _console_logger.propagate = False


def _ensure_log_dir() -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _rotate_if_needed() -> None:
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > _MAX_LOG_BYTES:
            LOG_FILE.write_text("", encoding="utf-8")
    except OSError:
        pass


def _write(record: dict) -> None:
    _ensure_log_dir()
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _lock:
        try:
            _rotate_if_needed()
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as exc:
            _console_logger.error("Failed writing to trace log: %r", exc)


def _trim(value: Any, limit: int = 400) -> Any:
    s = repr(value)
    return s if len(s) <= limit else s[: limit] + "...<truncated>"


def log_event(event: str, level: str = "info", **data: Any) -> dict:
    """Emit one structured trace event.

    ``event`` is a short machine-readable name, e.g. ``"http_request"``,
    ``"tool_call"``, ``"llm_response"``, ``"judge_verdict"``, ``"error"``.
    Extra keyword args become fields on the JSON record shown in the console.
    """
    record = {
        "id": uuid.uuid4().hex[:8],
        "ts": time.time(),
        "event": event,
        "level": level,
        **data,
    }
    log_fn = getattr(_console_logger, level if level in {"debug", "info", "warning", "error"} else "info")
    summary = {k: (_trim(v) if isinstance(v, (dict, list)) else v) for k, v in data.items() if k != "traceback"}
    log_fn("%-16s %s", event, summary)
    _write(record)
    return record


def log_error(event: str, exc: BaseException, **data: Any) -> dict:
    """Log an exception with its full traceback, without letting it crash the caller."""
    return log_event(
        event=event,
        level="error",
        error_type=type(exc).__name__,
        error_message=str(exc),
        traceback=traceback.format_exc(),
        **data,
    )
