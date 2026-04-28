import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from llm.client import PRODUCER_MODEL, chat_complete, parse_json_list
from parallel.judge import judge_candidate
from prompts.domain_personas import get_domain_persona

BASE_DIR = Path(__file__).resolve().parents[1]
PROMPT_DIR = BASE_DIR / "prompts"


def read_prompt(filename: str) -> str:
    return (PROMPT_DIR / filename).read_text(encoding="utf-8")


DECOMPOSE_PROMPT = read_prompt("decompose.md")
SYNTHESIZE_PROMPT = read_prompt("synthesize.md")


@dataclass
class CandidateResult:
    index: int
    answer: str
    score: float
    judge_reasoning: str


@dataclass
class SubAnswer:
    sub_question: str
    selected_answer: str
    candidates: list[CandidateResult]


async def decompose_question(question: str, domain: str) -> list[str]:
    persona = get_domain_persona(domain)

    messages = [
        {"role": "system", "content": f"{persona}\n\n{DECOMPOSE_PROMPT}"},
        {"role": "user", "content": question},
    ]

    raw = await chat_complete(
        messages=messages,
        model=PRODUCER_MODEL,
        temperature=0.2,
        timeout=35.0,
    )

    try:
        questions = parse_json_list(raw)
    except Exception:
        questions = [
            "What is the background and context of the topic?",
            "What are the main factors or arguments involved?",
            "What evidence supports the most important claims?",
            "What uncertainties, limitations, or tradeoffs matter?",
        ]

    cleaned = [str(q).strip() for q in questions if str(q).strip()]
    return cleaned[:5] if len(cleaned) >= 3 else cleaned


async def generate_candidate(
    original_question: str,
    sub_question: str,
    domain: str,
    index: int,
) -> CandidateResult:
    persona = get_domain_persona(domain)

    messages = [
        {
            "role": "system",
            "content": (
                f"{persona}\n\n"
                "Answer the assigned research sub-question. "
                "Be accurate, specific, appropriately hedged, and concise. "
                "Do not invent citations or unsupported facts."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Original research question:\n{original_question}\n\n"
                f"Sub-question:\n{sub_question}"
            ),
        },
    ]

    try:
        answer = await chat_complete(
            messages=messages,
            model=PRODUCER_MODEL,
            temperature=0.6 + index * 0.1,
            timeout=45.0,
        )
    except Exception as exc:
        answer = f"[candidate failed: {exc}]"

    return CandidateResult(
        index=index,
        answer=answer,
        score=0.0,
        judge_reasoning="Not judged yet.",
    )


async def best_of_n_for_subquestion(
    original_question: str,
    sub_question: str,
    domain: str,
    n: int = 3,
) -> SubAnswer:
    candidate_tasks = [
        generate_candidate(original_question, sub_question, domain, i + 1)
        for i in range(n)
    ]

    candidates = await asyncio.gather(*candidate_tasks, return_exceptions=True)

    safe_candidates: list[CandidateResult] = []

    for i, item in enumerate(candidates, start=1):
        if isinstance(item, Exception):
            safe_candidates.append(
                CandidateResult(
                    index=i,
                    answer=f"[candidate exception: {item}]",
                    score=0.0,
                    judge_reasoning=str(item),
                )
            )
        else:
            safe_candidates.append(item)

    judge_tasks = [
        judge_candidate(
            question=original_question,
            sub_question=sub_question,
            candidate_answer=c.answer,
            index=c.index,
        )
        for c in safe_candidates
    ]

    scores = await asyncio.gather(*judge_tasks, return_exceptions=True)

    for candidate, score in zip(safe_candidates, scores):
        if isinstance(score, Exception):
            candidate.score = 0.0
            candidate.judge_reasoning = f"Judge exception: {score}"
        else:
            candidate.score = score.score
            candidate.judge_reasoning = score.reasoning

    safe_candidates.sort(key=lambda c: c.score, reverse=True)
    selected = safe_candidates[0]

    print(f"\n[best-of-n] sub_question={sub_question}")

    for c in sorted(safe_candidates, key=lambda x: x.index):
        marker = " SELECTED" if c.index == selected.index else ""
        print(f"candidate_{c.index} score={c.score:.2f}{marker}")

    return SubAnswer(
        sub_question=sub_question,
        selected_answer=selected.answer,
        candidates=safe_candidates,
    )


async def sequential_baseline(
    question: str,
    sub_questions: list[str],
    domain: str,
    n: int,
) -> float:
    start = time.perf_counter()

    for sub_q in sub_questions:
        for i in range(n):
            await generate_candidate(question, sub_q, domain, i + 1)

    return time.perf_counter() - start


async def synthesize_brief(
    question: str,
    domain: str,
    sub_answers: list[SubAnswer],
) -> str:
    persona = get_domain_persona(domain)

    evidence = "\n\n".join(
        [
            f"Sub-question: {sa.sub_question}\nSelected answer:\n{sa.selected_answer}"
            for sa in sub_answers
        ]
    )

    messages = [
        {"role": "system", "content": f"{persona}\n\n{SYNTHESIZE_PROMPT}"},
        {
            "role": "user",
            "content": (
                f"Original research question:\n{question}\n\n"
                f"Selected sub-answers:\n{evidence}"
            ),
        },
    ]

    return await chat_complete(
        messages=messages,
        model=PRODUCER_MODEL,
        temperature=0.3,
        timeout=60.0,
    )


async def run_map_reduce(
    question: str,
    domain: str,
    n: int = 3,
) -> tuple[str, list[SubAnswer]]:
    sub_questions = await decompose_question(question, domain)

    print(f"\n[map] generated {len(sub_questions)} sub-questions")
    for i, q in enumerate(sub_questions, start=1):
        print(f"[map] {i}. {q}")

    parallel_start = time.perf_counter()

    tasks = [
        best_of_n_for_subquestion(question, sub_q, domain, n=n)
        for sub_q in sub_questions
    ]

    sub_answers = await asyncio.gather(*tasks, return_exceptions=True)

    safe_sub_answers: list[SubAnswer] = []

    for sub_q, item in zip(sub_questions, sub_answers):
        if isinstance(item, Exception):
            safe_sub_answers.append(
                SubAnswer(
                    sub_question=sub_q,
                    selected_answer=f"[sub-question failed: {item}]",
                    candidates=[],
                )
            )
        else:
            safe_sub_answers.append(item)

    parallel_elapsed = time.perf_counter() - parallel_start

    sequential_elapsed = await sequential_baseline(
        question=question,
        sub_questions=sub_questions,
        domain=domain,
        n=n,
    )

    print(
        f"\n[parallel] {len(sub_questions)} sub-questions x {n} candidates "
        f"= {len(sub_questions) * n} calls in {parallel_elapsed:.2f}s"
    )
    print(f"[sequential] baseline candidate generation time: {sequential_elapsed:.2f}s")

    brief = await synthesize_brief(question, domain, safe_sub_answers)

    return brief, safe_sub_answers