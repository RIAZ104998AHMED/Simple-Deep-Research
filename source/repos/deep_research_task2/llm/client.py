import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

# Always load .env from the project root, not from a random working directory.
BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env", override=True)

BASE_URL = os.getenv("BASE_URL", "https://openrouter.ai/api/v1").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

ROUTER_MODEL = os.getenv(
    "ROUTER_MODEL",
    os.getenv("PRODUCER_MODEL", "z-ai/glm-4.5-air:free"),
).strip()

PRODUCER_MODEL = os.getenv(
    "PRODUCER_MODEL",
    "z-ai/glm-4.5-air:free",
).strip()

CRITIC_MODEL = os.getenv(
    "CRITIC_MODEL",
    "qwen/qwen3-next-80b-a3b-instruct:free",
).strip()

JUDGE_MODEL = os.getenv(
    "JUDGE_MODEL",
    os.getenv("PRODUCER_MODEL", "z-ai/glm-4.5-air:free"),
).strip()


class LLMError(RuntimeError):
    pass


async def chat_complete(
    messages: list[dict[str, str]],
    model: str,
    temperature: float = 0.3,
    timeout: float = 45.0,
) -> str:
    """
    Calls OpenRouter Chat Completions API.

    Includes:
    - reliable .env loading
    - Authorization header
    - retry handling for 429 rate limits
    - useful error messages
    """

    if not OPENROUTER_API_KEY:
        raise LLMError(
            "OPENROUTER_API_KEY is missing. Add it to your .env file."
        )

    if OPENROUTER_API_KEY.startswith("sk-or-v1-your") or "PASTE" in OPENROUTER_API_KEY:
        raise LLMError(
            "OPENROUTER_API_KEY still looks like a placeholder. "
            "Replace it with your real OpenRouter API key."
        )

    url = f"{BASE_URL.rstrip('/')}/chat/completions"

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

    retry_delays = [2, 5, 10]

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(len(retry_delays) + 1):
            try:
                response = await client.post(url, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                if attempt < len(retry_delays):
                    delay = retry_delays[attempt]
                    print(f"[llm] timeout on attempt {attempt + 1}; retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    continue

                raise LLMError(f"LLM request timed out after retries: {exc}") from exc

            except httpx.HTTPError as exc:
                raise LLMError(f"HTTP client error while calling LLM: {exc}") from exc

            if response.status_code == 429:
                if attempt < len(retry_delays):
                    delay = retry_delays[attempt]
                    print(
                        f"[llm] rate limited for model={model} "
                        f"on attempt {attempt + 1}; retrying in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                    continue

                raise LLMError(
                    f"LLM request failed after retries due to rate limiting: "
                    f"{response.status_code} {response.text}"
                )

            if response.status_code == 401:
                raise LLMError(
                    "OpenRouter authentication failed. "
                    "Check that OPENROUTER_API_KEY in .env is real and active. "
                    f"Response: {response.text}"
                )

            if response.status_code >= 400:
                raise LLMError(
                    f"LLM request failed: {response.status_code} {response.text}"
                )

            data = response.json()

            try:
                return data["choices"][0]["message"]["content"]
            except Exception as exc:
                raise LLMError(f"Unexpected LLM response format: {data}") from exc

    raise LLMError("LLM request failed unexpectedly.")


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