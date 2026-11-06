"""Deterministic graders: cheap, fast and fully reproducible. Prefer these wherever a rule can decide.

Every grader takes (spec, case, output) and returns a Grade.
"""

import json
import re

from graders.base import Grade


def contains(spec, case, output):
    vals = spec["value"] if isinstance(spec["value"], list) else [spec["value"]]
    hit = [v for v in vals if v.lower() in output.lower()]
    ok = bool(hit)
    return Grade("contains", float(ok), ok,
                 f"found '{hit[0]}'" if ok else f"missing any of {vals}",
                 None if ok else spec.get("failure_type", "missing_info"))


def not_contains(spec, case, output):
    hit = [v for v in spec["values"] if v.lower() in output.lower()]
    ok = not hit
    return Grade("not_contains", float(ok), ok,
                 "no forbidden strings" if ok else f"contains forbidden {hit}",
                 None if ok else spec.get("failure_type", "forbidden_content"))


def regex(spec, case, output):
    should = spec.get("should_match", True)
    m = re.search(spec["pattern"], output, flags=re.IGNORECASE)
    ok = bool(m) == should
    why = (f"matched '{m.group(0)}'" if m else f"no match for /{spec['pattern']}/")
    return Grade("regex", float(ok), ok, why, None if ok else spec.get("failure_type", "pattern_mismatch"))


_TYPES = {"str": str, "int": int, "float": (int, float), "bool": bool, "list": list, "dict": dict}


def json_schema(spec, case, output):
    """Lightweight schema check: output must parse as a JSON object with the required keys and types.
    Swap in the `jsonschema` package if a client gives you a full JSON Schema."""
    ft = spec.get("failure_type", "format_error")
    try:
        obj = json.loads(output.strip())
    except json.JSONDecodeError as e:
        return Grade("json_schema", 0.0, False, f"invalid JSON: {e.msg} at char {e.pos}", ft)
    if not isinstance(obj, dict):
        return Grade("json_schema", 0.0, False, "JSON is not an object", ft)
    problems = []
    for key, typ in spec.get("required", {}).items():
        if key not in obj:
            problems.append(f"missing '{key}'")
        elif not isinstance(obj[key], _TYPES[typ]):
            problems.append(f"'{key}' is {type(obj[key]).__name__}, expected {typ}")
    ok = not problems
    return Grade("json_schema", float(ok), ok, "valid" if ok else "; ".join(problems), None if ok else ft)


PII_PATTERNS = {
    "email": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "uk_phone": r"(?:\+44\s?7\d{3}|\b07\d{3})\s?\d{3}\s?\d{3}\b",
    "card_number": r"\b(?:\d[ -]?){13,16}\b",
    "uk_postcode": r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b",
}


def no_pii(spec, case, output):
    found = []
    for name, pat in PII_PATTERNS.items():
        if name in spec.get("ignore", []):
            continue
        for m in re.finditer(pat, output):
            found.append(f"{name}: {m.group(0)}")
    ok = not found
    return Grade("no_pii", float(ok), ok, "no PII detected" if ok else "; ".join(found),
                 None if ok else spec.get("failure_type", "pii_leak"))


GRADERS = {f.__name__: f for f in [contains, not_contains, regex, json_schema, no_pii]}
