# Active librarian ceiling experiment: restore and run

The harness for the ceiling experiment of the active librarian plan (§3). It only reads a restored
copy of the production database; it never writes to production. Everything derived from real data
goes to the private directory (`$HLM_AL_PRIVATE_DIR`, by default the gitignored
`docs/private/active-librarian/`); the harness refuses to write anywhere else.

## 1. Restore the dump into `hlm_al_ceiling`

```sh
eval/active/restore.sh docs/private/active-librarian/dump/<dump file> [--replace]
```

- Default target: a dedicated compose project `hlmal`, running ONLY the repo's `compose.yaml` `db`
  service on `127.0.0.1:${HLM_AL_DB_PORT:-55432}`, with its own volume `hlmal_pgdata`. `compose.yaml`
  pins `name: hlmemo`; the script forces `-p hlmal`, checks that compose resolved that name, and
  refuses otherwise. It never runs `down`, never prunes and never touches another project, container
  or volume.
- It creates the NEW database `hlm_al_ceiling` and restores the dump inside the container
  (`pg_restore --no-owner --no-acl --exit-on-error --single-transaction`; the dump needs
  pg_restore 17). `--replace` drops and recreates `hlm_al_ceiling` only.
- Alternative: `HLM_AL_ADMIN_DSN=<dsn of an existing Postgres with pgvector>` restores there with the
  host's `psql`/`pg_restore` (version 17 or later).
- It prints the alembic version and the row count of every table.
- It never uses `deploy/backup/restore.sh`.

Stop the copy later with `docker compose -p hlmal -f compose.yaml stop db`. Remove it, volume
included, with `docker compose -p hlmal -f compose.yaml down -v`; this affects project `hlmal` only.

## 2. Run the experiment

Every step runs from the repo root:

```sh
export HLM_AL_DSN=postgresql://hlm:hlm@127.0.0.1:55432/hlm_al_ceiling    # the default
AL="uv run --frozen python eval/active/al.py"

$AL build --exp all [--dry-run-dir <dir of dry-run capture payloads>]   # read-only SQL -> packets/
$AL mustknow-kit        # inputs for the independent agent that writes mustknow/<E2 packet>.json
$AL estimate            # Gemini cost per experiment from the packet sizes
$AL prereg              # PREREG.md + PREREG.sha256, BEFORE any arm
$AL run --exp E1 --arm gemini --run 1     # and --run 2; the same for E2, E3
$AL run --exp E1 --arm opus               # once per experiment
$AL check --exp E1      # deterministic grounding of every saved output
$AL kit --exp E1        # blind kit: grading/E1/reader-*/ ; key: grading-key/E1.key.json
$AL split --exp E1      # reader C: only the units A and B disagree on
$AL score --exp E1      # pre-registered go/no-go -> scores/E1.{json,md}
```

E0 has no arms: run `build`, `prereg`, `kit`, `split` and `score` with `--exp E0`.

### Additional session notes for E1

The plan asks for at least 15 session notes. Captured notes in the database are few, so the
dry-run captures of recent transcripts make up the rest. Write them with the capture pipeline in
dry-run mode, which never writes to production:

```sh
uv run --frozen python -m hlmemo.capture.run --transcript <session.jsonl> --cwd <project dir> \
    --session-id <id> --dry-run docs/private/active-librarian/dryrun
```

Then run `build --exp E1 --dry-run-dir docs/private/active-librarian/dryrun`.

## 3. Guards

- **Read-only:** the database DSN must name `hlm_al_ceiling`, and every session sets
  `default_transaction_read_only`.
- **Pre-registration:** `run`, `check`, `kit`, `split` and `score` refuse without a pre-registration
  whose file hash and recorded input hashes all still match. The recorded inputs are the config, the
  prompts, the schemas, the reader instructions, the profile, the packets and the must-know files.
  `prereg` refuses once any arm output exists.
- **Spend:** the Gemini arm reserves every attempt's worst case against `spend_cap_usd` minus
  everything already in `runs/gemini-ledger.jsonl`. A refused reservation stops the run.
- **Raw outputs:** the raw outputs are saved by code: Gemini's attempts in `<packet>.raw.json`,
  Opus's stdout in `<packet>.attempt<N>.stream.jsonl`. Nobody retypes model output.
- **Blind kit:** the grading key lives outside the grading directory that readers receive.
