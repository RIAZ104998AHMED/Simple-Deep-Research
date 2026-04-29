# Deep Research Assistant — Task 2

An agentic Deep Research system that takes a complex research question and produces
a structured, fact-checked brief through three stages:

| Stage | Module | Pattern |
|-------|--------|---------|
| **Routing** | `router/supervisor.py` | LLM supervisor + guardrail |
| **Parallelisation** | `parallel/map_reduce.py` | Map-Reduce + Best-of-N |
| **Reflection** | `reflection/loop.py` | Producer–Critic (Reflexion) |

---

## Project structure

```
deep_research_task2/
├── app.py                         Main entry point
├── requirments.txt                Dependencies
├── .env.example                   Environment template
│
├── llm/
│   ├── __init__.py
│   └── client.py                  Central async OpenRouter wrapper
│
├── router/
│   ├── __init__.py
│   └── supervisor.py              Two-pass guardrail + domain classifier
│
├── parallel/
│   ├── __init__.py
│   └── map_reduce.py              Decompose → fan-out → Best-of-N → reduce
│
├── reflection/
│   ├── __init__.py
│   └── loop.py                    Producer-Critic Reflexion loop
│
├── prompts/                       ALL prompts live here — none in logic files
│   ├── __init__.py
│   ├── router_classify.md         Guardrail + 5-domain classification prompt
│   ├── fallback_refusal.md        Graceful refusal template
│   ├── decompose.md               Sub-question decomposition
│   ├── judge.md                   LLM-as-judge rubric
│   ├── synthesize.md              Fan-in synthesis
│   ├── critic.md                  Adversarial critic rubric
│   ├── producer_revise.md         Producer revision instructions
│   └── domain_personas.py         4 domain system prompts
│
├── eval/
│   ├── __init__.py
│   ├── routing_eval_set.py        12 hand-labeled questions
│   └── run_routing_eval.py        Precision/recall table
│
└── examples/
    ├── __init__.py
    └── sample_questions.py        12 sample questions with domain labels
```



## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | *(required)* | From https://openrouter.ai/keys |
| `ROUTER_MODEL` | `qwen/qwen3-235b-a22b:free` | Supervisor / guardrail |
| `PRODUCER_MODEL` | `qwen/qwen3-235b-a22b:free` | Domain agent + judge |
| `CRITIC_MODEL` | `meta-llama/llama-3.1-8b-instruct:free` | **Use different model** |
| `BEST_OF_N` | `3` | Candidates per sub-question |
| `MAX_REFLECTION_ITERATIONS` | `3` | Reflection loop limit |
| `REFLECTION_THRESHOLD` | `7.5` | Aggregate score to stop early |

---

## Architecture

```
User Question
      │
      ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ROUTER  ·  router/supervisor.py                                    │
│                                                                     │
│  Pass 1 — Guardrail                                                 │
│    Detects: injection / PII / disallowed → fallback + refusal       │
│                                                                     │
│  Pass 2 — LLM domain classification                                 │
│    scientific │ historical │ financial │ general │ fallback         │
└─────────────────────────┬───────────────────────────────────────────┘
                          │  RouterResult(domain, confidence, …)
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│  MAP-REDUCE + BEST-OF-N  ·  parallel/map_reduce.py                  │
│                                                                     │
│  1. Decompose  →  3–5 independent sub-questions                     │
│                                                                     │
│  2. Fan-out (asyncio.gather — ALL calls concurrent)                 │
│       sub-q 1 ──→ [cand0, cand1, cand2] ──→ judge → best           │
│       sub-q 2 ──→ [cand0, cand1, cand2] ──→ judge → best           │
│       sub-q 3 ──→ [cand0, cand1, cand2] ──→ judge → best           │
│                                                                     │
│  Judge rubric (0–10 each): correctness · specificity · hedging      │
│  Failure isolation: one bad branch never crashes the pipeline       │
│                                                                     │
│  3. Reduce  →  synthesise best answers into a research brief        │
└─────────────────────────┬───────────────────────────────────────────┘
                          │  Initial draft brief
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│  REFLECTION LOOP  ·  reflection/loop.py                             │
│                                                                     │
│  Iter 1:  Critic  →  rubric JSON (5 dimensions × 0–10)             │
│           Producer  →  revised draft                                │
│  Iter 2:  Critic  →  rubric JSON                                    │
│           Producer  →  revised draft                                │
│  Iter 3:  Critic  →  rubric JSON  (stop)                           │
│                                                                     │
│  Stop conditions:                                                   │
│    • aggregate ≥ threshold (default 7.5)    → "threshold_met"      │
│    • max_iterations reached (default 3)     → "max_iterations"     │
│    • Δaggregate < 0.10 two iterations       → "plateau"            │
│                                                                     │
│  Output: score trace + unified diff iter-1 → iter-N                │
└─────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
                   Final Research Brief
```

---

## Routing accuracy

*Run `python app.py --eval` and paste results here.*

| Domain | TP | FP | FN | Precision | Recall |
|--------|----|----|-----|-----------|--------|
| scientific | — | — | — | — | — |
| historical | — | — | — | — | — |
| financial  | — | — | — | — | — |
| general    | — | — | — | — | — |
| fallback   | — | — | — | — | — |
| **Overall** | | | | | |

Eval set: 12 questions (3 scientific · 3 historical · 2 financial · 2 general · 2 adversarial).

---

## Example console output

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  QUESTION : What are the main bottlenecks of current solid-state battery research?
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[ROUTER]
  Domain      : SCIENTIFIC
  Confidence  : 0.97
  Reasoning   : Empirical engineering question about materials science.

[MAP-REDUCE]  Sub-questions decomposed (4):
  1. What ionic conductivity limitations exist in current solid electrolytes?
  2. How does anode–electrolyte interface resistance limit performance?
  3. What manufacturing challenges prevent commercial-scale production?
  4. How do dendrite formation and mechanical stress affect cycle life?

[parallel] 4 sub-questions × 3 candidates = 12 calls in 5.8s; sequential estimate: ~42s

[MAP-REDUCE]  Best-of-N candidate scores:
  Sub-q: What ionic conductivity limitations exist ...
    candidate 0 | corr=7.2  spec=6.8  hedge=7.5  agg=7.2
    candidate 1 | corr=8.1  spec=8.4  hedge=7.8  agg=8.1  ← SELECTED
    candidate 2 | corr=6.5  spec=5.9  hedge=6.1  agg=6.2

  ⏱  parallel=5.8s | sequential_est=~42.0s | speedup=~7.2x

──────────────────────────────────────────────────────────────
  REFLECTION  ITERATION 1 / 3
──────────────────────────────────────────────────────────────
[critic] factual=6.5  complete=5.8  consistent=7.0  tone=7.5  no_unsupported=6.0  → aggregate=6.56
[critic] 2 revision instruction(s):
         • Quantify the ionic conductivity gap (~10 mS/cm target vs ~1 mS/cm current)
         • Distinguish sulphide vs oxide electrolyte trade-offs explicitly

──────────────────────────────────────────────────────────────
  REFLECTION  ITERATION 2 / 3
──────────────────────────────────────────────────────────────
[critic] factual=7.8  complete=7.6  consistent=7.8  tone=7.5  no_unsupported=7.2  → aggregate=7.58
✅ threshold 7.5 met (aggregate=7.58) — stopping

REFLECTION SCORE TRACE
  Iter 1: factual=6.5  complete=5.8  consistent=7.0  tone=7.5  no_unsupported=6.0  → 6.56
  Iter 2: factual=7.8  complete=7.6  consistent=7.8  tone=7.5  no_unsupported=7.2  → 7.58

[diff] iter-1 → iter-2:
--- iter-1
+++ iter-2
@@ -5,6 +5,9 @@
-Solid electrolytes remain a key hurdle.
+Solid electrolytes remain a key hurdle: current ionic conductivities
+typically reach ~1 mS/cm, well below the ~10 mS/cm target needed for
+commercially viable charge rates.
```

---

## Notes on evaluator–producer collusion

**What collusion looks like:** When `PRODUCER_MODEL == CRITIC_MODEL`, the critic
tends to award aggregate scores of 7.5+ on the first iteration regardless of actual
quality, with only superficial revision instructions like "improve clarity". This is
rubber-stamping — the model is reluctant to find fault with its own output.

**Mitigations implemented in this project:**

1. **Different model for critic** — `CRITIC_MODEL` defaults to a different model
   than `PRODUCER_MODEL`. A model that didn't write the draft is more willing to
   find fault with it.

2. **Forced concrete criticism** — `prompts/critic.md` contains a mandatory rule:
   *"You MUST identify at least ONE concrete weakness... revision_instructions MUST
   be a non-empty list of actionable bullet points. 'Improve quality' is not
   acceptable."*

3. **Adversarial framing** — the critic prompt explicitly says "be an adversarial
   reviewer, not a supportive one" and that a score of 10 "should be extremely rare".

4. **Plateau detection** — if `Δaggregate < 0.10` across consecutive iterations
   the loop terminates early. This prevents wasted iterations when collusion does
   occur (scores converge but no real improvement is happening).

---
