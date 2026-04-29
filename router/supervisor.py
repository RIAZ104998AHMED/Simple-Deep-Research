import logging
from dataclasses import dataclass
from pathlib import Path

from llm.client import ROUTER_MODEL, chat_complete, parse_json

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[1]
PROMPT_DIR = BASE_DIR / "prompts"


def read_prompt(filename: str) -> str:
    path = PROMPT_DIR / filename

    for encoding in ("cp1252", "utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue

    raise UnicodeDecodeError(
        "unknown",
        b"",
        0,
        1,
        f"Could not decode prompt file: {path}",
    )


ROUTER_CLASSIFY = read_prompt("router_classify.md")
FALLBACK_REFUSAL = read_prompt("fallback_refusal.md")

VALID_DOMAINS = {"scientific", "historical", "financial", "general", "fallback"}


@dataclass
class RouterResult:
    domain: str
    confidence: float
    guardrail_triggered: bool
    guardrail_reason: str | None
    reasoning: str
    raw_question: str


async def classify(question: str) -> RouterResult:
    messages = [
        {"role": "system", "content": ROUTER_CLASSIFY},
        {"role": "user", "content": question},
    ]

    try:
        raw = await chat_complete(
            messages=messages,
            model=ROUTER_MODEL,
            temperature=0.0,
            timeout=30.0,
        )
        data = parse_json(raw)
    except Exception as exc:
        logger.exception("Router failed. Falling back safely.")
        return RouterResult(
            domain="fallback",
            confidence=0.0,
            guardrail_triggered=True,
            guardrail_reason=f"Router parsing or API failure: {exc}",
            reasoning="Router failed, so the system chose fallback.",
            raw_question=question,
        )

    domain = str(data.get("domain", "fallback")).lower().strip()

    if domain not in VALID_DOMAINS:
        domain = "fallback"

    confidence = float(data.get("confidence", 0.0))
    confidence = max(0.0, min(1.0, confidence))

    result = RouterResult(
        domain=domain,
        confidence=confidence,
        guardrail_triggered=bool(data.get("guardrail_triggered", False)),
        guardrail_reason=data.get("guardrail_reason"),
        reasoning=str(data.get("reasoning", "")),
        raw_question=question,
    )

    print(
        f"[router] domain={result.domain} "
        f"confidence={result.confidence:.2f} "
        f"guardrail_triggered={result.guardrail_triggered}"
    )

    if result.guardrail_reason:
        print(f"[router] guardrail_reason={result.guardrail_reason}")

    return result


def fallback_message(reason: str | None = None) -> str:
    if reason:
        return f"{FALLBACK_REFUSAL}\n\nReason: {reason}"
    return FALLBACK_REFUSAL