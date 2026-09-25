"""Tiny MCP client for the R3 rehearsal: the token is read from the isolated hlm config's
credentials.toml (0600), never from argv, and is never printed."""
import json, os, time, tomllib, uuid
import httpx

URL = os.environ.get("HLM_URL", "https://localhost:19443")

def token(device: str) -> str:
    cfg = os.environ["HLM_CONFIG_DIR"]
    with open(os.path.join(cfg, "credentials.toml"), "rb") as fh:
        toks = tomllib.load(fh)["tokens"]
    for k, v in toks.items():
        if k.startswith(device + "@"):
            return v
    raise SystemExit(f"no token for {device}")

class Client:
    def __init__(self, device: str, timeout: float = 60.0):
        self.c = httpx.Client(base_url=URL, verify=os.environ.get("SSL_CERT_FILE", True), timeout=timeout)
        self.h = {"Authorization": f"Bearer {token(device)}", "Accept": "application/json, text/event-stream",
                  "Content-Type": "application/json"}
        r = self.c.post("/mcp", headers=self.h, json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "r3-rehearsal", "version": "1"}}})
        r.raise_for_status()
        self.h["MCP-Protocol-Version"] = r.json()["result"]["protocolVersion"]
        self.n = 0

    def rpc(self, method, params):
        self.n += 1
        r = self.c.post("/mcp", headers=self.h, json={"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        return r

    def call(self, name, arguments):
        t = time.perf_counter()
        r = self.rpc("tools/call", {"name": name, "arguments": arguments})
        ms = (time.perf_counter() - t) * 1000
        if r.status_code != 200:
            return True, {"http": r.status_code, "text": r.text[:300]}, ms
        res = r.json().get("result") or {}
        try:
            payload = json.loads(res["content"][0]["text"])
        except Exception:
            payload = res
        return bool(res.get("isError")), payload, ms

    def write(self, project, items, client="r3-rehearsal"):
        return self.call("memory.write", {"project": project, "request_id": str(uuid.uuid4()), "client": client, "items": items})

    def query(self, project, q, budget=1024):
        return self.call("memory.query", {"project": project, "query": q, "token_budget": budget})
