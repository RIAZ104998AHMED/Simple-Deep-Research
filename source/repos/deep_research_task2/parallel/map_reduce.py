"""
parallel/map_reduce.py
Map-Reduce + Best-of-N domain agent.

Pipeline:
  1. Decompose  — LLM breaks the question into 3–5 independent sub-questions
  2. Fan-out    — asyncio.gather runs ALL sub-questions concurrently;
                  each sub-question fans out again to N candidates in parallel
  3. Judge      — LLM-as-judge scores candidates on correctness/specificity/hedging
                  and selects the best one per sub-question
  4. Fan-in     — LLM synthesises the selected answers into a research brief

Failure handling:
  - A candidate that times out or throws returns None; the judge skips it.
  - If all N candidates for a sub-question fail, a fallback stub is used.
  - A failure in one sub-question branch does not crash the whole pipeline.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

from prompts import DECOMPOSE, JUDGE, SYNTHESIZE, DOMAIN_PERSONAS
from llm.client import chat_complete, parse_json, PRODUCER_MODEL

logger = logging.getLogger(__name__)

BEST_OF_N = int(os.getenv("BEST_OF_N", "3"))


# ?? Data classes ??????????????????????????????????????????????????????????????

@dataclass
class CandidateScore:
    index:       int
    correctness: float
    specificity: float
    hedging:     float
    aggregate:   float
    reason:      str


@dataclass
class SubQuestionResult:
    sub_question:   str
    candidates:     list[str]
    scores:         list[CandidateScore]
    best_index:     int
    best_answer:    str
    failed_indices: list[int] = field(default_factory=list)


@dataclass
class MapReduceResult:
    question:         str
    domain:           str
    sub_questions:    list[str]
    sub_results:      list[SubQuestionResult]
    brief:            str
    parallel_time_s:  float
    sequential_est_s: float


# ?? Step 1: Decompose ?????????????????????????????????????????????????????????

async def decompose(question: str, domain: str) -> list[str]:
    """Break the question into 3–5 independent sub-questions."""
    persona = DOMAIN_PERSONAS.get(domain, DOMAIN_PERSONAS["general"])
    messages = [
        {"role": "system", "content": f"{persona.strip()}\n\n{DECOMPOSE}"},
        {"role": "user",   "content": question},
    ]
    raw = await chat_complete(
        messages, model=PRODUCER_MODEL,
        temperature=0.3, max_tokens=512, json_mode=True,
    )
    try:
        data = parse_json(raw)
        subs = data.get("sub_questions", [])
        if not isinstance(subs, list) or len(subs) < 2:
            raise ValueError(f"Expected ?2 sub-questions, got: {subs}")
        return [str(s).strip() for s in subs[:5]]
    except (ValueError, KeyError) as exc:
        logger.warning("[decompose] failed (%s) — using original question as single sub-q", exc)
        return [question]


# ?? Step 2: Generate one candidate ???????????????????????????????????????????

async def _generate_candidate(
    sub_question: str,
    persona: str,
    candidate_idx: int,
) -> tuple[int, str | None]:
    """
    Generate one candidate answer for a sub-question.
    Returns (index, text) or (index, None) on any error — never raises.
    """
    messages = [
        {"role": "system", "content": persona.strip()},
        {
            "role": "user",
            "content": (
                "Answer the following research sub-question in 100–200 words. "
                "Be specific, accurate, and appropriately hedged.\n\n"
                f"Sub-question: {sub_question}"
            ),
        },
    ]
    try:
        text = await chat_complete(
            messages,
            model=PRODUCER_MODEL,
            temperature=0.5 + candidate_idx * 0.1,   # slight diversity across candidates
            max_tokens=400,
        )
        return candidate_idx, text
    except Exception as exc:                           # noqa: BLE001
        logger.warning(
            "[candidate] idx=%d sub=%.60r failed: %s",
            candidate_idx, sub_question, exc,
        )
        return candidate_idx, None


async def generate_candidates(
    sub_question: str,
    persona: str,
    n: int = BEST_OF_N,
) -> list[str | None]:
    """Fan-out: generate N candidates in parallel. Returns list of length n."""
    pairs = await asyncio.gather(
        *[_generate_candidate(sub_question, persona, i) for i in range(n)]
    )
    return [text for _, text in sorted(pairs, key=lambda p: p[0])]


# ?? Step 3: Judge ?????????????????????????????????????????????????????????????

async def judge_candidates(
    sub_question: str,
    candidates: list[str | None],
) -> tuple[int, list[CandidateScore]]:
    """
    Score all valid candidates with LLM-as-judge.
    Returns (best_index, list[CandidateScore]).
    Falls back to picking first valid candidate if judge call fails.
    """
    valid = {i: c for i, c in enumerate(candidates) if c is not None}

    if not valid:
        raise RuntimeError(
            f"All {len(candidates)} candidates failed for sub-question: "
            f"{sub_question[:80]!r}"
        )

    if len(valid) == 1:
        idx = next(iter(valid))
        fallback_score = CandidateScore(idx, 5.0, 5.0, 5.0, 5.0, "only valid candidate")
        return idx, [fallback_score]

    candidates_block = "\n\n".join(
        f"[Candidate {i}]\n{text}" for i, text in valid.items()
    )
    messages = [
        {"role": "system", "content": JUDGE},
        {
            "role": "user",
            "content": (
                f"Sub-question: {sub_question}\n\n"
                f"Candidates to evaluate:\n{candidates_block}"
            ),
        },
    ]
    try:
        raw = await chat_complete(
            messages, model=PRODUCER_MODEL,
            temperature=0.1, max_tokens=700, json_mode=True,
        )
        data = parse_json(raw)
        raw_scores = data.get("scores", [])
        scores: list[CandidateScore] = []

        for s in raw_scores:
            idx = int(s.get("candidate_index", 0))
            if idx not in valid:
                continue
            c   = float(s.get("correctness", 5))
            sp  = float(s.get("specificity", 5))
            h   = float(s.get("hedging", 5))
            agg = round((c + sp + h) / 3, 1)
            scores.append(CandidateScore(
                index=idx, correctness=c, specificity=sp,
                hedging=h, aggregate=agg,
                reason=str(s.get("brief_reason", "")),
            ))

        if not scores:
            raise ValueError("judge returned no parseable scores")

        best_idx = max(scores, key=lambda s: s.aggregate).index

        # Log all scores so selection is auditable
        for sc in sorted(scores, key=lambda s: s.index):
            marker = "  ? SELECTED" if sc.index == best_idx else ""
            logger.info(
                "[judge]   candidate %d | corr=%.1f  spec=%.1f  hedge=%.1f  "
                "agg=%.1f  %s%s",
                sc.index, sc.correctness, sc.specificity,
                sc.hedging, sc.aggregate, sc.reason[:50], marker,
            )

        return best_idx, scores

    except Exception as exc:                           # noqa: BLE001
        logger.warning("[judge] call failed (%s) — picking first valid candidate", exc)
        idx = next(iter(valid))
        return idx, [CandidateScore(idx, 5.0, 5.0, 5.0, 5.0, f"judge error: {exc}")]


# ?? Step 4: Synthesise ????????????????????????????????????????????????????????

async def synthesise(
    question: str,
    domain: str,
    sub_results: list[SubQuestionResult],
) -> str:
    """Reduce selected sub-answers into a single coherent research brief."""
    persona = DOMAIN_PERSONAS.get(domain, DOMAIN_PERSONAS["general"])

    sub_block = "\n\n".join(
        f"**Sub-question {i+1}:** {r.sub_question}\n**Best Answer:** {r.best_answer}"
        for i, r in enumerate(sub_results)
    )

    messages = [
        {"role": "system", "content": f"{persona.strip()}\n\n{SYNTHESIZE}"},
        {
            "role": "user",
            "content": (
                f"Original research question: {question}\n\n"
                f"Sub-question answers (pre-selected best candidates):\n\n{sub_block}"
            ),
        },
    ]
    return await chat_complete(
        messages, model=PRODUCER_MODEL,
        temperature=0.4, max_tokens=1400,
    )


# ?? Sequential timing estimate ????????????????????????????????????????????????

async def _estimate_sequential_time(
    n_sub_questions: int,
    n_candidates: int,
    persona: str,
) -> float:
    """
    Time a single warm API call and extrapolate to the full sequential workload.
    Extrapolation: n_sub_questions × (n_candidates + 1 judge call).
    """
    messages = [
        {"role": "system", "content": persona.strip()},
        {"role": "user",   "content": "In one sentence, what is research?"},
    ]
    t0 = time.perf_counter()
    try:
        await chat_complete(messages, model=PRODUCER_MODEL, temperature=0.1, max_tokens=40)
    except Exception:                                  # noqa: BLE001
        return 0.0
    single_call = time.perf_counter() - t0
    return round(single_call * n_sub_questions * (n_candidates + 1), 1)


# ?? Main entry point ??????????????????????????????????????????????????????????

async def run(question: str, domain: str) -> MapReduceResult:
    """
    Full map-reduce pipeline for one research question.

    Returns a MapReduceResult containing sub-questions, per-sub scores,
    the synthesised brief, and timing data (parallel vs sequential estimate).
    """
    persona = DOMAIN_PERSONAS.get(domain, DOMAIN_PERSONAS["general"])
    n = BEST_OF_N

    # ?? 1. Decompose ??????????????????????????????????????????????????????
    logger.info("[map_reduce] decomposing question (domain=%s)...", domain)
    sub_questions = await decompose(question, domain)
    logger.info("[map_reduce] %d sub-questions generated", len(sub_questions))
    for i, sq in enumerate(sub_questions, 1):
        logger.info("[map_reduce]   %d. %s", i, sq)

    total_leaf_calls = len(sub_questions) * n
    logger.info(
        "[map_reduce] fanning out: %d sub-questions × %d candidates = %d parallel calls",
        len(sub_questions), n, total_leaf_calls,
    )

    # ?? 2. Parallel fan-out ???????????????????????????????????????????????
    t_par_start = time.perf_counter()

    async def _handle_sub(sq: str) -> SubQuestionResult:
        """Generate + judge one sub-question; isolated so one failure doesn't break others."""
        try:
            candidates = await generate_candidates(sq, persona, n)
            failed = [i for i, c in enumerate(candidates) if c is None]
            if failed:
                logger.warning(
                    "[map_reduce] sub=%.55r: %d/%d candidates failed",
                    sq, len(failed), n,
                )
            best_idx, scores = await judge_candidates(sq, candidates)
            return SubQuestionResult(
                sub_question=sq,
                candidates=[c or "[GENERATION FAILED]" for c in candidates],
                scores=scores,
                best_index=best_idx,
                best_answer=candidates[best_idx] or "[GENERATION FAILED]",
                failed_indices=failed,
            )
        except Exception as exc:                       # noqa: BLE001
            logger.error("[map_reduce] sub-question branch failed: %s — using stub", exc)
            stub = f"[Error processing sub-question: {exc}]"
            return SubQuestionResult(
                sub_question=sq,
                candidates=[stub],
                scores=[CandidateScore(0, 0, 0, 0, 0.0, str(exc))],
                best_index=0,
                best_answer=stub,
                failed_indices=list(range(n)),
            )

    sub_results = list(await asyncio.gather(*[_handle_sub(sq) for sq in sub_questions]))
    parallel_time = round(time.perf_counter() - t_par_start, 2)

    # ?? Sequential estimate ???????????????????????????????????????????????
    seq_est = await _estimate_sequential_time(len(sub_questions), n, persona)

    speedup = (seq_est / parallel_time) if parallel_time > 0 and seq_est > 0 else 0
    logger.info(
        "[map_reduce] ?  parallel=%.2fs  sequential_est=%.1fs  speedup=%.1fx",
        parallel_time, seq_est, speedup,
    )

    # ?? 3. Synthesise ?????????????????????????????????????????????????????
    logger.info("[map_reduce] synthesising brief from %d sub-answers...", len(sub_results))
    brief = await synthesise(question, domain, sub_results)

    return MapReduceResult(
        question=question,
        domain=domain,
        sub_questions=sub_questions,
        sub_results=sub_results,
        brief=brief,
        parallel_time_s=parallel_time,
        sequential_est_s=seq_est,
    )