import argparse
import asyncio
import logging

from parallel.map_reduce import run_map_reduce
from reflection.loop import run_reflection_loop
from router.supervisor import classify, fallback_message

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
)


async def run(question: str) -> None:
    print("\nDeep Research Assistant")
    print("=======================")
    print(f"Question: {question}")

    route = await classify(question)

    if route.domain == "fallback" or route.guardrail_triggered:
        print("\n[fallback]")
        print(fallback_message(route.guardrail_reason))
        return

    draft, _sub_answers = await run_map_reduce(
        question=question,
        domain=route.domain,
        n=3,
    )

    print("\n[producer] initial synthesized draft")
    print("===================================")
    print(draft)

    final = await run_reflection_loop(
        question=question,
        domain=route.domain,
        initial_draft=draft,
        threshold=8.5,
        max_iterations=3,
    )

    print("\nFinal Research Brief")
    print("====================")
    print(final)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="*", help="Research question")
    args = parser.parse_args()

    question = " ".join(args.question).strip()

    if not question:
        question = input("Enter your research question: ").strip()

    asyncio.run(run(question))


if __name__ == "__main__":
    main()