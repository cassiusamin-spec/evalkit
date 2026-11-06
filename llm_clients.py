"""Minimal clients for the model APIs evalkit talks to: Anthropic (Claude) and Google (Gemini).

Both return the same shape so adapters and the judge don't care which provider they use:
    chat(...) -> {"text": str, "tokens_in": int, "tokens_out": int}

Keys come from environment variables:
    ANTHROPIC_API_KEY             for Claude models
    GEMINI_API_KEY (or GOOGLE_API_KEY)   for Gemini models

Rate limits (HTTP 429) and temporary server errors are retried with backoff. Errors that
retrying cannot fix (bad key, wrong model name, malformed request) raise FatalAPIError at once.
"""

import json
import os
import random
import time
import urllib.error
import urllib.request

# Overridable for testing against a local mock server.
ANTHROPIC_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages"
GEMINI_URL = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com") + "/v1beta/models/{model}:generateContent"

RETRY_STATUS = {429, 500, 502, 503, 504, 529}


class FatalAPIError(RuntimeError):
    """An API error that retrying will not fix."""


class RetriesExhausted(RuntimeError):
    """Still failing after this module's own retries; callers should not retry again."""


def provider_for(model: str) -> str:
    return "gemini" if model.lower().startswith("gemini") else "anthropic"


def key_for(provider: str):
    if provider == "gemini":
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    return os.environ.get("ANTHROPIC_API_KEY")


KEY_HELP = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}


def require_key(provider: str) -> str:
    key = key_for(provider)
    if not key:
        raise FatalAPIError(f"No API key found. Set {KEY_HELP[provider]} in this terminal window first.")
    return key


def _post(url: str, headers: dict, body: dict, retries: int = 6, timeout: int = 180) -> dict:
    data = json.dumps(body).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers={**headers, "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = _error_detail(e)
            if e.code not in RETRY_STATUS:
                raise FatalAPIError(f"HTTP {e.code}: {detail}") from None
            if attempt == retries:
                raise RetriesExhausted(f"HTTP {e.code} after {retries} retries: {detail}") from None
            wait = _retry_after(e) or min(60, 2 ** attempt) + random.uniform(0, 1)
            why = "rate limit reached" if e.code == 429 else f"server busy (HTTP {e.code})"
            print(f"    ...{why}, waiting {wait:.0f}s and retrying (attempt {attempt + 1} of {retries})", flush=True)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == retries:
                raise RetriesExhausted(f"Network error after {retries} retries: {e}") from None
            wait = min(60, 2 ** attempt) + random.uniform(0, 1)
            print(f"    ...network problem, waiting {wait:.0f}s and retrying (attempt {attempt + 1} of {retries})", flush=True)
        time.sleep(wait)
    raise RuntimeError("unreachable")


def _error_detail(e: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(e.read())
        err = payload.get("error", payload)
        return err.get("message", str(err)) if isinstance(err, dict) else str(err)
    except Exception:  # noqa: BLE001
        return e.reason or "no detail"


def _retry_after(e: urllib.error.HTTPError):
    try:
        return min(120, float(e.headers.get("retry-after")))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- Anthropic

def anthropic_chat(model, messages, system="", temperature=None, max_tokens=1024) -> dict:
    key = require_key("anthropic")
    body = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        body["system"] = system
    if temperature is not None:
        body["temperature"] = temperature
    resp = _post(ANTHROPIC_URL, {"x-api-key": key, "anthropic-version": "2023-06-01"}, body)
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type", "text") == "text")
    u = resp.get("usage", {})
    return {"text": text, "tokens_in": u.get("input_tokens", 0), "tokens_out": u.get("output_tokens", 0)}


# ---------------------------------------------------------------- Gemini

def gemini_chat(model, messages, system="", temperature=None, max_tokens=8192, thinking_level=None) -> dict:
    """Calls generateContent. Note for Gemini 'thinking' models: max_tokens covers thinking AND the
    answer, so keep it generous or the model can run out before writing anything."""
    key = require_key("gemini")
    contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in messages]
    gen = {"maxOutputTokens": max_tokens}
    if temperature is not None:
        gen["temperature"] = temperature
    if thinking_level:
        gen["thinkingConfig"] = {"thinkingLevel": thinking_level}
    body = {"contents": contents, "generationConfig": gen}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    resp = _post(GEMINI_URL.format(model=model), {"x-goog-api-key": key}, body)

    cands = resp.get("candidates") or []
    if not cands:
        reason = resp.get("promptFeedback", {}).get("blockReason", "no candidates returned")
        return {"text": f"[blocked by Gemini: {reason}]", **_gemini_usage(resp)}
    cand = cands[0]
    parts = cand.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    finish = cand.get("finishReason", "")
    if not text.strip() and finish == "MAX_TOKENS":
        raise FatalAPIError(f"Gemini used all {max_tokens} output tokens on thinking before answering. "
                            "Raise max_tokens or set thinking_level=low.")
    if not text.strip() and finish and finish != "STOP":
        text = f"[no answer: Gemini stopped with finishReason={finish}]"
    return {"text": text, **_gemini_usage(resp)}


def _gemini_usage(resp):
    u = resp.get("usageMetadata", {})
    # Thinking tokens are billed at the output rate, so count them as output.
    return {"tokens_in": u.get("promptTokenCount", 0),
            "tokens_out": u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)}


def chat(model, messages, **kw) -> dict:
    if provider_for(model) == "gemini":
        return gemini_chat(model, messages, **kw)
    kw.pop("thinking_level", None)
    return anthropic_chat(model, messages, **kw)
