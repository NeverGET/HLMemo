"""HLMemo librarian (PHASE2-4-ROADMAP §2, W2a foundation).

The librarian is an asynchronous system actor. The core (query/write/drilldown/raw) never waits
for it; it runs as its own service (``python -m hlmemo.librarian.worker``), loads no ONNX model,
and every model id, price or prompt quirk comes from a provider profile (D-017).

Modules: ``profiles`` (primary + fallback profile), ``redact`` (secret/PII filter), ``prompts``
(versioned prompt files), ``cassette`` (record/replay), ``budget`` (atomic worst-case
reservation), ``ledger`` (``llm_calls``), ``provider`` (OpenAI-compatible client with retry,
fallback and breaker), ``reserved`` (system device + reserved projects), ``jobs`` (enqueue +
capabilities), ``actor`` (apply-time recheck, mutations, audit events), ``roles`` (role ladder),
``memory`` (working memory), ``tasks`` (job handlers), ``worker`` (the service loop).
"""
