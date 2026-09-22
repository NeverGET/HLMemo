# HLMemo — Decision Log (append-only)

Format: `D-NNN | date | status | decision | rationale | consequences`

D-001 | 2026-09-22 | ACCEPTED | Deep-research report is the design baseline; deviations are logged here. | Owner request. | Every architectural change references a D-number.
D-002 | 2026-09-22 | ACCEPTED | codex/gpt-6-astra is a standing co-architect; every non-trivial design step gets a written consult in docs/consults/. | Owner request; cross-model review catches blind spots. | Adds ~5-15 min per design step.
D-003 | 2026-09-22 | ACCEPTED | Librarian LLM is a cloud model (not self-host) for now. Candidate: DeepSeek V4 Flash 0731; open to better price/perf. | Owner constraint. | Privacy tier policy required (see open questions).
D-004 | 2026-09-22 | ACCEPTED | Full local docker-compose validation with a deterministic gate precedes any VPS deployment. | Owner constraint. | Gate defined in docs/decisions/VALIDATION-GATES.md.
D-005 | 2026-09-22 | PENDING | VPS provider selection. | Research in progress. | —
D-006 | 2026-09-22 | PENDING | Graphiti reuse vs own bi-temporal Postgres schema. | Claude position: own schema (see consults/00). Awaiting codex. | —
D-007 | 2026-09-22 | PENDING | Apache AGE vs plain edges table. | Claude position: plain edges in Phase 0-1. Awaiting codex. | —
D-008 | 2026-09-22 | PENDING | Embedding model (multilingual TR/DE/EN requirement). | Claude position: bge-m3 or multilingual-e5, ONNX CPU. | —
