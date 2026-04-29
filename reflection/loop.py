import difflib
from dataclasses import dataclass
from pathlib import Path

from llm.client import CRITIC_MODEL, PRODUCER_MODEL, chat_complete, parse_json
from prompts.domain_personas import get_domain_persona

BASE_DIR = Path(__file__).resolve().parents[1]
PROMPT_DIR = BASE_DIR / "prompts"


def read_prompt(filename: str) -> str:
    return (PROMPT_DIR / filename).read_text(encoding="utf-8")


CRITIC_PROMPT = read_prompt("critic.md")
PRODUCER_REVISE_PROMPT = read_prompt("producer_revise.md")


@dataclass
class CriticResult:
    factual_grounding: float
    completeness: float
    internal_consistency: float
    domain_tone: float
    unsupported_claims: float
    aggregate_score: float
    revision_instructions: list[str]


async def critique_draft(question: str, domain: str, draft: str) -> CriticResult:
    messages = [
        {"role": "system", "content": CRITIC_PROMPT},
        {
            "role": "user",
            "content": (
                f"Domain: {domain}\n\n"
                f"Original question:\n{question}\n\n"
                f"Draft:\n{draft}\n\n"
                "Return only JSON."
            ),
        },
    ]

    raw = await chat_complete(
        messages=messages,
        model=CRITIC_MODEL,
        temperature=0.0,
        timeout=45.0,
    )

    data = parse_json(raw)
    scores = data.get("scores", {})

    return CriticResult(
        factual_grounding=float(scores.get("factual_grounding", 0.0)),
        completeness=float(scores.get("completeness", 0.0)),
        internal_consistency=float(scores.get("internal_consistency", 0.0)),
        domain_tone=float(scores.get("domain_tone", 0.0)),
        unsupported_claims=float(scores.get("unsupported_claims", 0.0)),
        aggregate_score=float(data.get("aggregate_score", 0.0)),
        revision_instructions=[
            str(x) for x in data.get("revision_instructions", [])
        ],
    )


async def revise_draft(
    question: str,
    domain: str,
    draft: str,
    critic_result: CriticResult,
) -> str:
    persona = get_domain_persona(domain)

    instructions = "\n".join(
        [f"- {item}" for item in critic_result.revision_instructions]
    )

    messages = [
        {"role": "system", "content": f"{persona}\n\n{PRODUCER_REVISE_PROMPT}"},
        {
            "role": "user",
            "content": (
                f"Original question:\n{question}\n\n"
                f"Current draft:\n{draft}\n\n"
                f"Critic revision instructions:\n{instructions}"
            ),
        },
    ]

    return await chat_complete(
        messages=messages,
        model=PRODUCER_MODEL,
        temperature=0.25,
        timeout=60.0,
    )


def make_diff(before: str, after: str) -> str:
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)

    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile="before.md",
            tofile="after.md",
        )
    )


async def run_reflection_loop(
    question: str,
    domain: str,
    initial_draft: str,
    threshold: float = 8.5,
    max_iterations: int = 3,
) -> str:
    current = initial_draft
    first = initial_draft
    best = initial_draft
    best_score = -1.0
    previous_score = -1.0

    for iteration in range(1, max_iterations + 1):
        critic = await critique_draft(question, domain, current)

        print(f"\n[reflection] iteration={iteration}")
        print(f"[reflection] factual_grounding={critic.factual_grounding:.2f}")
        print(f"[reflection] completeness={critic.completeness:.2f}")
        print(f"[reflection] internal_consistency={critic.internal_consistency:.2f}")
        print(f"[reflection] domain_tone={critic.domain_tone:.2f}")
        print(f"[reflection] unsupported_claims={critic.unsupported_claims:.2f}")
        print(f"[reflection] aggregate_score={critic.aggregate_score:.2f}")
        print("[reflection] revision instructions:")
        for item in critic.revision_instructions:
            print(f"  - {item}")

        if critic.aggregate_score > best_score:
            best_score = critic.aggregate_score
            best = current

        if critic.aggregate_score >= threshold:
            print("[reflection] threshold met")
            break

        if previous_score >= 0 and critic.aggregate_score <= previous_score:
            print("[reflection] plateau or regression detected; stopping")
            break

        previous_score = critic.aggregate_score
        current = await revise_draft(question, domain, current, critic)

    print("\n[diff] before/after unified diff:")
    print(make_diff(first, best))

    return best