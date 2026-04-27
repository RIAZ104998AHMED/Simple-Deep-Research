"""
eval/run_routing_eval.py
Run the router against the hand-labeled eval set and print a precision/recall table.

Usage:
    python -m eval.run_routing_eval
    # or via app.py:
    python app.py --eval
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from eval.routing_eval_set import EVAL_SET
from router.supervisor import classify

logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")

DOMAINS = ["scientific", "historical", "financial", "general", "fallback"]


async def _classify_one(item: dict) -> dict:
    result = await classify(item["question"])
    return {
        "question":    item["question"][:65],
        "label":       item["label"],
        "predicted":   result.domain,
        "confidence":  result.confidence,
        "guardrail":   result.guardrail_triggered,
        "correct":     result.domain == item["label"],
        "adversarial": item["adversarial"],
    }


async def main() -> None:
    print("\n" + "=" * 78)
    print("  ROUTING ACCURACY EVALUATION")
    print("=" * 78)

    rows = await asyncio.gather(*[_classify_one(item) for item in EVAL_SET])

    # ── Per-question results ────────────────────────────────────────────────
    print(f"\n  {'Question':<48} {'Label':<12} {'Predicted':<12} OK")
    print("  " + "-" * 78)
    for r in rows:
        tick = "✅" if r["correct"] else "❌"
        flag = " [ADV]" if r["adversarial"] else ""
        print(f"  {r['question']:<48} {r['label']:<12} {r['predicted']:<12} {tick}{flag}")

    # ── Precision / recall per class ────────────────────────────────────────
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)

    for r in rows:
        if r["correct"]:
            tp[r["label"]] += 1
        else:
            fp[r["predicted"]] += 1
            fn[r["label"]]     += 1

    print(f"\n  {'Domain':<14} {'TP':>4} {'FP':>4} {'FN':>4} {'Precision':>10} {'Recall':>8}")
    print("  " + "-" * 48)
    for d in DOMAINS:
        t = tp[d]; p = fp[d]; f = fn[d]
        prec = t / (t + p) if (t + p) > 0 else float("nan")
        rec  = t / (t + f) if (t + f) > 0 else float("nan")
        ps   = f"{prec:.2f}" if prec == prec else "  N/A"
        rs   = f"{rec:.2f}"  if rec  == rec  else "  N/A"
        print(f"  {d:<14} {t:>4} {p:>4} {f:>4} {ps:>10} {rs:>8}")

    total   = len(rows)
    correct = sum(1 for r in rows if r["correct"])
    adv     = [r for r in rows if r["adversarial"]]
    adv_ok  = sum(1 for r in adv if r["correct"])

    print(f"\n  Overall accuracy              : {correct}/{total} = {correct/total:.1%}")
    print(f"  Guardrail accuracy (adversarial): {adv_ok}/{len(adv)}")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    asyncio.run(main())