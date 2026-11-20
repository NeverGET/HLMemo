# Rust port assessment (asked by Cemal, 2026-11-20)

Question: what would moving HLMemo from Python to Rust cost; what ports, what does not; which libraries we would
write ourselves; the difference in performance and use.

Baseline (2026-11-20, main 4b6345d): src 57,774 lines of Python in 14 packages (librarian 16.4k, core 11.8k, cli 5.0k,
db 3.5k, server 3.2k, importers 3.1k, ops 3.0k, bench 2.9k, migrate 2.1k, curate 2.0k, capture 1.6k, brief 1.5k,
worker 0.9k, auth 0.3k); tests 71,902 lines; 11 alembic migrations; tools 1.2k Python + 3.7k shell.
Runtime deps: mcp 2.2, psycopg 3.3 (+pool), alembic, pgvector, onnxruntime (multilingual-e5-small), tokenizers,
tiktoken, pydantic 2, pydantic-settings, typer, httpx, keyring, starlette, uvicorn, sqlalchemy, jsonschema.

Workstreams (parallel agents, outputs in this folder):
- A-inventory.md: per package, what it does, Python-specific reliance, SQL vs Python share, hot paths, port notes.
- B-ecosystem.md: per dependency, the Rust equivalent, maturity and version (sourced), gaps we would write.
- C-performance.md: where time goes today (measured or recorded numbers), what Rust could change.
Then REPORT.md: effort estimate, portable / not portable, own libraries, performance and use, recommendation.
