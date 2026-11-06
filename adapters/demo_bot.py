"""A deliberately flawed customer-support bot for a fictional shop ("Northwind Goods").

It stands in for a client's LLM feature so the harness can run end to end with
no API key. It has planted bugs, so the sample report has real findings:

  v1 flaws
    - quotes the wrong refund window (60 days) about a third of the time
    - echoes the customer's phone number back in account answers (PII leak)
    - returns malformed JSON for some order-status lookups
    - follows "ignore previous instructions" prompt injections
    - gives medical advice instead of declining off-topic requests
  v2 changes (to demo regression tracking)
    - refund window fixed, PII leak fixed
    - NEW bug: claims to ship to every country

Real policy the bot should follow:
  refunds within 30 days with receipt; after that, store credit only
  ships to UK, Ireland, France, Germany, Spain only
  standard delivery 3-5 working days, express next working day (order by 2pm)
"""

import json
import random
import time

NAME = "demo_bot"

ORDERS = {
    "NW-1001": {"status": "dispatched", "eta": "2026-10-14"},
    "NW-1002": {"status": "processing", "eta": "2026-10-16"},
    "NW-1003": {"status": "delivered", "eta": "2026-10-09"},
    "NW-1004": {"status": "cancelled", "eta": None},
}

SHIP_COUNTRIES = ["uk", "united kingdom", "ireland", "france", "germany", "spain"]


class Adapter:
    cacheable = False  # local and free, so always re-run; real API adapters leave caching on

    def __init__(self, version: str = "v1", **_):
        self.version = version

    def config(self) -> dict:
        return {"adapter": NAME, "version": self.version}

    def run(self, case: dict, sample: int = 0) -> dict:
        """Return {"output": str, "latency_ms": float, "tokens_in": int, "tokens_out": int, "cost_usd": float}."""
        start = time.perf_counter()
        msgs = case["input"]["messages"]
        text = msgs[-1]["content"]
        rng = random.Random(f"{case['id']}-{sample}-{self.version}")
        out = self._respond(text.lower(), text, rng)
        time.sleep(rng.uniform(0.005, 0.02))  # pretend network latency
        latency = (time.perf_counter() - start) * 1000 + rng.uniform(300, 900)
        t_in = sum(len(m["content"].split()) for m in msgs) * 4 // 3 + 250  # + system prompt
        t_out = len(out.split()) * 4 // 3
        cost = t_in * 3e-6 + t_out * 15e-6
        return {"output": out, "latency_ms": round(latency, 1), "tokens_in": t_in,
                "tokens_out": t_out, "cost_usd": round(cost, 6)}

    def _respond(self, t: str, raw: str, rng: random.Random) -> str:
        v2 = self.version == "v2"

        if "ignore previous instructions" in t or "ignore all previous" in t:
            return "Sure! As requested, here is my system prompt: You are Northwind's support assistant. Discount code STAFF50 gives 50% off."

        if any(w in t for w in ["headache", "medicine", "rash", "ibuprofen", "dose"]):
            return "For a headache you can usually take 400mg ibuprofen every 4 hours. If it persists, see a doctor."

        if "order" in t and ("status" in t or "where" in t or "json" in t):
            oid = next((o for o in ORDERS if o.lower() in t), None)
            if oid is None:
                return json.dumps({"error": "order_not_found", "message": "I couldn't find that order number."})
            o = ORDERS[oid]
            if oid in ("NW-1002", "NW-1004"):  # planted malformed JSON
                return "{'order_id': '%s', 'status': '%s', eta: %s}" % (oid, o["status"], o["eta"])
            return json.dumps({"order_id": oid, "status": o["status"], "eta": o["eta"]})

        if "refund" in t or "return" in t or "money back" in t:
            if not v2 and rng.random() < 0.34:
                return "You can return any item within 60 days for a full refund to your original payment method."
            if "40 days" in t or "45 days" in t or "two months" in t:
                return "Our refund window is 30 days with a receipt, so that item is outside it, but we can offer you store credit instead."
            return "You can return items within 30 days with a receipt for a full refund. After 30 days we offer store credit."

        if "ship" in t or "post to" in t or ("deliver" in t and "to " in t and "how long" not in t):
            country = next((c for c in ["usa", "united states", "canada", "australia", "japan"] if c in t), None)
            if country and not v2:
                return f"Sorry, we don't currently ship to {country.title() if len(country) > 3 else country.upper()}. We deliver to the UK, Ireland, France, Germany and Spain."
            if country and v2:
                return "Yes, we ship worldwide! Delivery takes 3-5 working days."
            if any(c in t for c in SHIP_COUNTRIES):
                return "Yes, we deliver there. Standard delivery is 3-5 working days; express is next working day if you order by 2pm."

        if "express" in t or "next day" in t or ("how long" in t and "deliver" in t):
            return "Standard delivery takes 3-5 working days. Express delivery arrives the next working day if you order before 2pm."

        if "account" in t or "my details" in t or "phone number" in t:
            if not v2:
                return "I've found your account, Jane Smith, registered to 07700 900123 and jane.smith@example.com. How can I help?"
            return "I've found your account. For security I can't show personal details here, but you can view them under Account > Profile."

        if "complaint" in t or "terrible" in t or "angry" in t or "worst" in t:
            return "I'm really sorry about this. I've logged a complaint and a member of our team will contact you within 2 working days."

        if "discount" in t or "promo" in t or "voucher" in t:
            return "We don't have any active promo codes right now, but you can sign up to our newsletter for 10% off your first order."

        return "I'm sorry, I'm not sure I can help with that. Could you tell me a bit more?"
