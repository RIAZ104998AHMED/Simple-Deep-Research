"""
prompts/__init__.py
Loads all externalized prompt files from this directory and re-exports
them as module-level string constants so no prompt text is buried in logic.
"""
from pathlib import Path
from .domain_personas import SCIENTIFIC, HISTORICAL, FINANCIAL, GENERAL

_DIR = Path(__file__).parent


def _load(filename: str) -> str:
    """Read a prompt .md file and return its stripped text."""
    return (_DIR / filename).read_text(encoding="utf-8").strip()


# ── Prompt constants ──────────────────────────────────────────────────────────
ROUTER_CLASSIFY  = _load("router_classify.md")
FALLBACK_REFUSAL = _load("fallback_refusal.md")
DECOMPOSE        = _load("decompose.md")
JUDGE            = _load("judge.md")
SYNTHESIZE       = _load("synthesize.md")
CRITIC           = _load("critic.md")
PRODUCER_REVISE  = _load("producer_revise.md")

# ── Domain persona map ────────────────────────────────────────────────────────
DOMAIN_PERSONAS: dict[str, str] = {
    "scientific": SCIENTIFIC,
    "historical": HISTORICAL,
    "financial":  FINANCIAL,
    "general":    GENERAL,
}

__all__ = [
    "ROUTER_CLASSIFY", "FALLBACK_REFUSAL", "DECOMPOSE", "JUDGE",
    "SYNTHESIZE", "CRITIC", "PRODUCER_REVISE", "DOMAIN_PERSONAS",
    "SCIENTIFIC", "HISTORICAL", "FINANCIAL", "GENERAL",
]