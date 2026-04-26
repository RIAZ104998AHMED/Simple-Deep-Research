"""
llm/client.py
Central async wrapper around the OpenAI-compatible OpenRouter API.
All modules (router, parallel, reflection) import from here.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ?? Model names from environment ??????????????????????????????????????????????
ROUTER_MODEL   = os.getenv("ROUTER_MODEL",   "qwen/qwen3-235b-a22b:free")
PRODUCER_MODEL = os.getenv("PRODUCER_MODEL", "qwen/qwen3-235b-a22b:free")
CRITIC_MODEL   = os.getenv("CRITIC_MODEL",   "meta-llama/llama-3.1-8b-instruct:free")

_BASE_URL      = "https://openrouter.ai/api/v1"
_API_KEY       = os.getenv("OPENROUTER_API_KEY", "")
REQUEST_TIMEOUT = 90.0   # seconds per request


async def chat_complete(
    messages:    list[dict[str, str]],
    model:       str | None = None,
    temperature: float = 0.3,
    max_tokens:  int   = 1500,
    json_mode:   bool  = False,
) -> str:
    """
    Single async chat completion call.
    Returns the raw assistant message content as a string.
    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    if not _API_KEY:
        raise EnvironmentError(
            "OPENROUTER_API_KEY is not set.\n"
            "  Run:  cp .env.example .env\n"
            "  Then add your key from https://openrouter.ai/keys"
        )

    model = model or PRODUCER_MODEL
    payload: dict[str, Any] = {
        "model":       model,
        "messages":    messages,
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {_API_KEY}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://deep-research-assistant",
        "X-Title":       "Deep Research Assistant",
    }

    logger.debug("[llm] POST model=%s max_tokens=%d json_mode=%s", model, max_tokens, json_mode)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(
            f"{_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()

    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    logger.debug("[llm] response_len=%d", len(content))
    return content


def parse_json(raw: str) -> dict:
    """
    Robustly parse JSON from an LLM response.
    Strips markdown code fences (```json ... ```) and <json>…</json> tags
    before parsing, since some models wrap their output.
    """
    text = raw.strip()

    # Strip markdown fences
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            ln for ln in lines if not ln.strip().startswith("```")
        ).strip()

    # Strip XML-style tags some models use
    for tag in ("<json>", "</json>", "<JSON>", "</JSON>"):
        text = text.replace(tag, "")
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM returned non-JSON output (first 400 chars):\n{raw[:400]}"
        ) from exc