"""`hlm migrate`: the tool half of the migration kit (PLAYBOOK, `/hlm-migrate`, D-248, D-251).

A migration moves a project's legacy agent memory (auto-memory, serena, context files, a NotebookLM export,
notes) into its own HLMemo project as curated, dated items. These commands carry the mechanical parts that the
first migrations had to invent, so a project chat runs the same safe path every time:

    hlm migrate plan   --spec migration.toml          the batch table (per-file month buckets, undated last)
    hlm migrate lint   --spec migration.toml          layout, dates, tags, lessons, secrets, D-row wording
    hlm migrate seal   --spec migration.toml [--expect JSON] [--force]   seal the reviewed tree
    hlm migrate seal   --spec migration.toml --verify  check the tree against the seal
    hlm migrate run    --spec migration.toml --target local|prod [--batch B] [--apply] [--resume]
    hlm migrate verify --spec migration.toml --target local|prod   every batch must classify as unchanged
    hlm migrate recall --spec migration.toml --truthset T.jsonl [--target local] [--k 5] [--min-rate R]

The spec (`migration.toml`; the template is docs/migration/templates/migration.toml, an annotated copy is
tools/migrate/migration.example.toml). Relative paths are relative to the spec file; keep the spec and every
curated file in a private, gitignored directory.

    slug = "my-project"                  # the target project (created by the operator, D-246)
    tz = "Europe/Berlin"                 # zone of date-only evidence and of the month buckets
    estimated_marker = "Date estimated"  # optional: the body line that must accompany a `date-estimated` tag

    [paths]
    curated = "curated"                  # the curated tree that `lint` checks
    private = "."                        # seal.json and run reports land here
    repo = "/path/to/the/project/repo"   # optional: resolves `describes` pointers

    [[sources]]                          # optional; default: one markdown source = paths.curated
    importer = "markdown"                # markdown | automemory | serena | context
    path = "curated"
    section_chars = 8000

    [tags]
    closed = ["api", "deploy"]           # the closed list of topic tags (status/scope tags are always
    allowed)

    [local]                              # the scratch stack (tools/migrate/local_stack.sh)
    server_url = "http://127.0.0.1:8799/mcp"
    device = "mig-my-project"

    [prod]
    server_url = "https://memory.example.org/mcp"
    device = "my-device"

Safety rules the commands enforce:
- Batches are per FILE: every record of a file lands in the batch of the file's newest date, so a file never
  spans two runs (a later run could otherwise turn a new record into a revision of an earlier one, D-248).
- `run --apply` always runs the same batch's dry run first and stops unless that dry run shows only
  new/missing. Any failed, rejected, skipped, changed or closed count is a hard stop.
- `--target prod` needs a valid seal (tree digest and per-batch counts equal to the importer's parse) and the
  environment variable `HLM_MIGRATE_ALLOW_PROD=1`; prod writes still need the owner's OK (TEMPLATE step 5).
- `--target local` only accepts a loopback server.
- These commands never read `hlm.toml`: server and device come from the spec, the token from
  `HLM_DEVICE_TOKEN` or the credential store for (server, device).
"""
