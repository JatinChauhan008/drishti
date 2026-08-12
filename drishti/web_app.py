from pathlib import Path
from typing import Any

import anyio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from drishti import config
from drishti.chatbot import ask
from drishti.logbus import log_error, log_event

STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSION_HISTORY: dict[str, list[dict[str, str]]] = {}


async def index(request: Request) -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


async def chat(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"reply": "Invalid request body: expected JSON."}, status_code=400)

    message = str(payload.get("message", "")).strip()
    session_id = str(payload.get("session_id", "default")).strip() or "default"
    if not message:
        return JSONResponse({"reply": "Please enter a question."})

    history = SESSION_HISTORY.setdefault(session_id, [])
    log_event("web_chat_request", session_id=session_id, message=message)

    try:
        result = await anyio.to_thread.run_sync(ask, message, list(history))
    except Exception as exc:
        log_error("web_chat_error", exc, session_id=session_id, message=message)
        return JSONResponse(
            {"reply": f"Error: {exc}", "sources": [], "verdict": None},
            status_code=500,
        )

    reply = result.get("answer", "")
    _append_history(history, "user", message)
    _append_history(history, "assistant", reply)

    return JSONResponse(
        {
            "reply": reply,
            "sources": result.get("sources", []),
            "verdict": result.get("verdict"),
            "log_console_port": config.LOG_SERVER_PORT,
        }
    )


def _append_history(history: list[dict[str, str]], role: str, content: Any) -> None:
    history.append({"role": role, "content": str(content)})
    del history[: -config.CHAT_HISTORY_MESSAGES]


app = Starlette(
    debug=True,
    routes=[
        Route("/", index, methods=["GET"]),
        Route("/chat", chat, methods=["POST"]),
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
    ],
)
