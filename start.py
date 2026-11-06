"""evalkit launcher: reads settings.ini and does everything for you.

Run it by double-clicking start.py, or by opening it in IDLE or VS Code and pressing Run.
There is nothing to edit in this file: change settings.ini instead.

API keys go in api_keys.ini, which this script creates the first time it needs one.
"""

import configparser
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)  # double-clicking can start us in another folder

SETTINGS = ROOT / "settings.ini"
KEYS = ROOT / "api_keys.ini"
RUNS = ROOT / "runs"
REPORTS = ROOT / "reports"

ADAPTERS = {"demo": "demo_bot", "gemini": "gemini_prompt", "claude": "anthropic_prompt"}
KEY_FOR = {"gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY"}
KEYS_TEMPLATE = """; Your API keys. Paste each key after the = sign and save the file.
; You only need the key for the provider you use. Leave the other one blank.
;
; KEEP THIS FILE PRIVATE: don't email it, share it, or upload it anywhere.

[keys]
GEMINI_API_KEY =
ANTHROPIC_API_KEY =
"""

pause_at_end = True


class Stop(Exception):
    """A problem the user can fix. Shown as a plain message, without a traceback."""


def say(msg=""):
    print(msg, flush=True)


def heading(msg):
    say()
    say("=" * 64)
    say(f"  {msg}")
    say("=" * 64)


# ---------------------------------------------------------------- settings

def load_settings():
    if not SETTINGS.exists():
        raise Stop("settings.ini is missing. It should be in the same folder as start.py.")
    cp = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None)
    try:
        cp.read(SETTINGS, encoding="utf-8-sig")
    except configparser.Error as e:
        raise Stop(f"settings.ini couldn't be read: {e}\nCheck you haven't deleted a [section] line.")

    def get(section, key, default=""):
        if not cp.has_section(section):
            raise Stop(f"settings.ini is missing the [{section}] section. Restore it from a fresh copy of evalkit.")
        return cp.get(section, key, fallback=default).strip() or default
    return get


def as_int(value, setting, minimum=1):
    try:
        n = int(value)
    except ValueError:
        raise Stop(f"In settings.ini, '{setting}' must be a whole number (it says '{value}').")
    if n < minimum:
        raise Stop(f"In settings.ini, '{setting}' must be at least {minimum}.")
    return n


def as_yes(value, setting):
    v = value.lower()
    if v in ("yes", "y", "true", "1"):
        return True
    if v in ("no", "n", "false", "0"):
        return False
    raise Stop(f"In settings.ini, '{setting}' must be yes or no (it says '{value}').")


def as_path(value, setting, must_exist=True):
    p = Path(value.replace("\\", "/"))
    if must_exist and not (ROOT / p).exists():
        raise Stop(f"In settings.ini, '{setting}' points to '{value}', but there's no such file in the evalkit folder.")
    return str(p)


def clean_name(value, setting):
    name = value.replace(" ", "_")
    bad = set(name) - set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")
    if not name or bad:
        raise Stop(f"In settings.ini, '{setting}' can only use letters, numbers, - and _ (it says '{value}').")
    return name


# ---------------------------------------------------------------- setup helpers

def check_python():
    if sys.version_info < (3, 10):
        raise Stop(f"evalkit needs Python 3.10 or newer. This is Python {sys.version.split()[0]}.\n"
                   "Install a newer version from python.org/downloads.")


def ensure_jinja2():
    try:
        import jinja2  # noqa: F401
        return
    except ImportError:
        pass
    say("Installing jinja2, which builds the report (one time only)...")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    if r.returncode != 0:
        raise Stop("Couldn't install jinja2 automatically. See 'Installing jinja2 by hand' in the README.")
    say("Done.")


def load_keys():
    """Put keys from api_keys.ini into the environment. Values in the file win over older ones."""
    if not KEYS.exists():
        return
    cp = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None)
    cp.optionxform = str  # keep key names in capitals
    try:
        cp.read(KEYS, encoding="utf-8-sig")
    except configparser.Error as e:
        raise Stop(f"api_keys.ini couldn't be read: {e}\nDelete it and run start.py again to get a fresh one.")
    for name in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ANYTHINGLLM_API_KEY"):
        value = cp.get("keys", name, fallback="").strip().strip('"').strip("'")
        if value:
            os.environ[name] = value


def require_key(system_type):
    var = KEY_FOR.get(system_type)
    if not var or os.environ.get(var):
        return
    if not KEYS.exists():
        KEYS.write_text(KEYS_TEMPLATE, encoding="utf-8")
    provider = "Gemini" if system_type == "gemini" else "Claude"
    raise Stop(f"No {provider} API key found.\n\n"
               f"I've opened (or created) the file api_keys.ini in your evalkit folder.\n"
               f"Paste your key after  {var} =  , save the file, and run start.py again.")


def open_file(path):
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606 - opens in the default app (Notepad for .ini)
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        else:
            subprocess.run(["xdg-open", str(path)], stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001 - opening is a convenience, never fatal
        pass


def all_failed(run_dir):
    lines = [x for x in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    return not lines or all('"error"' in x for x in lines)


def latest_run(name, exclude=None):
    """Newest run folder with this name, skipping runs where every question failed."""
    if not RUNS.exists():
        return None
    found = sorted(p for p in RUNS.iterdir() if p.is_dir() and p.name.endswith("_" + name)
                   and (p / "results.jsonl").exists() and p != exclude)
    usable = [p for p in found if not all_failed(p)]
    return usable[-1] if usable else None


def check_report_settings(get):
    """Catch report-setting mistakes before a long test run, not after it."""
    as_yes(get("report", "open_in_browser", "yes"), "open_in_browser")
    if get("report", "notes"):
        as_path(get("report", "notes"), "notes")
    compare = get("report", "compare_with")
    if compare and latest_run(clean_name(compare, "compare_with")) is None:
        raise Stop(f"In settings.ini, 'compare_with' is '{compare}', but there's no earlier run with that name.\n"
                   "Check the names of the folders inside the runs folder (the part after the date and time).")


def python(*args):
    """Run one of evalkit's scripts with this same Python, relaying its output line by line.
    Relaying (rather than letting the script print directly) makes the output visible in IDLE
    and VS Code as well as in a double-clicked window."""
    env = {**os.environ, "EVALKIT_LAUNCHER": "1", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: no extra black window from IDLE
    proc = subprocess.Popen([sys.executable, *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", creationflags=flags)
    try:
        for line in proc.stdout:
            print(line, end="", flush=True)
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        raise


# ---------------------------------------------------------------- actions

def run_tests(get, name):
    system_type = get("system under test", "type", "demo").lower()
    adapter = ADAPTERS.get(system_type, system_type)
    if not (ROOT / "adapters" / f"{adapter}.py").exists():
        raise Stop(f"In settings.ini, 'type' is '{system_type}'. Use demo, gemini, claude, "
                   "or the name of an adapter file in the adapters folder.")
    require_key(system_type)
    judge_model = get("test", "judge_model")
    if judge_model:
        require_key("gemini" if judge_model.lower().startswith("gemini") else "claude")

    args = ["run.py",
            "--cases", as_path(get("test", "cases"), "cases"),
            "--adapter", adapter,
            "--samples", str(as_int(get("test", "samples", "1"), "samples")),
            "--workers", str(as_int(get("test", "workers", "2"), "workers")),
            "--name", name]
    if judge_model:
        args += ["--judge-model", judge_model]

    if system_type == "demo":
        args += ["--adapter-arg", f"version={get('system under test', 'demo_version', 'v1')}"]
    else:
        prompt = get("system under test", "prompt_file")
        if prompt:
            args += ["--adapter-arg", f"system_prompt_file={as_path(prompt, 'prompt_file')}"]
        for key in ("model", "temperature", "max_tokens", "thinking_level"):
            value = get("system under test", key)
            if value:
                args += ["--adapter-arg", f"{key}={value}"]

    heading(f"Running the tests ({name})")
    previous = max((p for p in RUNS.iterdir() if p.name.endswith("_" + name)), default=None) if RUNS.exists() else None
    code = python(*args)
    if code != 0:
        raise Stop("The test run stopped with an error (see the message above).")
    new = sorted(p for p in RUNS.iterdir() if p.is_dir() and p.name.endswith("_" + name)) if RUNS.exists() else []
    run_dir = new[-1] if new else None
    if run_dir is None or run_dir == previous or not (run_dir / "results.jsonl").exists():
        raise Stop("The test run didn't save any results (see the messages above).")
    if all_failed(run_dir):
        raise Stop("Every question failed, so there's no report to build. Fix the 'First error' shown above "
                   "and run start.py again.")
    return run_dir


def build_report(get, name, run_dir):
    ensure_jinja2()
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"{name}.html"
    args = ["report/build.py", str(run_dir), "--out", str(out),
            "--title", get("report", "title", "LLM evaluation report")]
    for key in ("client", "author"):
        if get("report", key):
            args += [f"--{key}", get("report", key)]
    notes = get("report", "notes")
    if notes:
        args += ["--notes", as_path(notes, "notes")]
    compare = get("report", "compare_with")
    if compare:
        base = latest_run(clean_name(compare, "compare_with"), exclude=run_dir)
        if base is None:
            raise Stop(f"In settings.ini, 'compare_with' is '{compare}', but there's no earlier run with that name.\n"
                       "Check the names of the folders inside the runs folder (the part after the date and time).")
        args += ["--baseline", str(base)]
        say(f"Comparing with: {base.name}")

    heading("Building the report")
    if python(*args) != 0:
        raise Stop("The report couldn't be built (see the message above).")
    say()
    say(f"Your report is ready: {out.relative_to(ROOT)}")
    if as_yes(get("report", "open_in_browser", "yes"), "open_in_browser"):
        webbrowser.open(out.resolve().as_uri())
    return out


def marking_sheet(get, name, mode):
    run_dir = latest_run(name)
    if run_dir is None:
        raise Stop(f"There's no run called '{name}' yet. Run 'test and report' first.")
    sheet = as_path(get("marking sheet", "file", "labels/sheet.csv"), "file", must_exist=(mode == "score"))
    if mode == "export":
        heading(f"Creating a marking sheet from {run_dir.name}")
        (ROOT / sheet).parent.mkdir(parents=True, exist_ok=True)
        samples = str(as_int(get("marking sheet", "samples", "1"), "samples (marking sheet)"))
        if python("calibrate.py", "export", str(run_dir), "--out", sheet, "--samples", samples) != 0:
            raise Stop("The marking sheet couldn't be created (see the message above).")
        say()
        say("Next: open the sheet in Excel, type 1 (acceptable) or 0 (not acceptable) in the human_pass column,")
        say("save it as 'CSV UTF-8', close Excel, then set  action = score marking sheet  and run start.py again.")
    else:
        heading(f"Scoring your marks against the AI judge ({run_dir.name})")
        if python("calibrate.py", "score", str(run_dir), sheet) != 0:
            raise Stop("Scoring failed (see the message above). Is the sheet still open in Excel?")


def main():
    global pause_at_end
    check_python()
    get = load_settings()
    pause_at_end = as_yes(get("window", "pause_at_end", "yes"), "pause_at_end")
    load_keys()

    action = " ".join(get("what to do", "action", "test and report").lower().split())
    name = clean_name(get("test", "name", "run"), "name")
    say(f"evalkit: {action} ({name})")

    if action == "test and report":
        check_report_settings(get)
        ensure_jinja2()
        run_dir = run_tests(get, name)
        build_report(get, name, run_dir)
    elif action == "report only":
        check_report_settings(get)
        run_dir = latest_run(name)
        if run_dir is None:
            raise Stop(f"There's no run called '{name}' yet. Use  action = test and report  first.")
        say(f"Using the latest run: {run_dir.name}")
        build_report(get, name, run_dir)
    elif action == "export marking sheet":
        marking_sheet(get, name, "export")
    elif action == "score marking sheet":
        marking_sheet(get, name, "score")
    else:
        raise Stop(f"In settings.ini, 'action' is '{action}'. Use one of: test and report, report only, "
                   "export marking sheet, score marking sheet.")


if __name__ == "__main__":
    ok = False
    try:
        main()
        ok = True
    except Stop as e:
        say()
        say("PROBLEM:")
        say(str(e))
        if "api_keys.ini" in str(e):
            open_file(KEYS)
    except KeyboardInterrupt:
        say()
        say("Stopped. Answers that had already finished are saved and will be reused next time.")
    except Exception:  # noqa: BLE001 - show the details, and keep the window open so they can be read
        import traceback
        traceback.print_exc()
        say()
        say("Something unexpected went wrong. Copy the text above and ask for help.")
    if pause_at_end or not ok:
        try:
            input("\nPress Enter to close this window...")
        except (EOFError, KeyboardInterrupt):
            pass
    sys.exit(0 if ok else 1)
