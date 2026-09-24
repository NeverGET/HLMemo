# nvidia/nemotron-3-ultra-550b-a55b:free (OpenRouter, free) as a dev-time reviewer — same blind bake-off, 2026-09-24

Same task, prompt (`PROMPT.md`, sha1 e16aad98…), tree and key (`ANSWER-KEY.md`) as `RESULT.md`. Scored strictly with the same 0/1/2 rubric (2 = correct mechanism, 1 = partial/vague, 0 = missed; no credit for vague mentions).

## Results

| Run | Mode | K1 | K2 | K3 | K4 | K5 | K6 | Total /12 | Verdict given | Wall s | Tokens (in / cached / out / reasoning) | Clear FPs* |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A-1 | A (codex agentic) | 0 | 0 | 0 | 0 | 0 | 0 | **0** | MERGE-WITH-FIXES | 1081 | 2,173,172 / 1,706,400 / 16,297 / 12,755 | 0 (+3 non-defects) |
| A-2 | A (codex agentic) | 0 | 1 | 0 | 0 | 0 | 0 | **1** | MERGE-WITH-FIXES | 1338 | 4,232,573 / 3,555,360 / 22,501 / 14,712 | 2 (+6 non-defects) |
| B-1 | B (single shot, 784k ctx) | 0 | 0 | 0 | 0 | 0 | 0 | **0** | MERGE-WITH-FIXES | 166 | 783,651 / 0 / 1,768 / 341 | 4 (+5 non-defects) |
| B-2 | B (single shot, 784k ctx) | 0 | 0 | 0 | 0 | 0 | 0 | **0** | MERGE-WITH-FIXES | 51 | 783,651 / 777,600 / 1,194 / 286 | 7 (+3 non-defects) |
| ref | gpt-6-astra low | 2 | 1 | 0 | 2 | 1 | 2 | 8 | DO-NOT-MERGE | 166 | 66,702 total | 0 |

\*Clear FP = a factual claim the code contradicts. "Non-defect" = right fact, but by design / style / opinion / self-refuted.
The change is a known DO-NOT-MERGE (Sol 51; fixed in f64b65a); all four Nemotron runs said MERGE-WITH-FIXES.

## Per-key evidence (≤ 1 line quoted from the output)

**A-1** — K1 0: none. K2 0: none. K3 0: none. K4 0: none. K5 0 (closest, wrong mechanism): "`HTTP_TIMEOUT_S = 5.5` leaves only 0.5s margin inside `SYNTH_TIMEOUT_S = 6.0`". K6 0 (closest, different issue): "the fallback's 4.7% hard-failure rate … is not reflected in the "system accuracy" metric".

**A-2** — K1 0: none. K2 **1**: "`visible_versions` checks `project_ids && pids` (array overlap), but privacy gate requires ALL projects in item's `project_ids` to have grants." (correct co-owned mechanism, risk_queries.py:231-240 / _UNIVERSE `&&`; token-generation pin missing → 1, same as astra). K3 0: none. K4 0: none. K5 0: none. K6 0 (closest, different mechanism): "G-LIVE-D evaluation uses only 62 weak-evidence questions … Small sample size".

**B-1** — K1 0: none. K2 0 (wrong mechanism): "Privacy gate re-check uses `sent` (allowed version_ids) but should re-check all originally allowed items". K3 0: none. K4 0 (scenario touched, conclusion inverted): "a sentence citing hit 1 and hit 20 is kept but hit 20 dropped → sentence loses citation and is dropped … (already done)". K5 0: "The 0.5s margin is tight for network variance." K6 0: none.

**B-2** — K1 0: none. K2 0 (contradicted): "`_recheck` uses `ctx` from before the call (captures old grants/token_generation)" — `_recheck` re-reads device + grants (synthesis_service.py:119-135). K3 0: none. K4 0: none. K5 0 (direction reversed): "Client waits 8s, server kills at 6s → client sees timeout". K6 0: "TAU_S … calibrated in test fixture … Keep as is".

## Extra findings, checked against the code (e2f30d8)

**A-1**
1. Prompt "at most 4 short sentences" vs schema `maxItems: 6` / `MAX_SENTENCES = 6` — **valid, trivial** (v1.md:10 vs v1.schema.json:9, synthesis.py:75). "CRITICAL" is unjustified: `render()` caps text at 400 tokens (synthesis_service.py:146-160).
2. Deepseek fallback 7 schema_fail + 3 timeout in 211 calls → should be disqualified "per D-071" — **debatable**: the numbers are right (eval/live/2026-09-24-synthesis/results.json), but the rule claim is wrong: D-071 disables a fallback that *fails* a release gate, and deepseek passed G-LIVE-D. It is a fair policy suggestion.
3. 5.5 s HTTP timeout inside the 6 s cap is "too tight" — **not a defect**: this is the deliberate settle margin (synthesis.py:69-71). The real defect here (backoff 1/2/4/8 s defeats the fallback inside 6 s) was not found.
4. `weak()` synthesizes on every near miss regardless of score — **not a defect**: documented design (synthesis_service.py:8-9, 84-85).
5. `TEXT_MAX_TOKENS` measured on text rather than the envelope → tail hits dropped "unexpectedly" — **not a defect**: `pack()` settles the full envelope and drops tail hits by design (:23-27, :215-233).
6. `_excerpts()` fetches chunk text before the privacy gate — **valid, trivial** (performance only; the hits are already authorized for the caller).

**A-2**
1. → K2 (valid).
2. Re-check ignores the enqueue-time `question` capability set — **FP**: an excerpt co-owned by a project outside `question` is denied before the provider call (privacy.py:135-137) and never shown, and `validate_sentences` accepts only ids that were shown (synthesis.py:175-179).
3. `SchemaFail` does not fall through to the fallback — **not a defect**: documented design (provider.py:10-11; the finding itself offers "or document").
4. `max_tokens=700` vs `TEXT_MAX_TOKENS=400` "discards 300 tokens" — **not a defect**: 700 is a ceiling that includes the JSON and cite arrays (prompts/__init__.py:30), and `render()` drops whole sentences rather than truncating output.
5. `weak()` naming — style. 6. `TAU_S` not configurable — opinion. 7. Runtime bool check duplicates the schema — not a defect.
8. n=62 is small (CI ±0.08) — **weak**: true, but the reported min deltas (+0.161 / +0.194) exceed the CI it cites. Not K6's mechanism.
9. `contract_version` only on `synthesize:true` — **not a defect**: the spec requires byte-identical no-flag output (query.py:4-7).
10. `detach=None` with an open transaction violates D-062 — **FP**: a non-IDLE connection without detach returns `no_detach` (synthesis_service.py:286-288).

**B-1** (line refs are often off)
- **FP**: #1 "LLM runs inside open transaction" when the conn is IDLE (IDLE means no transaction; the API path always has `reconnect` → `no_detach`, :286-288). #2 the precheck should re-check denied ids (denied items never enter the prompt; `sent` is exactly the prompt set, synthesis.py:331-347). #3 `synth.unavailable()` is checked after the excerpts (it is at :279, before `_excerpts` at :281-282). #8 env value is case-sensitive (preflight.py:181 does `.strip().lower()`).
- **Not a defect / self-refuted**: #4 ("The flow is correct"), #6 ("Safe for now"), #7 (K4 area, but it concludes "already done"), #9 (its fix loosens the test further; the assertion is at test_w2e_synthesis.py:228), #10 (opinion).
- **Valid, cosmetic**: #5 the 600-char cap applies after redaction and can cut a `⟦REDACTED:…⟧` placeholder (synthesis.py:182). Nothing leaks.

**B-2**
- **FP**: #1 (same IDLE misreading), #2 (the re-check re-reads the device and grants, :119-135), #3 (`_fit` arithmetic is correct: `omitted` grows by exactly the popped hits, :172-179), #4 (the precheck raises `PrivacyDenied` if any `sent` item is denied, synthesis.py:346-347), #5 (the code uses `.strip().endswith("?")`, preflight.py:184), #6 (`_consistency` rejects answered with empty sentences, synthesis.py:157), #9 (the client timeout is 8 s > the 6 s cap, preflight.py:81 / hlm.py:994).
- **Not a defect**: #7, #8, #10 (INFO, "keep as is").

No run found the extra real defects other models found (llm.env not mounted into the api; the 1/2/4/8 s backoff defeating the fallback within 6 s).

## Method

- **Tree**: `git archive e2f30d8` (no .git; consults end at 43 in that commit) + `git diff ad7ccc2 e2f30d8 > W2E-CHANGE.patch`. The tree hash (sha256 over sorted file hashes) was `c3708de0e92fad1b` for every run, identical before and after each Mode A run. The export is in the session scratch dir.
- **Secret gate before sending**: `gitleaks dir` over the export (no leaks), the repo's pre-commit regex plus nvapi/hf patterns (0 hits), a check that no `.env` secret value occurs anywhere in the export (0), and the same three checks over the final Mode B bundle (clean). No `docs/private/`, `.env` or `deploy/.local/` in the export.
- **Mode A (worked)**: codex-cli 0.155.1, a temp `CODEX_HOME` (no MCP servers, plugins, hooks, AGENTS.md or memories). The key reached only the codex process via `env_key`; `shell_environment_policy.inherit="core"` plus excludes keep it out of the model's shell. Provider:
  `model_provider="openrouter"`, `[model_providers.openrouter] base_url="https://openrouter.ai/api/v1", env_key="OPENROUTER_API_KEY", wire_api="responses"` (codex 0.155 rejects `wire_api="chat"`), `request_max_retries=stream_max_retries=20`, `stream_idle_timeout_ms=600000`, `model_context_window=1000000`, `web_search="disabled"`, `approval_policy="never"`.
  Command: `codex exec --skip-git-repo-check -m nvidia/nemotron-3-ultra-550b-a55b:free -c model_reasoning_effort="high" --json -o out.md - < PROMPT.md`, cwd = export.
  **Deviations**: (1) no `-s read-only`: in codex 0.155 it maps to the built-in `:root = read` profile, i.e. full-disk read (verified: it could read the HLMemo repo). I replaced it with a stricter permission profile, `default_permissions="exportonly"` (`":minimal"=read`, cwd = read, plus the codex binary dirs and /opt/homebrew). The rollout shows `sandbox_policy: read-only` plus a restricted file-system profile, and home, repo, `.env` and `~/.codex` were denied. Residual: codex's `:minimal` hard-codes read/write on /tmp and /private/tmp. (2) The outer-seatbelt alternative with codex's sandbox bypassed was blocked by the harness safety classifier and was not pursued. **Audit**: every executed command (49 in A-1, 67 in A-2) stayed inside its export (0 out-of-tree paths). Codex warned "Model metadata … not found. Defaulting to fallback metadata".
- **Mode B**: the full src/ + tests/ + docs/ plus the patch measures 6.39M o200k tokens (~6.9M Nemotron), which does not fit in 1M. Deterministic exclusions: `*.jsonl/*.tsv/*.csv` (4.72M, of which tests/fixtures/g3/items.jsonl alone is 3.95M), JSON > 50 KB except the W2e fixture, docs/bakeoff/, docs/consults/, src/hlmemo/bench/ and bench tests, tests/deploy/, and tests/fixtures/ except synthesis/. In the patch, the body of the recorded cassette (tests/cassettes/w2e/synthesis.jsonl) was elided. The result was 252 files. The 19 files changed by the patch carry `NNNNN| ` line numbers. PROMPT.md comes verbatim at the end, after a neutral format preamble. **Measured**: 717,432 o200k tokens, and OpenRouter reported **783,651 prompt tokens** (ratio 1.092; a 46k-token calibration request gave 1.087). One streaming chat request with `reasoning.effort="high"`, `max_tokens=16000`, and retry on 429/5xx (none was needed). deploy/ and eval/ files are not in the bundle, so the llm.env finding was unreachable in Mode B.

## Caveats

- n = 2 per mode, on one task. A-1 and A-2 overlapped for about 12 minutes (A-2's wall time may include contention). B-2 hit OpenRouter's prompt cache (777,600 cached tokens; time to first token 6.4 s vs 45.5 s), so its 51 s is not a cold-start number.
- Rate limits and availability: no HTTP 429s. The free endpoint returned NVIDIA 503 "Service temporarily overloaded" often: the first A-1 attempt aborted after codex's default 6 retries (24 s, not scored; retries raised to 20), A-1 had 7 reconnects, A-2 had 15, and the calibration request had 1. About 160 requests were used out of the 1,000/day limit.
- Reasoning effort "high" produced only 286–341 reasoning tokens in Mode B, so the single-shot runs barely reasoned over the 784k-token context.
- ToS: the NVIDIA trial terms allow NVIDIA to log and train on inputs. Only the public e2f30d8 export was sent. Mode A cannot hard-enforce a read-only view limited to the export (/tmp stays readable), so the command audit is the control.

## Verdict

It is not useful as a second reviewer next to astra-low in either mode. It scored 0–1/12 against astra-low's 8/12, gave a MERGE-WITH-FIXES verdict on a known DO-NOT-MERGE change, and Mode B produced 4–7 claims per review that the code contradicts. Mode A is the less bad mode (fewer false positives, found the co-owned-grant half of K2 once) but took 18–22 minutes. The union with astra-low adds nothing, because astra-low already covers K2's co-owned half.
