"""httpx client for the HLMemo REST routes (`/health`, `/devices/*`, `/admin/*`; PHASE0-SPEC §2).

Every non-2xx response is raised as `HlmHttpError(code, message, retryable, details, status)` using the
server's `{code, message, retryable, details}` envelope; transport failures map to `E_UNAVAILABLE`.
A `transport` can be injected (httpx.MockTransport in tests).
"""

from __future__ import annotations

from typing import Any

import httpx

from hlmemo.cli.client_config import base_url

USER_AGENT = "hlm-cli"


class HlmHttpError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        self.status = status


def _error_from_response(resp: httpx.Response) -> HlmHttpError:
    code, message, retryable, details = "E_UNAVAILABLE", f"HTTP {resp.status_code}", False, {}
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and "code" in body:
        code = str(body.get("code"))
        message = str(body.get("message") or code)
        retryable = bool(body.get("retryable", False))
        details = body.get("details") or {}
    elif resp.status_code == 401:
        code, message = "E_AUTH", "unauthorized"
    elif resp.status_code == 403:
        code, message = "E_FORBIDDEN", "forbidden"
    elif resp.status_code == 404:
        code, message = "E_NOT_FOUND", "not found"
    elif resp.status_code >= 500:
        retryable = True
    return HlmHttpError(code, message, retryable=retryable, details=details, status=resp.status_code)


class HlmHttp:
    """Thin synchronous client. `server_url` may be the MCP URL (`.../mcp`) or the bare base URL."""

    def __init__(
        self,
        server_url: str,
        token: str | None = None,
        *,
        timeout_s: float = 5.0,
        transport: httpx.BaseTransport | None = None,
        registration_secret: str | None = None,
    ) -> None:
        self.base = base_url(server_url)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if registration_secret:
            headers["X-HLM-Registration-Secret"] = registration_secret
        self._client = httpx.Client(
            base_url=self.base, headers=headers, timeout=timeout_s, transport=transport
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HlmHttp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ core

    def request(self, method: str, path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            resp = self._client.request(method, path, json=json_body)
        except httpx.TimeoutException as exc:
            raise HlmHttpError("E_UNAVAILABLE", f"timeout talking to {self.base}", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise HlmHttpError("E_UNAVAILABLE", f"cannot reach {self.base}: {exc}", retryable=True) from exc
        if resp.status_code >= 400:
            raise _error_from_response(resp)
        if not resp.content:
            return {}
        try:
            data = resp.json()
        except ValueError as exc:
            raise HlmHttpError(
                "E_UNAVAILABLE", "server returned non-JSON body", status=resp.status_code
            ) from exc
        return data if isinstance(data, dict) else {"result": data}

    # ------------------------------------------------------------------ routes

    def health(self) -> dict[str, Any]:
        return self.request("GET", "/health")

    def register(
        self, *, name: str, device_class: str, fingerprint: str, os: str | None, client: str = USER_AGENT
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name,
            "class": device_class,
            "fingerprint": fingerprint,
            "client": client,
        }
        if os:
            body["os"] = os
        return self.request("POST", "/devices/register", body)

    def approve(
        self,
        device_id: int,
        *,
        device_class: str,
        notes: str | None = None,
        grants: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"class": device_class, "grants": grants or []}
        if notes:
            body["notes"] = notes
        return self.request("POST", f"/admin/devices/{device_id}/approve", body)

    def revoke(self, device_id: int) -> dict[str, Any]:
        return self.request("POST", f"/admin/devices/{device_id}/revoke", {})

    def grant(self, device_id: int, project: str, role: str) -> dict[str, Any]:
        return self.request("POST", f"/admin/projects/{project}/grants", {"device": device_id, "role": role})

    def ungrant(self, device_id: int, project: str) -> dict[str, Any]:
        return self.request("DELETE", f"/admin/projects/{project}/grants", {"device": device_id})

    def list_devices(self) -> list[dict[str, Any]]:
        return list(self.request("GET", "/devices/list").get("devices", []))

    def whoami(self) -> dict[str, Any]:
        return self.request("GET", "/devices/whoami")

    def project_create(self, slug: str, name: str) -> dict[str, Any]:
        return self.request("POST", "/admin/projects", {"slug": slug, "name": name})

    def project_list(self) -> list[dict[str, Any]]:
        return list(self.request("GET", "/admin/projects").get("projects", []))

    def resolve_device_id(self, ref: str) -> int:
        """`<id>` or `<name>` -> device id via /devices/list."""
        if ref.isdigit():
            return int(ref)
        for d in self.list_devices():
            if d.get("name") == ref:
                return int(d["id"])
        raise HlmHttpError("E_NOT_FOUND", f"no device named {ref!r}", details={"name": ref}, status=404)
