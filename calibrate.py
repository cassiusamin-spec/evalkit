"""Check how far you can trust the LLM judge, by comparing it with your own labels.

Step 1 - export a labelling sheet from a run (judge scores are hidden so they don't bias you):
    python calibrate.py export runs/<run_dir> --out labels/sheet.csv

Step 2 - open the CSV in Excel or Sheets and fill the human_pass column with 1 (pass) or 0 (fail).
         Aim for 50-100 rows, including plenty of borderline responses.

Step 3 - score agreement:
    python calibrate.py score runs/<run_dir> labels/sheet.csv

Report the agreement rate and Cohen's kappa in the method section of client reports, and list
the disagreements so you can tighten the rubric. Rough guide for kappa: >0.8 strong, 0.6-0.8
acceptable, <0.6 fix the rubric or judge before relying on it.
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from graders.base import conversation_text


def judge_grades(run_dir: Path):
    rows = {}
    for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        for g in r["grades"]:
            if g["grader"].startswith("llm_judge:"):
                rubric = g["grader"].split(":", 1)[1]
                rows[(r["case_id"], str(r["sample"]), rubric)] = (r, g)
    return rows


def export(run_dir: Path, out: Path, samples: int):
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["case_id", "sample", "rubric", "user_message", "response", "reference", "human_pass", "notes"])
        n = 0
        for (cid, sample, rubric), (r, _) in sorted(judge_grades(run_dir).items()):
            if int(sample) >= samples:
                continue
            w.writerow([cid, sample, rubric, conversation_text(r["input"]["messages"]), r.get("output") or "",
                        r.get("reference") or "", "", ""])
            n += 1
    print(f"Wrote {n} rows to {out}.")
    if not os.environ.get("EVALKIT_LAUNCHER"):  # start.py prints its own next steps
        print(f"Fill human_pass with 1 or 0, then run: python calibrate.py score {run_dir} {out}")


def kappa(pairs):
    n = len(pairs)
    if n == 0:
        return float("nan")
    po = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def score(run_dir: Path, labels: Path):
    grades = judge_grades(run_dir)
    pairs, per_rubric, disagreements, missing = [], defaultdict(list), [], 0
    with open(labels, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            h = row["human_pass"].strip()
            if h not in ("0", "1"):
                continue
            key = (row["case_id"], row["sample"], row["rubric"])
            if key not in grades:
                missing += 1
                continue
            r, g = grades[key]
            pair = (int(h), int(g["passed"]))
            pairs.append(pair)
            per_rubric[row["rubric"]].append(pair)
            if pair[0] != pair[1]:
                disagreements.append((key, pair, (r.get("output") or "")[:100], g["rationale"][:120], row.get("notes", "")))
    if not pairs:
        sys.exit("No labelled rows found. Fill human_pass with 1 or 0.")

    cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    print(f"Judge: {cfg['judge']['model']}  ({len(pairs)} labelled responses)")
    if cfg["judge"]["mode"] == "stub":
        print("  note: this run used the offline stub judge; calibrate a real judge before client work")
    print(f"\n{'rubric':<14}{'n':>4}{'agree':>8}{'kappa':>8}{'judge too lenient':>19}{'judge too strict':>18}")
    for rubric, ps in sorted(per_rubric.items()) + [("ALL", pairs)]:
        agree = sum(a == b for a, b in ps) / len(ps)
        lenient = sum(1 for h, j in ps if h == 0 and j == 1)
        strict = sum(1 for h, j in ps if h == 1 and j == 0)
        print(f"{rubric:<14}{len(ps):>4}{agree:>8.0%}{kappa(ps):>8.2f}{lenient:>19}{strict:>18}")
    if missing:
        print(f"\n{missing} labelled rows did not match this run and were skipped.")
    if disagreements:
        print("\nDisagreements (human, judge) - read these to improve the rubric:")
        for (cid, s, rub), (h, j), out, why, note in disagreements:
            print(f"  {cid} s{s} [{rub}] human={'pass' if h else 'fail'} judge={'pass' if j else 'fail'}")
            print(f"     response: {out!r}")
            print(f"     judge:    {why}")
            if note:
                print(f"     your note: {note}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("run_dir", type=Path)
    e.add_argument("--out", type=Path, default=Path("labels/sheet.csv"))
    e.add_argument("--samples", type=int, default=1, help="how many samples per case to include")
    s = sub.add_parser("score")
    s.add_argument("run_dir", type=Path)
    s.add_argument("labels", type=Path)
    a = p.parse_args()
    if a.cmd == "export":
        export(a.run_dir, a.out, a.samples)
    else:
        score(a.run_dir, a.labels)


if __name__ == "__main__":
    main()
