"""
reflection/loop.py
Reflexion-style producer–critic loop.

Each iteration:
  1. Critic  (CRITIC_MODEL)   evaluates the current draft ? structured JSON rubric
  2. Producer (PRODUCER_MODEL) revises the draft using the critic's instructions
  3. Plateau / regression detection prevents infinite loops

Stop conditions (whichever triggers first):
  • aggregate score ? REFLECTION_THRESHOLD
  • MAX_REFLECTION_ITERATIONS reached
  • Score improvement < PLATEAU_DELTA for two consecutive iterations (plateau/regression)
"""
from __future__ import annotations

import difflib
import logging
import os
from dataclasses import dataclass, field

from prompts import CRITIC, PRODUCER_REVISE, DOMAIN_PERSONAS
from llm.client import chat_complete, parse_json, PRODUCER_MODEL, CRITIC_MODEL

logger = logging.getLogger(__name__)

MAX_ITERATIONS = int(os.getenv("MAX_REFLECTION_ITERATIONS", "3"))
THRESHOLD      = float(os.getenv("REFLECTION_THRESHOLD", "7.5"))
PLATEAU_DELTA  = 0.10    # minimum improvement to continue iterating


# ?? Data classes ??????????????????????????????????????????????????????????????

@dataclass
class RubricScores:
    factual_grounding:     float
    completeness:          float
    internal_consistency:  float
    tone:                  float
    no_unsupported_claims: float
    aggregate:             float
    revision_instructions: list[str]
    summary:               str

    def one_line(self) -> str:
        return (
            f"factual={self.factual_grounding:.1f}  "
            f"complete={self.completeness:.1f}  "
            f"consistent={self.internal_consistency:.1f}  "
            f"tone={self.tone:.1f}  "
            f"no_unsupported={self.no_unsupported_claims:.1f}  "
            f"? aggregate={self.aggregate:.2f}"
        )


@dataclass
class ReflectionIteration:
    iteration: int
    draft:     str
    scores:    RubricScores


@dataclass
class ReflectionResult:
    final_draft:  str
    iterations:   list[ReflectionIteration] = field(default_factory=list)
    converged:    bool = False
    stop_reason:  str  = "max_iterations"
    # "threshold_met" | "max_iterations" | "plateau"


# ?? Critic ????????????????????????????????????????????????????????????????????

async def _run_critic(draft: str, question: str, domain: str) -> RubricScores:
    """Call the critic model and parse its structured rubric JSON."""
    domain_note = f"Domain context for tone evaluation: {domain} research."
    messages = [
        {"role": "system", "content": f"{CRITIC}\n\n{domain_note}"},
        {
            "role": "user",
            "content": (
                f"Research question: {question}\n\n"
                f"Draft brief to evaluate:\n\n{draft}"
            ),
        },
    ]
    try:
        raw = await chat_complete(
            messages, model=CRITIC_MODEL,
            temperature=0.2, max_tokens=800, json_mode=True,
        )
        data = parse_json(raw)
        scores_d = data.get("scores", {})
        agg      = float(data.get("aggregate", 0.0))
        insts    = data.get("revision_instructions", [])

        if not isinstance(insts, list):
            insts = [str(insts)]
        # Anti-rubber-stamp: ensure at least one instruction exists
        if not insts or all(not str(i).strip() for i in insts):
            insts = ["(Critic returned no specific instructions — manual review recommended)"]

        return RubricScores(
            factual_grounding=float(scores_d.get("factual_grounding", 5)),
            completeness=float(scores_d.get("completeness", 5)),
            internal_consistency=float(scores_d.get("internal_consistency", 5)),
            tone=float(scores_d.get("tone", 5)),
            no_unsupported_claims=float(scores_d.get("no_unsupported_claims", 5)),
            aggregate=agg,
            revision_instructions=[str(i) for i in insts],
            summary=str(data.get("summary", "")),
        )

    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("[critic] parse failed (%s) — using neutral fallback scores", exc)
        return RubricScores(
            5.0, 5.0, 5.0, 5.0, 5.0,
            aggregate=5.0,
            revision_instructions=[f"Critic parse error: {exc}"],
            summary="Parse error — treat as needing review",
        )


# ?? Producer revision ?????????????????????????????????????????????????????????

async def _run_producer_revision(
    question: str, domain: str, draft: str, scores: RubricScores
) -> str:
    """Ask the producer to revise the draft based on critic feedback."""
    persona = DOMAIN_PERSONAS.get(domain, DOMAIN_PERSONAS["general"])
    instructions_text = "\n".join(f"  - {inst}" for inst in scores.revision_instructions)

    messages = [
        {"role": "system", "content": f"{persona.strip()}\n\n{PRODUCER_REVISE}"},
        {
            "role": "user",
            "content": (
                f"Original research question: {question}\n\n"
                f"Current draft:\n\n{draft}\n\n"
                f"Critic scores: {scores.one_line()}\n\n"
                f"Critic summary: {scores.summary}\n\n"
                f"Revision instructions:\n{instructions_text}"
            ),
        },
    ]
    return await chat_complete(
        messages, model=PRODUCER_MODEL,
        temperature=0.4, max_tokens=1400,
    )


# ?? Diff utility ??????????????????????????????????????????????????????????????

def make_diff(old: str, new: str, label_old: str = "iter-1", label_new: str = "final") -> str:
    """Return a unified diff between two draft strings (truncated to 100 lines)."""
    diff = list(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=label_old,
        tofile=label_new,
        lineterm="",
    ))
    if not diff:
        return "(no textual changes detected)"
    if len(diff) > 100:
        diff = diff[:100] + ["... (diff truncated at 100 lines)\n"]
    return "".join(diff)


# ?? Main reflection loop ??????????????????????????????????????????????????????

async def run(
    question:      str,
    domain:        str,
    initial_draft: str,
) -> ReflectionResult:
    """
    Reflexion loop: critic evaluates, producer revises, repeat.

    Returns a ReflectionResult containing:
      - final_draft: the best version produced
      - iterations: list of (iteration, draft, scores) for full traceability
      - converged + stop_reason: why the loop terminated
    """
    logger.info(
        "[reflection] starting loop | max_iter=%d  threshold=%.1f",
        MAX_ITERATIONS, THRESHOLD,
    )

    iterations:     list[ReflectionIteration] = []
    draft           = initial_draft
    prev_aggregate  = -1.0
    stop_reason     = "max_iterations"

    for i in range(1, MAX_ITERATIONS + 1):
        _sep = "?" * 62
        print(f"\n{_sep}")
        print(f"  REFLECTION  ITERATION {i} / {MAX_ITERATIONS}")
        print(_sep)

        # ?? Critic step ???????????????????????????????????????????????????
        scores = await _run_critic(draft, question, domain)
        iterations.append(ReflectionIteration(iteration=i, draft=draft, scores=scores))

        print(f"[critic] {scores.one_line()}")
        print(f"[critic] summary: {scores.summary}")
        print(f"[critic] {len(scores.revision_instructions)} revision instruction(s):")
        for inst in scores.revision_instructions:
            print(f"         • {inst}")

        # ?? Threshold check ???????????????????????????????????????????????
        if scores.aggregate >= THRESHOLD:
            print(
                f"\n[reflection] ? threshold {THRESHOLD} met "
                f"(aggregate={scores.aggregate:.2f}) — stopping"
            )
            stop_reason = "threshold_met"
            break

        # ?? Plateau / regression check ????????????????????????????????????
        if i > 1:
            delta = scores.aggregate - prev_aggregate
            if delta < PLATEAU_DELTA:
                print(
                    f"\n[reflection] ?  plateau/regression detected "
                    f"(?={delta:+.3f} < {PLATEAU_DELTA}) — stopping early"
                )
                stop_reason = "plateau"
                break

        prev_aggregate = scores.aggregate

        if i == MAX_ITERATIONS:
            print("[reflection] max iterations reached")
            break

        # ?? Producer revision step ????????????????????????????????????????
        print(f"\n[producer] revising draft ? iteration {i + 1} ...")
        draft = await _run_producer_revision(question, domain, draft, scores)

    # ?? Score trace ???????????????????????????????????????????????????????
    print(f"\n{'?' * 62}")
    print("  REFLECTION SCORE TRACE")
    print(f"{'?' * 62}")
    for it in iterations:
        print(f"  Iter {it.iteration}: {it.scores.one_line()}")

    # ?? Before / after diff ???????????????????????????????????????????????
    if len(iterations) >= 2:
        diff_text = make_diff(
            iterations[0].draft, draft,
            label_old=f"iter-1",
            label_new=f"iter-{len(iterations)}",
        )
        print(f"\n[diff] iter-1 ? iter-{len(iterations)}:\n")
        print(diff_text)
    else:
        print("\n[diff] only one iteration ran — no diff to show")

    return ReflectionResult(
        final_draft=draft,
        iterations=iterations,
        converged=(stop_reason == "threshold_met"),
        stop_reason=stop_reason,
    )