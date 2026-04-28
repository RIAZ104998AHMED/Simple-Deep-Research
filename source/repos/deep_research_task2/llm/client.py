import json
import os
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

ROUTER_MODEL = os.getenv("ROUTER_MODEL", os.getenv("PRODUCER_MODEL", "qwen/qwen3-next-80b-a3b-instruct:free"))
PRODUCER_MODEL = os.getenv("PRODUCER_MODEL", "qwen/qwen3-next-80b-a3b-instruct:free")
CRITIC_MODEL = os.getenv("CRITIC_MODEL", "z-ai/glm-4.5-air:free")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", os.getenv("PRODUCER_MODEL", "qwen/qwen3-next-80b-a3b-instruct:free"))


class LLMError(RuntimeError):
    pass


async def chat_complete(
    messages: list[dict[str, str]],
    model: str,
    temperature: float = 0.3,
    timeout: float = 45.0,
) -> str:
    if not OPENROUTER_API_KEY:
        raise LLMError("OPENROUTER_API_KEY is missing. Add it to your .env file.")

    url = f"{BASE_URL}/chat/completions"

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",
        "X-Title": "Deep Research Task 2",
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=payload)

    if response.status_code >= 400:
        raise LLMError(f"LLM request failed: {response.status_code} {response.text}")

    data = response.json()

    try:
        return data["choices"][0]["message"]["content"]
    except Exception as exc:
        raise LLMError(f"Unexpected LLM response format: {data}") from exc


def strip_json_fences(text: str) -> str:
    cleaned = text.strip()

    if cleaned.startswith("```json"):
        cleaned = cleaned.removeprefix("```json").strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```").strip()

    if cleaned.endswith("```"):
        cleaned = cleaned.removesuffix("```").strip()

    return cleaned


def parse_json(text: str) -> dict[str, Any]:
    cleaned = strip_json_fences(text)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])

        raise


def parse_json_list(text: str) -> list[Any]:
    cleaned = strip_json_fences(text)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("[")
        end = cleaned.rfind("]")

        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])

        raise