# `hlm review` and the owner token

`hlm review` is the owner's LLM-free batch review of the librarian's open questions: accept, reject
or skip in short batches. Decisions go through `memory.answer`, which works with the device bearer.
The full listing comes from the client tool `hlm.questions`, which is **owner-only**. The server
runs it only when the request carries the **owner token** in the `X-HLM-Owner-Token` header, in
addition to the device bearer.

Without the owner token, `hlm review` still works. It lists only the newest ≤ 3 notices from
`memory.query`, with no reason, quotes, age or paging, and the header says why.

Each card also shows the librarian's own doubts next to the proposal: its verifier, which is the
kind and verdict of the second opinion (for example `verifier  supersede (disagrees)`), and its
guard flags (for example `flags     supersedes_against_time · verifier_direction_disputed`). They come
from the read-only `hlm.questions` fields `verifier_kind`, `verified` and `flags`; the owner-token
rules below are unchanged.

## Threat model

Every agent on the owner's machine holds the same device bearer as the `hlm` CLI. The bearer is
fixed in the agent's MCP entry by `hlm mcp add`. The owner token separates the owner from those
agents.

**What the gate stops**

- **The MCP path.** An agent's `tools/call` carries only the bearer from its MCP entry. So a
  prompt-injected or curious agent cannot call `hlm.questions`. Caller labels (`User-Agent`,
  `x-hlm-client`) are not credentials. The server refuses a missing, wrong or short token, or an
  unconfigured server, with one `E_FORBIDDEN` message. It compares in constant time even when no
  token is configured. The refusal happens before any read.
- **Accidental inheritance.**
  - The CLI never reads the owner token from an environment variable. The token lives only in the
    macOS keychain, as generic-password service `hlm-owner-token` with the server base URL as the
    account. A process therefore cannot inherit it the way it inherits an exported variable.
  - `hlm review` reads the keychain only when stdin AND stdout are terminals. An agent's shell
    tool has piped stdio, so an agent that runs `hlm review` gets the notices instead. This covers
    `--decisions` too.
  - The header goes only on the `hlm.questions` requests. It never goes on `memory.answer`,
    drilldown or export.
  - `hlm claude|codex|agy` also strips `HLM_OWNER_TOKEN` from the agent's environment, as defence
    in depth.

**What it does not stop**

An agent with deliberate shell access to the owner's account already has owner-level power. It
can:

- read the keychain item (macOS may ask once);
- read `deploy/.local/<host>/`, which holds the SSH access to the server and a copy of `api.env`;
- run `hlm review` under a pseudo-terminal (`script`).

Stopping that agent is not a goal of this gate. Do not give agents unattended shell access to the
owner account if that matters.

**Limits of the token**

- The token unlocks nothing beyond the device. `hlm.questions` still applies the device's project
  grants and device/class scope to every question.
- `hlm.export` is **not** owner-only. It is a bulk read of exactly what that device can read with
  `memory.query`, `memory.drilldown` and `memory.raw`. Making it owner-only is an open owner
  decision (review 102).

## 1. Generate a token

Use 32 or more printable characters with no spaces; the server ignores a shorter token. Generate
it in your own terminal, not in an agent's:

```sh
TOKEN=$(openssl rand -hex 32)      # 64 hex characters; never pass it as a command-line argument
```

## 2. Set it on the server (production)

The API reads `HLM_OWNER_TOKEN` from `/etc/hlmemo/api.env`. That file is the API-only env file,
loaded through `env_file` in `compose.prod.yaml`. The worker does not need the token.

How the deploy tooling treats `api.env`:

| Tool | Behaviour |
|---|---|
| `first_deploy.sh` | Generates `api.env` from `deploy/api.env.example` only on the first install. On a re-run it refuses to replace a host `api.env` that differs from the local copy `deploy/.local/<host>/secrets/api.env` ("refusing to replace a live secret"), unless the files differ only in the retired W0 keys. Keep the two files equal. |
| `deploy.sh` / `remote-deploy.sh` / `rollback.sh` | Never compare `api.env` with the template. `migrate_env_w0` removes only `HLM_ADMIN_TOKEN` and `HLM_REGISTRATION_SECRET`, so `HLM_OWNER_TOKEN` survives deploys and rollbacks. |
| Older releases | Ignore the unknown key (unknown settings are ignored). |
| `deploy/api.env.example` | Lists the key commented out. A fresh install has no token until you add one. |

Write the same line to the host file and to the local copy. The value goes through stdin, never
through argv. The `env_file` is read when the container is created, so recreate the API
afterwards; a restart is not enough:

```sh
STATE=deploy/.local/<host>
# host copy: replace or add the line, keeping mode 0600
printf '%s\n' "$TOKEN" | ssh -F "$STATE/ssh_config" hlm-deploy 'set -eu; umask 077; read -r t
  f=/etc/hlmemo/api.env; { grep -v "^HLM_OWNER_TOKEN=" "$f" || true; printf "HLM_OWNER_TOKEN=%s\n" "$t"; } > "$f.new"
  chmod 0600 "$f.new"; mv "$f.new" "$f"'
# local copy (what first_deploy.sh compares against)
f="$STATE/secrets/api.env"; { grep -v '^HLM_OWNER_TOKEN=' "$f" || true; printf 'HLM_OWNER_TOKEN=%s\n' "$TOKEN"; } > "$f.new" \
  && chmod 0600 "$f.new" && mv "$f.new" "$f"
# recreate the api only (db, worker, caddy and migrate are left alone; about a minute of 502s)
ssh -F "$STATE/ssh_config" hlm-deploy \
  'cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh up -d --no-deps api' </dev/null
```

**Local compose stack:**

1. Put `HLM_OWNER_TOKEN=<token>` in `.hlm-dev.env`. It is gitignored, mode 0600, and the api's
   `env_file`; a shell export is not seen.
2. Run `make up`.
3. Store the token for the local server URL (step 3 below).

## 3. Store it on the owner's machine

In a terminal, run:

```sh
echo "$TOKEN"; hlm review --set-owner-token; unset TOKEN; clear   # paste at the hidden prompt
```

`--set-owner-token` works only in a terminal. It reads the token with a hidden prompt and never
echoes it. It stores the token in the keychain item `hlm-owner-token` / `<server base url>`, and
nowhere else. It fails if no keychain works. Never put the token into:

- your shell profile or an `export`;
- `hlm.toml`;
- an agent's MCP entry or environment.

## 4. Verify

Check that the owner path works. In a terminal, run `hlm review --dry-run`. The header should read
`<project>: N open question(s) (...); this batch: ..., oldest first`, with no "owner-only" line.
Quit with `q`; nothing is sent.

Check that a non-terminal run is refused. Run `hlm review --dry-run </dev/null | cat`. The header
should say `hlm.questions is owner-only and not a terminal, so the owner token is not used`.

Check that the server refuses the bearer alone. The value never reaches argv or output:

```sh
DEV=$(security find-generic-password -s hlmemo -a "<device_name>@https://<domain>" -w)
curl -s "https://<domain>/mcp" -H "Authorization: Bearer $DEV" \
  -H 'Accept: application/json, text/event-stream' -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"hlm.questions","arguments":{"project":"<slug>"}}}' \
  | grep -o E_FORBIDDEN; unset DEV
```

The command prints `E_FORBIDDEN`.

## 5. Rotate

1. Generate a new token (step 1).
2. Write it to the host and the local copy, then recreate the API (step 2). The server holds one
   value, so the old token stops working the moment the new container starts.
3. Optionally, prove the old token is refused. Before storing the new one, run
   `hlm review --dry-run` in a terminal. It should report
   `the server refused the owner token for hlm.questions`.
4. Store the new token with `hlm review --set-owner-token`, which overwrites the old one. Then
   verify (step 4).

## Disable

1. Remove the `HLM_OWNER_TOKEN=` line from the host `api.env` and from the local copy.
2. Recreate the API. `hlm.questions` is now refused for everyone and `hlm review` lists the
   notices.
3. Delete the local item:
   `security delete-generic-password -s hlm-owner-token -a "https://<domain>"`.
