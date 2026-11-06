"""Build a client-ready HTML report from a saved run.

    python report/build.py runs/<run_dir>
    python report/build.py runs/<run_dir> --baseline runs/<older_run_dir> --notes notes.md \
        --client "Northwind Goods" --title "Support assistant evaluation"

Writes <run_dir>/report.html. Open it in a browser and print to PDF for the deliverable.

Counting rules (stated in the report's method section, too):
  - A case is "reliable" only if it passed in every sample. Cases are the unit for
    confidence intervals, because samples of the same case are not independent.
  - A case is "flaky" if it passed in some samples but not all.
"""

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).resolve().parent


def conversation_text(messages):
    if len(messages) == 1:
        return messages[0]["content"]
    who = {"user": "Customer", "assistant": "Assistant"}
    return "\n\n".join(f"{who.get(m['role'], m['role'])}: {m['content']}" for m in messages)
RISK_ORDER = {"high": 0, "medium": 1, "low": 2}


def wilson(k: int, n: int, z: float = 1.96):
    """95% Wilson score interval for k successes out of n. Behaves well for small n and 0%/100%."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def load_run(run_dir: Path):
    cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    results = [json.loads(l) for l in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    return cfg, results


def by_case(results):
    cases = defaultdict(list)
    for r in results:
        cases[r["case_id"]].append(r)
    out = {}
    for cid, rs in cases.items():
        k = sum(r["passed"] for r in rs)
        out[cid] = {"id": cid, "tags": rs[0]["tags"], "samples": rs, "n": len(rs), "k": k,
                    "reliable": k == len(rs), "flaky": 0 < k < len(rs), "frac": k / len(rs)}
    return out


def rate(cases):
    n = len(cases)
    k = sum(c["reliable"] for c in cases)
    lo, hi = wilson(k, n)
    return {"n": n, "k": k, "pct": k / n if n else 0.0, "lo": lo, "hi": hi}


def pct(x, digits=0):
    return f"{x * 100:.{digits}f}%"


def summarise(cfg, results, baseline=None):
    cases = by_case(results)
    clist = list(cases.values())
    headline = rate(clist)
    attempts = len(results)
    att_pass = sum(r["passed"] for r in results)
    errors = sum(1 for r in results if r.get("error"))

    # Breakdown by every tag key (category, risk, source, ...)
    breakdown = {}
    keys = sorted({k for c in clist for k in c["tags"]})
    for key in keys:
        groups = defaultdict(list)
        for c in clist:
            groups[c["tags"].get(key, "untagged")].append(c)
        rows = [{"value": v, **rate(g)} for v, g in groups.items()]
        if key == "risk":
            rows.sort(key=lambda r: RISK_ORDER.get(r["value"], 9))
        else:
            rows.sort(key=lambda r: (r["pct"], r["value"]))
        breakdown[key] = rows

    # Failure taxonomy: each failed attempt counts once per distinct failure type
    tax_attempts, tax_cases, tax_example, tax_risk = Counter(), defaultdict(set), {}, defaultdict(set)
    for r in results:
        types = {g["failure_type"] for g in r["grades"] if not g["passed"] and g["failure_type"]}
        if r.get("error"):
            types.add("target_error")
        for t in types:
            tax_attempts[t] += 1
            tax_cases[t].add(r["case_id"])
            tax_risk[t].add(r["tags"].get("risk", "unknown"))
            tax_example.setdefault(t, r["case_id"])
    taxonomy = [{"type": t, "attempts": n, "cases": len(tax_cases[t]),
                 "risk": min(tax_risk[t], key=lambda x: RISK_ORDER.get(x, 9)),
                 "example": tax_example[t], "share": n / max(1, attempts - att_pass)}
                for t, n in tax_attempts.most_common()]
    taxonomy.sort(key=lambda x: (RISK_ORDER.get(x["risk"], 9), -x["cases"]))

    # Worst failures: high risk first, then lowest pass fraction
    failing = [c for c in clist if not c["reliable"]]
    failing.sort(key=lambda c: (RISK_ORDER.get(c["tags"].get("risk"), 9), c["frac"], c["id"]))
    worst = []
    for c in failing[:10]:
        bad = next(r for r in c["samples"] if not r["passed"])
        worst.append({"id": c["id"], "tags": c["tags"], "k": c["k"], "n": c["n"],
                      "user": conversation_text(bad["input"]["messages"]), "output": bad.get("output") or bad.get("error"),
                      "reference": bad.get("reference"),
                      "grades": [g for g in bad["grades"] if not g["passed"]]})

    flaky = sorted([c for c in clist if c["flaky"]], key=lambda c: c["id"])
    flaky_rows = []
    for c in flaky:
        outs = Counter((r.get("output") or "")[:160] for r in c["samples"])
        flaky_rows.append({"id": c["id"], "k": c["k"], "n": c["n"], "variants": outs.most_common()})

    # Where rule-based graders and the LLM judge disagree on the same attempt
    disagree = 0
    both = 0
    for r in results:
        det = [g["passed"] for g in r["grades"] if not g["grader"].startswith("llm_judge")]
        llm = [g["passed"] for g in r["grades"] if g["grader"].startswith("llm_judge")]
        if det and llm:
            both += 1
            disagree += all(det) != all(llm)

    # Cost and latency
    lat = [r["latency_ms"] for r in results if r.get("latency_ms") is not None]
    cost = sum(r.get("cost_usd", 0) or 0 for r in results)
    perf = {
        "p50": statistics.median(lat) if lat else 0,
        "p95": sorted(lat)[max(0, math.ceil(0.95 * len(lat)) - 1)] if lat else 0,
        "cost_total": cost,
        "cost_per_1k": cost / attempts * 1000 if attempts else 0,
        "tokens_in": sum(r.get("tokens_in", 0) or 0 for r in results),
        "tokens_out": sum(r.get("tokens_out", 0) or 0 for r in results),
    }

    grader_counts = Counter()
    for r in results:
        if r["sample"] == 0:
            for g in r["grades"]:
                grader_counts[g["grader"]] += 1

    comparison = None
    if baseline:
        bcfg, bres = baseline
        bcases = by_case(bres)
        common = sorted(set(cases) & set(bcases))
        regressions = [cid for cid in common if bcases[cid]["reliable"] and not cases[cid]["reliable"]]
        fixes = [cid for cid in common if not bcases[cid]["reliable"] and cases[cid]["reliable"]]
        brate = rate([bcases[c] for c in common])
        crate = rate([cases[c] for c in common])

        def detail(cid, src):
            bad = next((r for r in src[cid]["samples"] if not r["passed"]), src[cid]["samples"][0])
            return {"id": cid, "tags": src[cid]["tags"],
                    "types": sorted({g["failure_type"] for g in bad["grades"] if not g["passed"] and g["failure_type"]}),
                    "output": bad.get("output") or ""}
        comparison = {"name": bcfg["name"], "target": bcfg["target"], "base": brate, "cur": crate,
                      "delta": crate["pct"] - brate["pct"], "n_common": len(common),
                      "regressions": [detail(c, cases) for c in regressions],
                      "fixes": [detail(c, bcases) for c in fixes]}

    return {"headline": headline, "attempts": attempts, "attempt_rate": att_pass / attempts if attempts else 0,
            "errors": errors, "breakdown": breakdown, "taxonomy": taxonomy, "worst": worst,
            "n_failing": len(failing), "flaky": flaky_rows, "perf": perf, "grader_counts": grader_counts,
            "agreement": {"both": both, "disagree": disagree}, "comparison": comparison,
            "high_risk_fail": sum(1 for c in failing if c["tags"].get("risk") == "high")}


def parse_notes(path):
    """Tiny markdown subset: '## ' headings, '- ' bullets, blank-line paragraphs."""
    if not path:
        return None
    blocks, para, items = [], [], []

    def flush():
        nonlocal para, items
        if para:
            blocks.append(("p", " ".join(para)))
        if items:
            blocks.append(("ul", items))
        para, items = [], []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            flush()
        elif s.startswith("## "):
            flush()
            blocks.append(("h", s[3:]))
        elif s.startswith(("- ", "* ")):
            if para:
                flush()
            items.append(s[2:])
        else:
            if items:
                flush()
            para.append(s)
    flush()
    return blocks


def main():
    p = argparse.ArgumentParser()
    p.add_argument("run_dir", type=Path)
    p.add_argument("--baseline", type=Path)
    p.add_argument("--notes", type=Path, help="markdown file with your findings and recommendations")
    p.add_argument("--title", default="LLM evaluation report")
    p.add_argument("--client", default="")
    p.add_argument("--author", default="")
    p.add_argument("--out", type=Path)
    a = p.parse_args()

    cfg, results = load_run(a.run_dir)
    baseline = load_run(a.baseline) if a.baseline else None
    s = summarise(cfg, results, baseline)

    env = Environment(loader=FileSystemLoader(HERE), autoescape=select_autoescape(["html"]))
    env.filters["pct"] = pct
    html = env.get_template("template.html").render(
        cfg=cfg, s=s, title=a.title, client=a.client, author=a.author,
        notes=parse_notes(a.notes), date=datetime.now().strftime("%d %B %Y"))
    out = a.out or (a.run_dir / "report.html")
    out.write_text(html, encoding="utf-8")
    print(f"Report written to {out}")


if __name__ == "__main__":
    main()
