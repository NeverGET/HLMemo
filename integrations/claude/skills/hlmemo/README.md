# `hlmemo` skill for Claude Code

The `hlmemo` skill teaches every Claude Code chat on the owner's machine to act as an HLMemo project
writer: read before acting, write durable items into its own slug, correct with `updates`, close with
`call_the_day`, and run the catch-up and migration procedures. It is product content and agrees with
protocol v1 (`docs/protocol/HLMEMO-PROTOCOL.md`), which is copied into `references/` so the skill is
self-contained. If the two differ, the protocol wins.

Install it from the root of the HLMemo checkout (the orchestrator does this after review):

```sh
ln -s "$PWD/integrations/claude/skills/hlmemo" ~/.claude/skills/hlmemo   # a symlink follows later edits
cp -R integrations/claude/skills/hlmemo ~/.claude/skills/hlmemo          # or a copy, refreshed by hand
```

In a registered project (its folder mapped to a slug under `[projects]` in `~/.config/hlm/capture.toml`),
the SessionStart hook injects the "HLMemo mode" digest (protocol §6) and the memory brief
(`docs/brief/README.md`). The digest tells the chat to load this skill for a catch-up or a migration.
When the protocol changes, re-copy it into `references/` and update the header line's date and sha256.
