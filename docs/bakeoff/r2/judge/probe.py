#!/usr/bin/env python3
"""Neutral judge probe for the R2 deploy bake-off (docs/bakeoff/r2/BRIEF.md, G-D2/G-D3/G-D4).

Talks to a running HLMemo stack over plain HTTP(S) with httpx only; it imports nothing from the
contestant's code and nothing from `hlmemo` either, so it verifies the wire contract end to end.

Endpoint sequence (taken from src/hlmemo/server/{app,devices,admin,middleware,mcp_server}.py):
  GET  /ready                          -> 200 (no bearer)
  GET  /health                         -> 200 (no bearer)
  GET  /definitely-not-a-route         -> 404 expected behind Caddy (the bare api answers 401)
  POST /devices/register               {name, class, fingerprint, os, client} -> 201 {device, token}
       (+ header X-HLM-Registration-Secret when HLM_REGISTRATION_SECRET is set)
  POST /admin/devices/{id}/approve     {class}                        (bearer = admin token)
  POST /admin/projects                 {slug, name} -> 201, or 409-ish if it exists (then GET-listed)
  POST /admin/projects/{slug}/grants   {device, role:"write"}         (bearer = admin token)
  GET  /devices/whoami                 (bearer = device token) -> grant on the project
  POST /mcp  initialize, notifications/initialized, tools/list      (bearer = device token)
       stateless streamable HTTP with JSON responses; SSE bodies are parsed too, just in case.

Usage:
  HLM_ADMIN_TOKEN=... uv run --frozen python docs/bakeoff/r2/judge/probe.py \
      --base https://localhost:8443 --insecure --mode smoke --state /tmp/judge-state.json
  ... --mode write-marker --marker judge-XYZ --state /tmp/judge-state.json
  ... --mode read-marker  --marker judge-XYZ --state /tmp/judge-state.json

The admin token may be given as --admin-token or (preferred, keeps it out of `ps`) via the
HLM_ADMIN_TOKEN environment variable. It is never printed.
Output: one line per check `PASS|FAIL <check> <detail>`; exit 1 if any check failed.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

EXPECTED_TOOLS = {
    "memory.query",
    "memory.drilldown",
    "memory.raw",
    "memory.write",
    "memory.call_the_day",
}
CLIENT = "judge-probe/1"
PROTOCOL_VERSION = "2025-06-18"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class Probe:
    def __init__(self, args: argparse.Namespace) -> None:
        self.base = args.base.rstrip("/")
        self.admin_token: str | None = args.admin_token or os.environ.get("HLM_ADMIN_TOKEN") or None
        self.reg_secret = os.environ.get("HLM_REGISTRATION_SECRET") or None
        self.project: str = args.project
        self.state_path: Path | None = Path(args.state) if args.state else None
        self.failed = False
        self.http = httpx.Client(
            base_url=self.base,
            verify=not args.insecure,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": CLIENT, "X-HLM-Client": CLIENT},
            follow_redirects=False,
        )
        self.device_token: str | None = None
        self.device_id: int | None = None
        self.protocol_version: str | None = None
        self._rpc_id = 0

    # ------------------------------------------------------------------ reporting

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        detail = self._scrub(detail)
        print(f"{'PASS' if ok else 'FAIL'} {name} {detail}".rstrip(), flush=True)
        if not ok:
            self.failed = True
        return ok

    def _scrub(self, text: str) -> str:
        for s in (self.admin_token, self.device_token, self.reg_secret):
            if s:
                text = text.replace(s, "<redacted>")
        return text.replace("\n", " ")[:400]

    # ------------------------------------------------------------------ state

    def load_state(self) -> bool:
        if not self.state_path or not self.state_path.is_file():
            return False
        st = json.loads(self.state_path.read_text())
        if st.get("base") != self.base:
            return False
        self.device_token = st["device_token"]
        self.device_id = int(st["device_id"])
        self.project = st.get("project", self.project)
        return True

    def save_state(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "base": self.base,
            "device_id": self.device_id,
            "device_token": self.device_token,
            "project": self.project,
        }
        fd = os.open(self.state_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)

    # ------------------------------------------------------------------ http helpers

    def _admin(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.admin_token}"}

    def _dev(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.device_token}"}

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.get(path, **kw)

    # ------------------------------------------------------------------ checks

    def basic_http(self) -> None:
        for path, want in (("/ready", 200), ("/health", 200)):
            try:
                r = self.get(path)
                self.check(
                    f"http{path}", r.status_code == want, f"status={r.status_code} body={r.text[:120]}"
                )
            except httpx.HTTPError as exc:
                self.check(f"http{path}", False, f"{type(exc).__name__}: {exc}")
        try:
            r = self.get("/definitely-not-a-route")
            self.check(
                "http/unknown-route-404", r.status_code == 404, f"status={r.status_code} body={r.text[:120]}"
            )
        except httpx.HTTPError as exc:
            self.check("http/unknown-route-404", False, f"{type(exc).__name__}: {exc}")

    def bootstrap(self) -> bool:
        if not self.admin_token:
            return self.check("bootstrap", False, "no admin token (--admin-token or HLM_ADMIN_TOKEN)")
        name = f"judge-{secrets.token_hex(4)}"
        body = {
            "name": name,
            "class": "ci",
            "fingerprint": f"judge-probe-{uuid.uuid4()}",
            "os": sys.platform,
            "client": CLIENT,
        }
        headers = {"X-HLM-Registration-Secret": self.reg_secret} if self.reg_secret else {}
        try:
            r = self.http.post("/devices/register", json=body, headers=headers)
            if not self.check("device/register", r.status_code == 201, f"status={r.status_code} name={name}"):
                print(f"      body={self._scrub(r.text[:300])}")
                return False
            reg = r.json()
            self.device_token = reg["token"]
            self.device_id = int(reg["device"]["id"])

            r = self.http.post(
                f"/admin/devices/{self.device_id}/approve", json={"class": "ci"}, headers=self._admin()
            )
            ok = r.status_code == 200 and r.json().get("device", {}).get("status") == "trusted"
            if not self.check(
                "device/approve", ok, f"status={r.status_code} id={self.device_id} body={r.text[:200]}"
            ):
                return False

            r = self.http.post(
                "/admin/projects", json={"slug": self.project, "name": self.project}, headers=self._admin()
            )
            if r.status_code == 201:
                self.check("project/create", True, f"slug={self.project} created")
            else:
                lst = self.get("/admin/projects", headers=self._admin())
                slugs = (
                    {p["slug"] for p in lst.json().get("projects", [])} if lst.status_code == 200 else set()
                )
                if not self.check(
                    "project/create",
                    self.project in slugs,
                    f"slug={self.project} create_status={r.status_code} exists={self.project in slugs}",
                ):
                    return False

            r = self.http.post(
                f"/admin/projects/{self.project}/grants",
                json={"device": self.device_id, "role": "write"},
                headers=self._admin(),
            )
            if not self.check(
                "project/grant-write", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}"
            ):
                return False

            r = self.get("/devices/whoami", headers=self._dev())
            grants = r.json().get("grants", []) if r.status_code == 200 else []
            ok = any(g.get("project") == self.project and g.get("role") == "write" for g in grants)
            self.check("device/whoami", ok, f"status={r.status_code} grants={grants}")
            if ok:
                self.save_state()
            return ok
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            return self.check("bootstrap", False, f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ MCP

    @staticmethod
    def _parse_rpc(r: httpx.Response) -> dict[str, Any]:
        ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype == "text/event-stream":
            last: dict[str, Any] | None = None
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload:
                        msg = json.loads(payload)
                        if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                            last = msg
            if last is None:
                raise ValueError("SSE body without a JSON-RPC response")
            return last
        return r.json()

    def rpc(
        self, method: str, params: dict[str, Any] | None = None, *, notify: bool = False
    ) -> tuple[int, dict[str, Any] | None]:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notify:
            self._rpc_id += 1
            msg["id"] = self._rpc_id
        if params is not None:
            msg["params"] = params
        headers = {**MCP_HEADERS, **self._dev()}
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        r = self.http.post("/mcp", json=msg, headers=headers)
        if notify or r.status_code == 202 or not r.content:
            return r.status_code, None
        try:
            return r.status_code, self._parse_rpc(r)
        except ValueError:
            return r.status_code, {"_raw": r.text[:300]}

    def mcp_initialize(self) -> bool:
        try:
            status, body = self.rpc(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "judge-probe", "version": "1"},
                },
            )
        except httpx.HTTPError as exc:
            return self.check("mcp/initialize", False, f"{type(exc).__name__}: {exc}")
        result = (body or {}).get("result") or {}
        pv = result.get("protocolVersion")
        ok = status == 200 and bool(pv) and "serverInfo" in result
        self.check(
            "mcp/initialize", ok, f"status={status} protocolVersion={pv} server={result.get('serverInfo')}"
        )
        if ok:
            self.protocol_version = pv
            try:
                self.rpc("notifications/initialized", notify=True)
            except httpx.HTTPError:
                pass
        return ok

    def mcp_tools_list(self) -> bool:
        try:
            status, body = self.rpc("tools/list", {})
        except httpx.HTTPError as exc:
            return self.check("mcp/tools-list", False, f"{type(exc).__name__}: {exc}")
        tools = {t.get("name") for t in ((body or {}).get("result") or {}).get("tools", [])}
        ok = status == 200 and tools == EXPECTED_TOOLS
        return self.check("mcp/tools-list", ok, f"status={status} tools={sorted(t for t in tools if t)}")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[int, bool, Any]:
        status, body = self.rpc("tools/call", {"name": name, "arguments": arguments})
        result = (body or {}).get("result") or {}
        if not result:
            return status, True, body
        content = result.get("content") or []
        text = content[0].get("text", "") if content else ""
        try:
            payload: Any = json.loads(text)
        except ValueError:
            payload = text
        return status, bool(result.get("isError")), payload

    def write_marker(self, marker: str) -> bool:
        args = {
            "project": self.project,
            "request_id": str(uuid.uuid4()),
            "client": CLIENT,
            "items": [
                {
                    "kind": "fact",
                    "title": marker,
                    "body": f"Judge probe marker fact {marker}.",
                    "tags": ["judge"],
                }
            ],
        }
        try:
            status, is_err, payload = self.call_tool("memory.write", args)
        except httpx.HTTPError as exc:
            return self.check("mcp/write-marker", False, f"{type(exc).__name__}: {exc}")
        versions = payload.get("versions", []) if isinstance(payload, dict) else []
        vid = versions[0].get("version_id") if versions else None
        ok = status == 200 and not is_err and isinstance(vid, int)
        self.check(
            "mcp/write-marker",
            ok,
            f"status={status} version_id={vid}" + ("" if ok else f" payload={payload}"),
        )
        if ok:
            print(f"VERSION_ID {vid}")
        return ok

    def read_marker(self, marker: str, wait_s: float) -> bool:
        deadline = time.monotonic() + wait_s
        last = ""
        while True:
            try:
                status, is_err, payload = self.call_tool(
                    "memory.query", {"project": self.project, "query": marker, "token_budget": 1024}
                )
            except httpx.HTTPError as exc:
                status, is_err, payload = 0, True, f"{type(exc).__name__}: {exc}"
            hits = payload.get("hits", []) if isinstance(payload, dict) else []
            found = [h for h in hits if marker in json.dumps(h, ensure_ascii=False)]
            if status == 200 and not is_err and found:
                return self.check("mcp/read-marker", True, f"hits={len(hits)} clue={found[0].get('clue')}")
            last = f"status={status} isError={is_err} hits={len(hits)} payload={str(payload)[:200]}"
            if time.monotonic() >= deadline:
                return self.check("mcp/read-marker", False, last)
            time.sleep(2)

    # ------------------------------------------------------------------ modes

    def ensure_device(self) -> bool:
        if self.load_state():
            r = self.get("/devices/whoami", headers=self._dev())
            if r.status_code == 200:
                self.check("device/state", True, f"reused device id={self.device_id} project={self.project}")
                return True
            self.check(
                "device/state", True, f"stored token rejected (status={r.status_code}); re-bootstrapping"
            )
            self.device_token = None
        return self.bootstrap()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="e.g. https://localhost:8443 (no /mcp suffix)")
    ap.add_argument("--admin-token", default=None, help="prefer env HLM_ADMIN_TOKEN")
    ap.add_argument("--insecure", action="store_true", help="skip TLS verification (Caddy internal CA)")
    ap.add_argument("--mode", choices=("smoke", "write-marker", "read-marker"), default="smoke")
    ap.add_argument("--marker", default=None)
    ap.add_argument("--state", default=None, help="JSON file persisting device token + project")
    ap.add_argument("--project", default="judge", help="project slug (default: judge)")
    ap.add_argument("--wait", type=float, default=60.0, help="read-marker: seconds to wait for a hit")
    args = ap.parse_args()

    p = Probe(args)
    if args.mode == "smoke":
        p.basic_http()
        # W0a (D-061): with no admin token the caller pre-seeds --state with an operator-minted
        # device (hlm_ops.sh device mint); ensure_device() reuses it, else falls back to bootstrap.
        if p.ensure_device():
            if p.mcp_initialize():
                p.mcp_tools_list()
    else:
        if not args.marker:
            p.check(args.mode, False, "--marker is required")
            return 2
        if p.ensure_device() and p.mcp_initialize():
            if args.mode == "write-marker":
                p.write_marker(args.marker)
            else:
                p.read_marker(args.marker, args.wait)
    print(f"RESULT {'FAIL' if p.failed else 'PASS'} mode={args.mode} base={p.base}")
    return 1 if p.failed else 0


if __name__ == "__main__":
    sys.exit(main())
