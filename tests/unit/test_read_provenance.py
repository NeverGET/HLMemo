"""Content provenance remains readable through long survivor chains and rejects cycles."""

from types import SimpleNamespace

import pytest

from hlmemo.core import read_service
from hlmemo.core.errors import ToolError


@pytest.mark.parametrize("cycle", [False, True])
async def test_survivor_provenance_long_chain(monkeypatch, cycle: bool) -> None:
    versions = {i: SimpleNamespace(version_id=i, source_event_id=i) for i in range(1, 81)}
    original = {"kind": "fact", "title": "Original", "body": "Public evidence"}

    async def source_event(conn, event_id):
        if event_id == 1 and not cycle:
            payload = {
                "request": {"items": [original]},
                "resolved": {"items": [{"index": 0, "version_id": 1}]},
            }
        else:
            payload = {
                "request": {"items": [{"body": "Restricted correction"}]},
                "resolved": {
                    "items": [
                        {
                            "version_id": 1000 + event_id,
                            "survivors": [{"version_id": event_id, "from_version_id": event_id - 1 or 80}],
                        }
                    ]
                },
            }
        return SimpleNamespace(event_id=event_id, payload=payload)

    async def version_authz(conn, vid, pid, scopes):
        assert pid == 1 and scopes == ["all"]
        return versions[vid]

    monkeypatch.setattr(read_service.q, "source_event", source_event)
    monkeypatch.setattr(read_service.q, "version_authz", version_authz)
    if cycle:
        with pytest.raises(ToolError) as exc:
            await read_service._provenance(None, versions[80], 1, ["all"])
        assert exc.value.code == "E_NOT_FOUND"
    else:
        event, item = await read_service._provenance(None, versions[80], 1, ["all"])
        assert event.event_id == 1
        assert item == original
