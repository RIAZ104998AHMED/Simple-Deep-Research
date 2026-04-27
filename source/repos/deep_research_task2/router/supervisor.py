"""
router/supervisor.py
LLM-based domain supervisor.

Two-pass approach:
  Pass 1 — Guardrail: detects prompt injection, PII, disallowed content
  Pass 2 — Domain classification: scientific / historical / financial /
            general / fallback
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from prompts import ROUTER_CLASSIFY, FALLBACK_REFUSAL
from llm.client import chat_complete, parse_json, ROUTER_MODEL

logger = logging.getLogger(__name__)

VALID_DOMAINS = {"scientific", "historical", "financial", "general", "fallback"}


@dataclass
class RouterResult:
    """Structured output from the domain supervisor."""
    domain:              str
    confidence:          float
    guardrail_triggered: bool
    guardrail_reason:    str | None
    reasoning:           str
    raw_question:        str


async def classify(question: str) -> RouterResult:
    """
    Run the two-pass LLM supervisor and return a RouterResult.
    Never raises — falls back to domain='fallback' on any parse error.
    """
    logger.info("[router] classifying: %.100r", question)

    messages = [
        {"role": "system", "content": ROUTER_CLASSIFY},
        {"role": "user",   "content": question},
    ]

    raw = await chat_complete(
        messages,
        model=ROUTER_MODEL,
        temperature=0.1,
        max_tokens=300,
        json_mode=True,
    )
    logger.debug("[router] raw_response=%s", raw)

    try:
        parsed = parse_json(raw)
    except ValueError as exc:
        logger.warning("[router] JSON parse failed → fallback: %s", exc)
        parsed = {
            "domain": "fallback",
            "confidence": 0.0,
            "guardrail_triggered": False,
            "guardrail_reason": f"Router returned malformed JSON: {exc}",
            "reasoning": "Parse error — defaulted to fallback",
        }

    domain = str(parsed.get("domain", "fallback")).lower().strip()
    if domain not in VALID_DOMAINS:
        logger.warning("[router] unknown domain %r → remapping to fallback", domain)
        domain = "fallback"

    result = RouterResult(
        domain=domain,
        confidence=float(parsed.get("confidence", 0.0)),
        guardrail_triggered=bool(parsed.get("guardrail_triggered", False)),
        guardrail_reason=parsed.get("guardrail_reason"),
        reasoning=str(parsed.get("reasoning", "")),
        raw_question=question,
    )

    _print_result(result)
    return result


def _print_result(r: RouterResult) -> None:
    if r.guardrail_triggered:
        logger.warning(
            "[router] ⚠️  GUARDRAIL TRIGGERED  domain=fallback  reason=%s",
            r.guardrail_reason,
        )
    else:
        logger.info(
            "[router] ✅ domain=%-12s  confidence=%.2f  reasoning=%s",
            r.domain, r.confidence, r.reasoning,
        )


async def get_fallback_response(result: RouterResult) -> str:
    """
    Generate a graceful, helpful refusal message for questions that hit
    the fallback / guardrail path.
    """
    reason = result.guardrail_reason or "The question is ambiguous or out of scope."
    messages = [
        {"role": "system", "content": FALLBACK_REFUSAL},
        {
            "role": "user",
            "content": (
                f"Reason for rejection: {reason}\n\n"
                f"Original question: {result.raw_question}"
            ),
        },
    ]
    return await chat_complete(
        messages,
        model=ROUTER_MODEL,
        temperature=0.4,
        max_tokens=200,
    )