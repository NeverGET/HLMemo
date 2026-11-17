# Desk: a local page for the questions waiting for Cemal

Agents (the operator at home, project chats at work) write question batches as JSON; Cemal answers them in a local
page; agents read the answers back. One copy runs on each machine, over its own `docs/private/desk/` (git-ignored).
A question that needs the other machine is marked with its `place`, and travels there as a note in shared memory,
where that machine's agent adds it to its own desk (option A of 2026-11-17; a shared desk through the server can come
later).

```sh
python3 -I tools/desk/desk.py serve            # http://127.0.0.1:8765/  (--place home|work, default by OS)
python3 -I tools/desk/desk.py check            # validate every batch file
python3 -I tools/desk/desk.py status           # open/total per batch and place
python3 -I tools/desk/desk.py answers <batch>  # current answer per question, JSON, for agents
```

Stdlib only. The server binds 127.0.0.1, refuses any other `Host` header, and needs a per-run token on every write,
so another site in the browser can neither read nor answer. Local files are shown only from `docs/` or the desk
data, never secret-looking names.

## Batch file: `docs/private/desk/questions/<batch>.json`

```json
{
  "batch": "<same as the file name>",
  "title": "…", "project": "my-project", "created": "2026-11-17", "from": "operator (Mac)", "intro": "…",
  "questions": [
    {
      "id": "q1", "title": "…", "body": "mini markdown: `code`, **bold**, [text](https://…), - lists",
      "kind": "question | approval | decision | info", "place": "home | work | any",
      "group": "optional: questions with the same group can be answered together",
      "options": ["optional", "choices"],
      "refs": [
        {"label": "commit", "url": "https://github.com/…"},
        {"label": "notes", "path": "docs/private/…"},
        {"label": "memory item", "clue": "v123", "project": "my-project"},
        {"label": "work tracker", "note": "only reachable at work", "where": "work"}
      ]
    }
  ]
}
```

Change a batch by rewriting its file; keep question ids stable, because answers refer to them. A work-only
reference is a `note` with `where: "work"`: the home page cannot link it.

## Answers: `docs/private/desk/answers/<batch>.jsonl`

Append-only, one record per click, `{ts, batch, qids, type, text, option, not_for_memory, place, by}`. The newest
record per question counts; `reopen` withdraws the previous answer. Types: `answer`, `dont_know` (the agent falls
back to inference and labels it as such), `later` (stays open), `rule` (a rule instead of single answers),
`approve`, `approve_conditional` (text = the conditions), `reject`. `not_for_memory: true` means the answer guides
the work but must not be written into HLMemo.

Tests: `.venv/bin/python -m pytest --noconftest tests/unit/test_desk.py` (the suite's conftest needs a database;
the desk tests do not).
