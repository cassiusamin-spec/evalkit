"""Run a test set against a target system and save every raw result.

    python run.py --cases cases/demo_support.jsonl --adapter demo_bot --samples 3 --name baseline
    python run.py --cases cases/demo_support.jsonl --adapter demo_bot --adapter-arg version=v2 --samples 3 --name v2

Each run is saved to runs/<timestamp>_<name>/ as config.json + results.jsonl,
so any report can be rebuilt and any number traced back to the exact output.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import adapters  # noqa: E402
from graders import grade  # noqa: E402
from graders.llm_judge import Judge  # noqa: E402
from llm_clients import FatalAPIError, RetriesExhausted  # noqa: E402

OUTPUT_CACHE = ROOT / "runs" / ".cache" / "outputs"


def load_cases(path: Path) -> list[dict]:
    cases, seen = [], set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        try:
            c = json.loads(line)
        except json.JSONDecodeError as e:
            sys.exit(f"{path}:{n}: invalid JSON ({e.msg})")
        for field in ("id", "input", "graders"):
            if field not in c:
                sys.exit(f"{path}:{n}: case is missing '{field}'")
        if c["id"] in seen:
            sys.exit(f"{path}:{n}: duplicate case id '{c['id']}'")
        seen.add(c["id"])
        c.setdefault("tags", {})
        cases.append(c)
    return cases


def call_with_retry(adapter, case, sample, retries=3):
    for attempt in range(retries + 1):
        try:
            return adapter.run(case, sample)
        except (FatalAPIError, RetriesExhausted):
            raise  # bad key or wrong model (retrying won't help), or already retried inside llm_clients
        except Exception:  # noqa: BLE001 - any other target failure is retried, then recorded
            if attempt == retries:
                raise
            time.sleep(2 ** attempt)


def run_one(adapter, judge, case, sample, use_cache):
    rec = {"case_id": case["id"], "sample": sample, "tags": case["tags"],
           "input": case["input"], "reference": case.get("reference")}
    cache_file = None
    if use_cache and getattr(adapter, "cacheable", True):
        key = hashlib.sha256(json.dumps([adapter.config(), case["input"], sample], sort_keys=True).encode()).hexdigest()
        cache_file = OUTPUT_CACHE / f"{key}.json"
    try:
        if cache_file and cache_file.exists():
            res = json.loads(cache_file.read_text(encoding="utf-8"))
            res["cached"] = True
        else:
            res = call_with_retry(adapter, case, sample)
            if cache_file:
                OUTPUT_CACHE.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(res), encoding="utf-8")
        rec.update(res)
    except Exception as e:  # noqa: BLE001
        rec.update({"output": None, "error": f"{type(e).__name__}: {e}"})
        rec["grades"] = []
        rec["passed"] = False
        return rec
    try:
        grades = [grade(spec, case, rec["output"], judge).to_dict() for spec in case["graders"]]
    except Exception as e:  # noqa: BLE001 - e.g. the judge's API failed; keep the answer, flag the case
        rec.update({"error": f"Judge error: {type(e).__name__}: {e}", "grades": [], "passed": False})
        return rec
    rec["grades"] = grades
    rec["passed"] = all(g["passed"] for g in grades)
    return rec


def git_hash():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:  # noqa: BLE001
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cases", required=True, type=Path)
    p.add_argument("--adapter", required=True, help="module name in adapters/, e.g. demo_bot")
    p.add_argument("--adapter-arg", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--samples", type=int, default=1, help="runs per case, to measure consistency")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--judge-mode", choices=["auto", "api", "stub"], default="auto")
    p.add_argument("--judge-model", default=None,
                   help="e.g. claude-sonnet-5-5 or gemini-3.8-flash (default: Claude if ANTHROPIC_API_KEY "
                        "is set, otherwise Gemini if GEMINI_API_KEY is set)")
    p.add_argument("--name", default="run")
    p.add_argument("--no-cache", action="store_true")
    a = p.parse_args()

    kwargs = dict(kv.split("=", 1) for kv in a.adapter_arg)
    try:
        adapter = adapters.load(a.adapter, **kwargs)
        judge = Judge(mode=a.judge_mode, model=a.judge_model)
    except FatalAPIError as e:
        sys.exit(f"Error: {e}")
    cases = load_cases(a.cases)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "runs" / f"{stamp}_{a.name}"
    out_dir.mkdir(parents=True)
    config = {"name": a.name, "started_utc": stamp, "cases_file": str(a.cases), "n_cases": len(cases),
              "samples": a.samples, "target": adapter.config(), "judge": judge.config(), "git": git_hash()}

    print(f"Running {len(cases)} cases x {a.samples} samples against {adapter.config()}")
    print(f"Judge: {judge.config()['model']}" + ("  (offline stub - not for client reports)" if judge.mode == "stub" else ""))
    jobs = [(c, s) for c in cases for s in range(a.samples)]
    judge_calls = sum(1 for c in cases for g in c["graders"] if g["type"] == "llm_judge") * a.samples
    api_calls = (len(jobs) if getattr(adapter, "cacheable", True) else 0) + (judge_calls if judge.mode == "api" else 0)
    print(f"{len(jobs)} answers to collect" + (f", about {api_calls} API calls in total" if api_calls else "") + ".")
    if api_calls:
        print("Each line below is one finished answer. This can take a while on a free-tier key.")
        print("Press Ctrl+C to stop at any time: finished answers are saved and reused when you run again.")
    results = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(run_one, adapter, judge, c, s, not a.no_cache) for c, s in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            results.append(f.result())
            r = results[-1]
            status = "error" if r.get("error") else ("pass" if r["passed"] else "fail")
            print(f"  {i}/{len(jobs)}  {r['case_id']} (sample {r['sample'] + 1}): {status}", flush=True)
    results.sort(key=lambda r: (r["case_id"], r["sample"]))

    config["finished_utc"] = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (out_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    with open(out_dir / "results.jsonl", "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")

    passed = sum(r["passed"] for r in results)
    errors = sum(1 for r in results if r.get("error"))
    print(f"Pass rate: {passed}/{len(results)} ({passed / len(results):.0%})" + (f", {errors} errors" if errors else ""))
    print(f"Saved to {out_dir.relative_to(ROOT)}")
    if errors:
        first = next(r["error"] for r in results if r.get("error"))
        print(f"First error: {first}")
    if errors == len(results):
        print("Every call failed, so these results are not meaningful. Fix the error above and run again.")
    elif not os.environ.get("EVALKIT_LAUNCHER"):  # start.py builds the report itself
        print(f"Build the report:  python report/build.py {out_dir.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
