from graders.base import Grade
from graders.deterministic import GRADERS as _DET
from graders.llm_judge import Judge


def grade(spec: dict, case: dict, output: str, judge: Judge) -> Grade:
    t = spec["type"]
    if t == "llm_judge":
        return judge.grade(spec, case, output)
    if t not in _DET:
        raise ValueError(f"Unknown grader type '{t}'. Known: {sorted(_DET) + ['llm_judge']}")
    return _DET[t](spec, case, output)
