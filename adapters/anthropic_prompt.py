"""Adapter for a 'system prompt + Claude model' feature, via the Anthropic Messages API.

Usage (PowerShell):
    $env:ANTHROPIC_API_KEY = "..."
    python run.py --cases cases\\demo_support.jsonl --adapter anthropic_prompt `
        --adapter-arg system_prompt_file=prompts\\support_v1.txt --adapter-arg model=claude-sonnet-5-5

Settings (all optional, passed with --adapter-arg KEY=VALUE):
    model, system_prompt_file, temperature (default 0), max_tokens (default 600)

For a client's own endpoint (RAG pipeline, internal API), copy this file and replace run().
"""

import hashlib
import time

import llm_clients

NAME = "anthropic_prompt"

# Per-million-token prices (input, output) used for cost estimates. Check current pricing and update.
PRICES = {"input": 3.0, "output": 15.0}


class Adapter:
    def __init__(self, model: str = "claude-sonnet-5-5", system_prompt_file: str = "",
                 temperature: str = "0", max_tokens: str = "600", **_):
        self.model = model
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.system_prompt_file = system_prompt_file
        self.system = open(system_prompt_file, encoding="utf-8").read() if system_prompt_file else ""
        llm_clients.require_key("anthropic")

    def config(self) -> dict:
        return {"adapter": NAME, "model": self.model, "temperature": self.temperature,
                "max_tokens": self.max_tokens, "system_prompt_file": self.system_prompt_file,
                # A fingerprint of the prompt text, so editing the file is never mistaken for an unchanged prompt.
                "system_prompt_sha": hashlib.sha256(self.system.encode()).hexdigest()[:12]}

    def run(self, case: dict, sample: int = 0) -> dict:
        start = time.perf_counter()
        r = llm_clients.anthropic_chat(self.model, case["input"]["messages"], system=self.system,
                                       temperature=self.temperature, max_tokens=self.max_tokens)
        cost = (r["tokens_in"] * PRICES["input"] + r["tokens_out"] * PRICES["output"]) / 1e6
        return {"output": r["text"], "latency_ms": round((time.perf_counter() - start) * 1000, 1),
                "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"], "cost_usd": round(cost, 6)}
