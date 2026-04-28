import asyncio
from collections import defaultdict

from eval.routing_eval_set import ROUTING_EVAL_SET
from router.supervisor import classify


async def main() -> None:
    labels = sorted({item["label"] for item in ROUTING_EVAL_SET})

    true_positive = defaultdict(int)
    predicted_count = defaultdict(int)
    actual_count = defaultdict(int)

    correct = 0

    for item in ROUTING_EVAL_SET:
        question = item["question"]
        expected = item["label"]

        result = await classify(question)
        predicted = result.domain

        print("\n---")
        print(f"question: {question}")
        print(f"expected: {expected}")
        print(f"predicted: {predicted}")
        print(f"confidence: {result.confidence:.2f}")

        actual_count[expected] += 1
        predicted_count[predicted] += 1

        if predicted == expected:
            correct += 1
            true_positive[expected] += 1

    print("\nRouting Accuracy")
    print("================")
    print(f"Overall accuracy: {correct}/{len(ROUTING_EVAL_SET)} = {correct / len(ROUTING_EVAL_SET):.2f}")

    print("\nPrecision per class")
    print("===================")

    for label in labels:
        denom = predicted_count[label]
        precision = true_positive[label] / denom if denom else 0.0
        print(f"{label}: {precision:.2f}")


if __name__ == "__main__":
    asyncio.run(main())