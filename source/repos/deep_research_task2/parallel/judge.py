from dataclasses import dataclass

from llm.client import JUDGE_MODEL, chat_complete, parse_json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
PROMPT_DIR = BASE_DIR / "prompts"


def read_prompt(filename: str) -> str:
    return (PROMPT_DIR / filename).read_text(encoding="utf-8")


JUDGE_PROMPT = read_prompt("judge.md")


@dataclass
class CandidateScore:
    index: int
    score: float
    correctness: float
    specificity: float
    hedging: float
    reasoning: str


async def judge_candidate(
    question: str,
    sub_question: str,
    candidate_answer: str,
    index: int,
) -> CandidateScore:
    messages = [
        {"role": "system", "content": JUDGE_PROMPT},
        {
            "role": "user",
            "content": (
                f"Original research question:\n{question}\n\n"
                f"Sub-question:\n{sub_question}\n\n"
                f"Candidate answer:\n{candidate_answer}\n\n"
                "Return only JSON."
            ),
        },
    ]

    try:
        raw = await chat_complete(
            messages=messages,
            model=JUDGE_MODEL,
            temperature=0.0,
            timeout=35.0,
        )
        data = parse_json(raw)

        return CandidateScore(
            index=index,
            score=float(data.get("score", 0.0)),
            correctness=float(data.get("correctness", 0.0)),
            specificity=float(data.get("specificity", 0.0)),
            hedging=float(data.get("hedging", 0.0)),
            reasoning=str(data.get("reasoning", "")),
        )

    except Exception as exc:
        return CandidateScore(
            index=index,
            score=0.0,
            correctness=0.0,
            specificity=0.0,
            hedging=0.0,
            reasoning=f"Judge failed or malformed JSON: {exc}",
        )