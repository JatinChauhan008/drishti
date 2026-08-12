"""Verifies chatbot answers against the sources that backed them, using a
judge model on OpenRouter.

Every tool call in tools.py now returns a ``source`` block describing exactly
which CivicDataSpace API call produced the data (dataset_id, resource_id,
api_url, filters, retrieved_at). chatbot.py collects those as the model
works, and after it has a final answer this module asks an independent judge
model whether the answer is actually supported by those sources.

This never raises: any failure (missing key, network error, bad response)
comes back as a verdict dict explaining what went wrong, so it can be shown
in the trace console without breaking the chat.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from drishti import config
from drishti.logbus import log_error, log_event

JUDGE_SYSTEM_PROMPT = """You are a strict fact-checking judge for a data assistant.

You are given a user question, the assistant's answer, and the list of
source records that were actually retrieved from the CivicDataSpace API to
produce that answer. Each source describes which dataset/resource/API call
was used.

Decide whether the answer is fully and specifically supported by those
sources -- no invented numbers, no claims the sources don't back up.

Respond with ONLY a JSON object, no prose, no markdown fences, of the form:
{"verified": true|false, "confidence": <float 0-1>, "issues": "<short string, empty if none>"}
"""


async def judge_answer(question: str, answer: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Ask the OpenRouter judge model whether `answer` is supported by `sources`."""

    if not config.OPENROUTER_API_KEY:
        return {"verified": None, "confidence": None, "issues": "Judge skipped: OPENROUTER_API_KEY is not set."}

    if not sources:
        return {"verified": None, "confidence": None, "issues": "Judge skipped: no sources were retrieved for this answer."}

    payload = {
        "model": config.JUDGE_MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"question": question, "answer": answer, "sources": sources},
                    ensure_ascii=False,
                    default=str,
                ),
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "HTTP-Referer": config.OPENROUTER_SITE_URL,
        "X-Title": "CivicDataSpace Assistant",
    }

    log_event("judge_request", model=config.JUDGE_MODEL, source_count=len(sources))
    try:
        async with httpx.AsyncClient(base_url=config.OPENROUTER_BASE_URL, timeout=30.0) as client:
            response = await client.post("/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"].strip()
        content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        verdict = json.loads(content)
    except httpx.HTTPStatusError as exc:
        log_error("judge_error", exc, status=exc.response.status_code, body=exc.response.text[:300])
        return {"verified": None, "confidence": None, "issues": f"Judge request failed: HTTP {exc.response.status_code}"}
    except httpx.RequestError as exc:
        log_error("judge_error", exc, kind="connection")
        return {"verified": None, "confidence": None, "issues": f"Could not reach OpenRouter: {exc}"}
    except (KeyError, IndexError, TypeError) as exc:
        log_error("judge_error", exc, kind="unexpected_response_shape")
        return {"verified": None, "confidence": None, "issues": f"Judge returned an unexpected response shape: {exc}"}
    except json.JSONDecodeError as exc:
        log_error("judge_error", exc, kind="bad_json", raw=content if "content" in locals() else None)
        return {"verified": None, "confidence": None, "issues": f"Judge response was not valid JSON: {exc}"}

    verdict.setdefault("verified", None)
    verdict.setdefault("confidence", None)
    verdict.setdefault("issues", "")
    log_event("judge_verdict", **verdict)
    return verdict
