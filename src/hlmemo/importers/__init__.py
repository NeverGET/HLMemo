"""W1.5 importers (Phase 1.5, D-020/D-021/D-022): legacy memories → HLMemo items with provenance.

Client-side (the files live on the owner's machines). ``hlm import <source> --project SLUG``
with ``source`` ∈ {markdown, automemory, serena, context}; ``hlm export`` writes the re-importable
Markdown fallback. Modules: ``common`` (parsing rules), ``build`` (file → records), one module
per source, ``exportfmt`` (the export format), ``plan`` (classification + report), ``runner``
(MCP I/O), ``cli`` (command bodies).
"""
