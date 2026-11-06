import os, time, json, urllib.request

NAME = "anythingllm"

class Adapter:
    def __init__(self, base_url="http://localhost:3001", workspace="northwind", **_):
        self.base_url, self.workspace = base_url, workspace
        self.key = os.environ["ANYTHINGLLM_API_KEY"]

    def config(self):
        return {"adapter": NAME, "workspace": self.workspace}

    def run(self, case, sample=0):
        start = time.perf_counter()
        body = {"message": case["input"]["messages"][-1]["content"], "mode": "query"}
        req = urllib.request.Request(
            f"{self.base_url}/api/v1/workspace/{self.workspace}/chat",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        return {"output": data.get("textResponse", ""), "latency_ms": round((time.perf_counter() - start) * 1000),
                "tokens_in": 0, "tokens_out": 0, "cost_usd": 0}
