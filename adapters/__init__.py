"""Adapters wrap the system under test. Each module exposes NAME and an Adapter class with:

    Adapter(**kwargs).run(case, sample) -> {"output", "latency_ms", "tokens_in", "tokens_out", "cost_usd"}
    Adapter(**kwargs).config() -> dict (saved with the run so results are reproducible)

To evaluate a new client system, copy anthropic_prompt.py or demo_bot.py and change run().
"""

import importlib


def load(name: str, **kwargs):
    mod = importlib.import_module(f"adapters.{name}")
    return mod.Adapter(**kwargs)
