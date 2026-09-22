# Legacy memory estate — public summary (2026-09-22)
Detailed inventory (project names, paths) is private (docs/private/, ops repo). Aggregate facts that shape the importer design:

| Source | Projects | Files | Bytes | Est. tokens | Format |
|---|---|---|---|---|---|
| Serena MCP memories (`.serena/memories/*.md`) | 24 non-empty (30 dirs) | 363 | 2.51 MB | ~626k | Markdown, H1-first, no frontmatter |
| Claude auto-memory (`~/.claude/projects/*/memory/`) | 18 | 120 | 371 KB | ~93k | Markdown, MEMORY.md index |
| Context files (CLAUDE/AGENTS/GEMINI/.mcp.json) | 27 | 34 | 184 KB | ~46k | Markdown/JSON |
| Codex (`~/.codex`) | 1 | 2 | 54 KB | ~13k | AGENTS.md + **SQLite** memory DB |
| NotebookLM | 10 real (+6 empty) | 208 sources | n/a | n/a | Notes + sources via `nlm` |
| **Total** | ~31 | 520 | 3.13 MB | ~782k | 2025-11 → 2026-09 |

## Importer design constraints derived from the inventory
1. **No frontmatter anywhere.** Title/date/tags must be derived from filename + first H1 + mtime.
2. **Two naming conventions** (numeric-ordered `01_...md` vs topical kebab/snake). Dedup keys normalize separators and strip numeric prefixes.
3. **Monorepo duplication**: the same memory file copied at root + sub-package levels (est. 30–50% redundancy in the largest tree). Content-hash dedupe before embedding.
4. **Cross-system duplication**: "lessons-learned"/"recent-changes" themes exist simultaneously in serena, auto-memory MEMORY.md and NotebookLM. Needs a 3-way merge policy with provenance, not union.
5. **Stub files** (11-byte `@import` one-liners) must be resolved or skipped.
6. **Mixed TR/DE/EN**; ~180 files contain Turkish characters. Confirms multilingual-e5 + language-agnostic chunking (D-008).
7. **Abandoned scaffolds** (6 empty serena dirs, 6 empty notebooks) filtered out; no empty HLMemo projects.
8. **Codex is the outlier**: SQLite store needs a dedicated extractor; everything else is a filesystem walk.
9. **Staleness spread is wide** (10 months). Import original mtime as `recorded_at`; projects untouched >9 months are archived on import (reversible, D-012).
10. Largest single memory file is 93 KB → chunker must handle long documents; RAPTOR-style summaries (deferred) will matter for these.
