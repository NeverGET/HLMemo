"""Input JSON schemas of the five tools, verbatim from PHASE0-SPEC §3.

``tools/list`` advertises exactly these ``inputSchema`` objects and nothing else (D-024 (6): no
``outputSchema`` — the output shapes are validated internally by the services). Each schema
embeds the shared ``$defs`` so it is self-contained on the wire.
"""

from __future__ import annotations

from typing import Any

SLUG_RE = r"^[a-z0-9][a-z0-9-]{1,63}$"

DEFS: dict[str, Any] = {
    "Slug": {"type": "string", "pattern": SLUG_RE},
    "Uuid": {"type": "string", "format": "uuid"},
    "Ts": {"type": "string", "format": "date-time"},
    "Budget": {"type": "integer", "minimum": 256, "maximum": 32000},
    "Kind": {
        "enum": ["fact", "episode", "lesson", "experience", "project_card", "session_note", "doc_chunk"]
    },
    "Rel": {"enum": ["relates_to", "contradicts", "supersedes", "derived_from", "depends_on"]},
    "Clue": {"type": "string", "pattern": r"^v[0-9]+(\.[0-9]+)?$"},
    "DeviceScope": {
        "type": "string",
        "pattern": r"^(all|class:(personal|work|server|ci|other)|device:[0-9]+)$",
    },
    "Item": {
        "type": "object",
        "properties": {
            "kind": {"$ref": "#/$defs/Kind"},
            "logical_id": {"type": "integer"},
            "expected_version_id": {"type": "integer"},
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "body": {"type": "string", "minLength": 1, "maxLength": 64000},
            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 32},
            "pinned": {"type": "boolean"},
            "stability": {"enum": ["stable", "volatile"]},
            "importance": {"type": "integer", "minimum": 1, "maximum": 10},
            "project_ids": {
                "type": "array",
                "items": {"$ref": "#/$defs/Slug"},
                "minItems": 1,
                "maxItems": 16,
                "uniqueItems": True,
            },
            "device_scope": {"$ref": "#/$defs/DeviceScope", "default": "all"},
            "valid_from": {"$ref": "#/$defs/Ts"},
            "valid_to": {"anyOf": [{"$ref": "#/$defs/Ts"}, {"type": "null"}]},
            "links": {
                "type": "array",
                "maxItems": 32,
                "items": {
                    "type": "object",
                    "properties": {
                        "rel": {"$ref": "#/$defs/Rel"},
                        "target": {
                            "type": ["integer", "string"],
                            "description": 'logical_id or "$<item_index>"',
                        },
                        "target_version_id": {
                            "type": "integer",
                            "description": (
                                "pin the immutable target version "
                                "(default: head at write time; recorded in payload.resolved)"
                            ),
                        },
                    },
                    "required": ["rel", "target"],
                    "additionalProperties": False,
                },
            },
            # W1.5 (additive, optional): import provenance and the code paths an item describes.
            "source": {
                "type": "object",
                "properties": {
                    "system": {"type": "string", "pattern": r"^[a-z][a-z0-9_-]{0,31}$"},
                    "path": {"type": "string", "minLength": 1, "maxLength": 512},
                    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "mtime": {"$ref": "#/$defs/Ts"},
                    "commit": {"type": "string", "pattern": "^[0-9a-f]{7,64}$"},
                    "commit_date": {"$ref": "#/$defs/Ts"},
                },
                "required": ["system", "path", "sha256"],
                "additionalProperties": False,
            },
            "describes": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 256},
                "maxItems": 16,
                "uniqueItems": True,
            },
        },
        "required": ["kind", "title", "body"],
        "additionalProperties": False,
    },
}


def _schema(properties: dict[str, Any], required: list[str], *, defs: tuple[str, ...]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {k: DEFS[k] for k in defs},
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


QUERY_INPUT = _schema(
    {
        "project": {"$ref": "#/$defs/Slug"},
        "query": {"type": "string", "minLength": 1, "maxLength": 2000},
        "token_budget": {"$ref": "#/$defs/Budget"},
        "valid_at": {"$ref": "#/$defs/Ts"},
        "known_at": {"$ref": "#/$defs/Ts"},
        "include_archived": {"type": "boolean", "default": False},
        "kinds": {"type": "array", "items": {"$ref": "#/$defs/Kind"}},
    },
    ["project", "query", "token_budget"],
    defs=("Slug", "Ts", "Budget", "Kind"),
)

DRILLDOWN_INPUT = _schema(
    {
        "project": {"$ref": "#/$defs/Slug"},
        "clue_ids": {
            "type": "array",
            "items": {"$ref": "#/$defs/Clue"},
            "minItems": 1,
            "maxItems": 20,
            "uniqueItems": True,
        },
        "token_budget": {"$ref": "#/$defs/Budget"},
        "cursor": {"type": "string"},
        "valid_at": {"$ref": "#/$defs/Ts"},
        "known_at": {"$ref": "#/$defs/Ts"},
    },
    ["project", "clue_ids", "token_budget"],
    defs=("Slug", "Clue", "Ts", "Budget"),
)

RAW_INPUT = _schema(
    {
        "project": {"$ref": "#/$defs/Slug"},
        "version_id": {"type": "integer", "minimum": 1},
        "token_budget": {"$ref": "#/$defs/Budget"},
        "cursor": {"type": "string"},
    },
    ["project", "version_id", "token_budget"],
    defs=("Slug", "Budget"),
)

WRITE_INPUT = _schema(
    {
        "project": {"$ref": "#/$defs/Slug"},
        "request_id": {"$ref": "#/$defs/Uuid"},
        "occurred_at": {"$ref": "#/$defs/Ts"},
        "client": {"type": "string", "minLength": 1, "maxLength": 200},
        "items": {"type": "array", "items": {"$ref": "#/$defs/Item"}, "minItems": 1, "maxItems": 50},
        "token_budget": {"$ref": "#/$defs/Budget", "default": 2000},
    },
    ["project", "request_id", "client", "items"],
    defs=("Slug", "Uuid", "Ts", "Budget", "Kind", "Rel", "DeviceScope", "Item"),
)

CALL_THE_DAY_INPUT = _schema(
    {
        "project": {"$ref": "#/$defs/Slug"},
        "request_id": {"$ref": "#/$defs/Uuid"},
        "session_id": {"$ref": "#/$defs/Uuid"},
        "occurred_at": {"$ref": "#/$defs/Ts"},
        "client": {"type": "string", "minLength": 1, "maxLength": 200},
        "notes": {"type": "string", "minLength": 1, "maxLength": 64000},
        "decisions": {"type": "array", "items": {"type": "string"}, "maxItems": 32},
        "lessons": {
            "type": "array",
            "maxItems": 16,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
                    "body": {"type": "string", "minLength": 1, "maxLength": 64000},
                    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 32},
                    "device_scope": {"$ref": "#/$defs/DeviceScope", "default": "all"},
                },
                "required": ["title", "body"],
                "additionalProperties": False,
            },
        },
        "card_update": {
            "type": "object",
            "properties": {
                "body": {"type": "string", "minLength": 1, "maxLength": 64000},
                "expected_version_id": {"type": "integer"},
            },
            "required": ["body"],
            "additionalProperties": False,
        },
        "expected_versions": {
            "type": "array",
            "maxItems": 64,
            "items": {
                "type": "object",
                "properties": {"logical_id": {"type": "integer"}, "version_id": {"type": "integer"}},
                "required": ["logical_id", "version_id"],
                "additionalProperties": False,
            },
        },
        "token_budget": {"$ref": "#/$defs/Budget", "default": 2000},
    },
    ["project", "request_id", "session_id", "client", "notes"],
    defs=("Slug", "Uuid", "Ts", "Budget", "DeviceScope"),
)

__all__ = [
    "CALL_THE_DAY_INPUT",
    "DEFS",
    "DRILLDOWN_INPUT",
    "QUERY_INPUT",
    "RAW_INPUT",
    "WRITE_INPUT",
]
