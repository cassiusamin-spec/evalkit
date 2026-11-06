from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Grade:
    """The one shape every grader returns, so the report never cares which grader ran."""
    grader: str
    score: float          # 0.0 - 1.0
    passed: bool
    rationale: str
    failure_type: Optional[str] = None  # e.g. wrong_policy, pii_leak, format_error

    def to_dict(self):
        return asdict(self)


def conversation_text(messages) -> str:
    """The customer's message, or for multi-turn cases the whole conversation with speaker labels."""
    if len(messages) == 1:
        return messages[0]["content"]
    who = {"user": "Customer", "assistant": "Assistant"}
    return "\n\n".join(f"{who.get(m['role'], m['role'])}: {m['content']}" for m in messages)
