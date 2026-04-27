"""
app.py
Deep Research Assistant — main entry point.

Full pipeline:
  Router (domain supervisor + guardrail)
    → Map-Reduce + Best-of-N (parallel fan-out)
    → Reflexion loop (producer–critic)

Usage:
    python app.py -q "Your research question here"
    python app.py --demo              # run all sample questions
    python app.py --demo --question 1 # run sample question #1 only
    python app.py --eval              # run routing accuracy eval set
    python app.py                     # interactive mode
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

# ── Load .env before anything else ───────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; rely on real env vars

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)-20s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("app")

# ── Pipeline imports ─────────────────────────────────────────────────────────
from router.supervisor   import classify, get_fallback_response
from parallel.map_reduce import run as map_reduce_run
from reflection.loop     import run as reflection_run
from examples.sample_questions import SAMPLE_QUESTIONS

DIVIDER = "━" * 72


# ── Core pipeline ─────────────────────────────────────────────────────────────

async def run_pipeline(question: str) -> None:
    """Run the full router → map-reduce → reflection pipeline for one question."""

    print(f"\n{DIVIDER}")
    print(f"  QUESTION : {question}")
    print(DIVIDER)

    # ══ 1. ROUTE ════════════════════════════════════════════════════════════
    result = await classify(question)

    print(f"\n[ROUTER]")
    print(f"  Domain      : {result.domain.upper()}")
    print(f"  Confidence  : {result.confidence:.2f}")
    print(f"  Reasoning   : {result.reasoning}")
    if result.guardrail_triggered:
        print(f"  ⚠️  Guardrail : {result.guardrail_reason}")

    # ══ FALLBACK PATH ════════════════════════════════════════════════════════
    if result.domain == "fallback":
        print("\n[FALLBACK] Generating refusal response ...")
        refusal = await get_fallback_response(result)
        print(f"\n  {refusal}")
        print(f"\n{DIVIDER}\n")
        return

    # ══ 2. MAP-REDUCE + BEST-OF-N ═══════════════════════════════════════════
    print(f"\n[MAP-REDUCE]  domain={result.domain.upper()}")
    mr = await map_reduce_run(question, result.domain)

    print(f"\n[MAP-REDUCE]  Sub-questions decomposed ({len(mr.sub_questions)}):")
    for i, sq in enumerate(mr.sub_questions, 1):
        print(f"  {i}. {sq}")

    print(f"\n[MAP-REDUCE]  Best-of-N candidate scores:")
    for sr in mr.sub_results:
        print(f"\n  Sub-q: {sr.sub_question[:68]}")
        for sc in sorted(sr.scores, key=lambda s: s.index):
            sel = "  ← SELECTED" if sc.index == sr.best_index else ""
            print(
                f"    candidate {sc.index} | "
                f"corr={sc.correctness:.1f}  spec={sc.specificity:.1f}  "
                f"hedge={sc.hedging:.1f}  agg={sc.aggregate:.1f}{sel}"
            )
        if sr.failed_indices:
            print(f"    ⚠️  failed candidates: {sr.failed_indices}")

    speedup = (
        mr.sequential_est_s / mr.parallel_time_s
        if mr.parallel_time_s > 0 and mr.sequential_est_s > 0 else 0
    )
    print(
        f"\n  ⏱  parallel={mr.parallel_time_s}s | "
        f"sequential_est=~{mr.sequential_est_s}s | "
        f"speedup=~{speedup:.1f}x"
    )

    print(f"\n[MAP-REDUCE]  Initial research brief (before reflection):\n")
    print(mr.brief)

    # ══ 3. REFLECTION LOOP ══════════════════════════════════════════════════
    print(f"\n{'═'*72}")
    print("  REFLECTION LOOP")
    print(f"{'═'*72}")

    rf = await reflection_run(question, result.domain, mr.brief)

    print(f"\n[REFLECTION]  Stop reason : {rf.stop_reason}")
    print(f"[REFLECTION]  Iterations  : {len(rf.iterations)}")
    print(f"[REFLECTION]  Converged   : {rf.converged}")

    print(f"\n{'═'*72}")
    print("  FINAL RESEARCH BRIEF")
    print(f"{'═'*72}\n")
    print(rf.final_draft)
    print(f"\n{DIVIDER}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deep Research Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python app.py -q \"What causes solid-state battery degradation?\"\n"
            "  python app.py --demo\n"
            "  python app.py --eval\n"
        ),
    )
    parser.add_argument("--question", "-q", type=str, help="Research question to process")
    parser.add_argument("--demo",  action="store_true", help="Run all sample questions")
    parser.add_argument("--eval",  action="store_true", help="Run routing accuracy eval")
    parser.add_argument("--index", "-i", type=int, default=None,
                        help="Run a specific sample question by index (1-based, use with --demo)")
    args = parser.parse_args()

    # ── API key check ──────────────────────────────────────────────────────
    if not os.getenv("OPENROUTER_API_KEY"):
        print("\n❌  OPENROUTER_API_KEY is not set.")
        print("    1. Copy .env.example to .env")
        print("    2. Add your key from https://openrouter.ai/keys")
        print("    3. Re-run this script\n")
        sys.exit(1)

    # ── Dispatch ───────────────────────────────────────────────────────────
    if args.eval:
        from eval.run_routing_eval import main as eval_main
        await eval_main()

    elif args.question:
        await run_pipeline(args.question)

    elif args.demo:
        questions = SAMPLE_QUESTIONS
        if args.index is not None:
            idx = args.index - 1
            if not (0 <= idx < len(questions)):
                print(f"❌  --index must be between 1 and {len(questions)}")
                sys.exit(1)
            questions = [questions[idx]]
        for q in questions:
            await run_pipeline(q["question"])

    else:
        # Interactive mode
        print(f"\nDeep Research Assistant  —  interactive mode")
        print("Type your research question and press Enter. Type 'quit' to exit.\n")
        while True:
            try:
                q = input("Question: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                break
            if not q:
                continue
            if q.lower() in ("quit", "exit", "q"):
                print("Goodbye.")
                break
            await run_pipeline(q)


if __name__ == "__main__":
    asyncio.run(main())