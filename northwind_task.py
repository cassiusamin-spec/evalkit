from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import Sample, json_dataset
from inspect_ai.model import ChatMessageAssistant, ChatMessageUser
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, model_graded_qa, scorer, stderr
from inspect_ai.solver import TaskState, generate, system_message

from graders.deterministic import GRADERS  # reuse evalkit's rule-based checks

JUDGE_MODEL = "google/gemini-3.5-flash-lite"
RUBRICS = {p.stem: p.read_text(encoding="utf-8") for p in Path("graders/rubrics").glob("*.md")}


def record_to_sample(record):
    messages = [ChatMessageUser(content=m["content"]) if m["role"] == "user"
                else ChatMessageAssistant(content=m["content"])
                for m in record["input"]["messages"]]
    judge = next((g for g in record["graders"] if g["type"] == "llm_judge"), {"rubric": "correctness"})
    return Sample(
        id=record["id"],
        input=messages,
        target=record["reference"],
        metadata={**record["tags"],
                  "rubric_text": RUBRICS[judge["rubric"]],
                  "rules": [g for g in record["graders"] if g["type"] != "llm_judge"],
                  "case": record},
    )


PROMPT = Path("prompts/support_v1.txt").read_text(encoding="utf-8")
SYSTEM = PROMPT.replace("{", "{{").replace("}", "}}")


JUDGE_TEMPLATE = """You are grading a response from a customer-support assistant.

[Marking guide]
{rubric_text}

[Conversation]
{question}

[What a good answer does]
{criterion}

[Response to grade]
{answer}

{instructions}"""

JUDGE_INSTRUCTIONS = """Think through the marking guide step by step. It scores 1 to 5; 4 or 5 is acceptable.
Then finish with exactly one line: GRADE: C if the response is acceptable, or GRADE: I if it is not."""

@scorer(metrics=[accuracy(), stderr()])
def rules():
    async def score(state: TaskState, target: Target) -> Score:
        output = state.output.completion
        failed = []
        for spec in state.metadata["rules"]:
            g = GRADERS[spec["type"]](spec, state.metadata["case"], output)
            if not g.passed:
                failed.append(f"{g.grader}: {g.rationale}")
        return Score(value=INCORRECT if failed else CORRECT, answer=output,
                     explanation="; ".join(failed) or "all rule checks passed")
    return score

@task
def northwind():
    return Task(
        dataset=json_dataset("cases/northwind_inspect.jsonl", record_to_sample),
        solver=[system_message(SYSTEM), generate()],
        scorer=[model_graded_qa(template=JUDGE_TEMPLATE, instructions=JUDGE_INSTRUCTIONS,
                                model=JUDGE_MODEL, include_history=True),
                rules()],
    )
