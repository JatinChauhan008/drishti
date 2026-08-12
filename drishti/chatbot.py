import json
import sys
from pathlib import Path
from typing import Any

import anyio
import httpx
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from drishti import config
from drishti.judge import judge_answer
from drishti.logbus import log_error, log_event
from drishti.prompts import SYSTEM_PROMPT

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
VENV_PYTHON = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
MAX_TOOL_ROUNDS = 8


def ask(question: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Answer one user question using the LLM and the local MCP tools.

    Returns ``{"answer": str, "sources": [...], "verdict": dict | None}``.
    ``sources`` lists every CivicDataSpace API call the tools made while
    answering; ``verdict`` is the OpenRouter judge's assessment of whether
    the answer is actually supported by those sources (None if judging is
    disabled or was skipped).
    """
    return anyio.run(_ask_async, question, history or [])


async def _ask_async(question: str, history: list[dict[str, str]]) -> dict[str, Any]:
    if not config.LLM_API_KEY:
        raise RuntimeError("Missing LLM_API_KEY or OPENAI_API_KEY. Set one of them before running main.py.")

    server = StdioServerParameters(
        command=str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable,
        args=["-m", "drishti.server"],
        cwd=PROJECT_DIR,
    )

    collected_sources: list[dict[str, Any]] = []
    answer = ""

    try:
        async with stdio_client(server) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                tools = await _load_openai_tools(session)

                messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT.strip()}]
                messages.extend(_clean_history(history))
                messages.append({"role": "user", "content": question})

                for _ in range(MAX_TOOL_ROUNDS):
                    message = await _chat_completion(messages, tools)
                    messages.append(message)

                    tool_calls = message.get("tool_calls") or []
                    if not tool_calls:
                        answer = message.get("content") or ""
                        break

                    for tool_call in tool_calls:
                        result_text, source = await _call_mcp_tool(session, tool_call)
                        if source:
                            collected_sources.append(source)
                        messages.append(
                            {"role": "tool", "tool_call_id": tool_call["id"], "content": result_text}
                        )
                else:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Stop calling tools now. Use the tool results already provided "
                                "and write the best final answer you can. If the results are "
                                "insufficient, say exactly what is missing."
                            ),
                        }
                    )
                    final_message = await _chat_completion(messages, tools, tool_choice="none")
                    answer = final_message.get("content") or ""
    except Exception as exc:
        log_error("chat_error", exc, question=question)
        return {
            "answer": f"Something went wrong while answering: {_display_exception(exc)}",
            "sources": collected_sources,
            "verdict": None,
        }

    verdict = None
    if config.ENABLE_JUDGE and answer:
        verdict = await judge_answer(question, answer, collected_sources)

    return {"answer": answer, "sources": collected_sources, "verdict": verdict}


def _clean_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    cleaned = []
    for message in history:
        role = message.get("role")
        content = str(message.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            cleaned.append({"role": role, "content": content})
    return cleaned[-config.CHAT_HISTORY_MESSAGES :]


async def _load_openai_tools(session: ClientSession) -> list[dict[str, Any]]:
    result = await session.list_tools()
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": _model_attr(tool, "input_schema", "inputSchema"),
            },
        }
        for tool in result.tools
    ]


def _model_attr(model: Any, snake_name: str, camel_name: str) -> Any:
    try:
        return getattr(model, snake_name)
    except AttributeError:
        return getattr(model, camel_name, None)


def _display_exception(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        return _display_exception(exc.exceptions[0])
    return str(exc)


async def _chat_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_choice: str = "auto",
) -> dict[str, Any]:
    payload = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": 0,
        "max_tokens": config.LLM_MAX_TOKENS,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice
    headers = {"Authorization": f"Bearer {config.LLM_API_KEY}"}

    log_event(
        "llm_request",
        model=config.LLM_MODEL,
        message_count=len(messages),
        tool_choice=tool_choice if tools else None,
    )
    try:
        async with httpx.AsyncClient(base_url=config.LLM_BASE_URL, timeout=60.0) as client:
            response = await client.post("/chat/completions", headers=headers, json=payload)
    except httpx.RequestError as exc:
        log_error("llm_request_error", exc)
        raise RuntimeError(f"Could not reach the LLM at {config.LLM_BASE_URL}: {exc}") from exc

    if response.status_code >= 400:
        log_event("llm_error_status", level="error", status=response.status_code, body=response.text[:500])
        raise RuntimeError(f"LLM request failed: {response.status_code} {response.text[:300]}")

    try:
        data = response.json()
        message = data["choices"][0]["message"]
    except (ValueError, KeyError, IndexError) as exc:
        log_error("llm_parse_error", exc, body=response.text[:500])
        raise RuntimeError(f"LLM returned an unexpected response shape: {exc}") from exc

    log_event("llm_response", status=response.status_code, finish_reason=data["choices"][0].get("finish_reason"))
    return message


async def _call_mcp_tool(session: ClientSession, tool_call: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    function = tool_call["function"]
    tool_name = function["name"]

    try:
        arguments = json.loads(function.get("arguments") or "{}")
    except json.JSONDecodeError as exc:
        log_error("tool_args_error", exc, tool=tool_name, raw=function.get("arguments"))
        return json.dumps({"is_error": True, "content": [f"Invalid arguments for {tool_name}: {exc}"]}), None

    log_event("mcp_call", tool=tool_name, arguments=arguments)
    try:
        result = await session.call_tool(tool_name, arguments)
    except Exception as exc:
        log_error("mcp_call_error", exc, tool=tool_name, arguments=arguments)
        return json.dumps({"is_error": True, "content": [f"{tool_name} failed: {exc}"]}), None

    payload = None
    structured_content = _model_attr(result, "structured_content", "structuredContent")
    is_error = bool(_model_attr(result, "is_error", "isError"))

    if structured_content is not None:
        payload = structured_content
        result_text = json.dumps(payload, ensure_ascii=False)
    else:
        content = []
        for block in result.content:
            if getattr(block, "type", None) == "text":
                content.append(block.text)
            else:
                content.append(block.model_dump(mode="json"))
        if len(content) == 1 and isinstance(content[0], str):
            try:
                parsed_content = json.loads(content[0])
            except json.JSONDecodeError:
                parsed_content = None
            if isinstance(parsed_content, dict):
                payload = parsed_content
                is_error = bool(parsed_content.get("error"))
        result_text = json.dumps({"is_error": is_error, "content": content}, ensure_ascii=False)

    source = None
    if isinstance(payload, dict):
        raw_source = payload.get("source")
        if raw_source:
            source = {"tool": tool_name, **raw_source}

    log_event("mcp_result", level="warning" if is_error else "info", tool=tool_name, is_error=is_error, has_source=source is not None)
    return result_text, source
