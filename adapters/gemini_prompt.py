"""Adapter for a 'system prompt + Gemini model' feature, via Google's Gemini API.

Usage (PowerShell):
    $env:GEMINI_API_KEY = "..."
    python run.py --cases cases\\demo_support.jsonl --adapter gemini_prompt `
        --adapter-arg system_prompt_file=prompts\\support_v1.txt --adapter-arg model=gemini-3.8-flash

Settings (all optional, passed with --adapter-arg KEY=VALUE):
    model           Gemini model code (default gemini-3.8-flash)
    system_prompt_file   text file with the system prompt
    temperature     leave unset to use the model's default, which Google recommends for recent models
    max_tokens      output budget INCLUDING thinking tokens (default 8192)
    thinking_level  e.g. minimal, low, medium, high (supported values vary by model)

Set these to match how the client actually runs the model, so you test what their users get.
"""

import hashlib
import time

import llm_clients

NAME = "gemini_prompt"

# USD per million tokens (input, output incl. thinking), from Google's pricing page in October 2026.
# Several prices rise on 1 Dec 2026 / 1 Jan 2027. Check https://ai.google.dev/gemini-api/docs/pricing.
PRICES = {
    "gemini-4-argon": (2.00, 10.00),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
}
DEFAULT_PRICE = (0.75, 3.75)


class Adapter:
    def __init__(self, model: str = "gemini-3.8-flash", system_prompt_file: str = "",
                 temperature: str = "", max_tokens: str = "8192", thinking_level: str = "", **_):
        self.model = model
        self.temperature = float(temperature) if temperature else None
        self.max_tokens = int(max_tokens)
        self.thinking_level = thinking_level or None
        self.system_prompt_file = system_prompt_file
        self.system = open(system_prompt_file, encoding="utf-8").read() if system_prompt_file else ""
        llm_clients.require_key("gemini")

    def config(self) -> dict:
        return {"adapter": NAME, "model": self.model, "temperature": self.temperature,
                "max_tokens": self.max_tokens, "thinking_level": self.thinking_level,
                "system_prompt_file": self.system_prompt_file,
                # A fingerprint of the prompt text, so editing the file is never mistaken for an unchanged prompt.
                "system_prompt_sha": hashlib.sha256(self.system.encode()).hexdigest()[:12]}

    def run(self, case: dict, sample: int = 0) -> dict:
        start = time.perf_counter()
        r = llm_clients.gemini_chat(self.model, case["input"]["messages"], system=self.system,
                                    temperature=self.temperature, max_tokens=self.max_tokens,
                                    thinking_level=self.thinking_level)
        p_in, p_out = PRICES.get(self.model, DEFAULT_PRICE)
        cost = (r["tokens_in"] * p_in + r["tokens_out"] * p_out) / 1e6
        return {"output": r["text"], "latency_ms": round((time.perf_counter() - start) * 1000, 1),
                "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"], "cost_usd": round(cost, 6)}
