"""R4 deploy-test fixtures (no test cases here; imported by the tests/deploy modules).

The R4 template names the Gemini writer profiles (google-gemini38-flash-medium/-high). Those profile
files ship with the code branch (profiles/**), not with the deploy tooling, so a test that runs
install_llm_env.sh uses a WORKSTATION COPY of the repository: deploy/ and profiles/ copied into a
temporary directory, plus a contract-shaped stand-in for each Gemini profile the checkout does not
have yet (``HLM_LLM_API_KEY = "env:GEMINI_API_KEY"``, prices and ``price_valid_until``). The real
profile always wins when it exists. No real key value appears anywhere: keys are built at runtime.
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GEMINI_WRITERS = ("google-gemini38-flash-medium", "google-gemini38-flash-high")
#: built at runtime (no secret-shaped literal in the repository)
GEMINI_KEY = "fake-gm-" + "G4" * 16
GEMINI_KEY_B = "fake-gm-" + "H8" * 16


def gemini_profile(effort: str, price_valid_until: str = "2026-12-31") -> str:
    return (
        "# TEST STAND-IN (tests/deploy/r4_fixtures.py): the contract fields of the R4 writer profile\n"
        'HLM_LLM_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"\n'
        'HLM_LLM_MODEL = "gemini-3.8-flash"\n'
        'HLM_LLM_API_KEY = "env:GEMINI_API_KEY"\n'
        f'extra = {{ reasoning_effort = "{effort}", response_format = {{ type = "json_object" }} }}\n'
        "price_in_per_m = 0.75\n"
        "price_out_per_m = 3.75\n"
        f'price_valid_until = "{price_valid_until}"\n'
        'disabled_tasks = ["risk_judge"]\n'
    )


def workstation_repo(dest: Path) -> Path:
    """A copy of this checkout's deploy/ and profiles/ at ``dest`` (the Gemini stand-ins added
    where the checkout lacks them). Scripts copied there resolve REPO_ROOT to ``dest``."""
    shutil.copytree(
        ROOT / "deploy",
        dest / "deploy",
        ignore=shutil.ignore_patterns("data", ".terraform", ".local", "__pycache__"),
    )
    profiles = dest / "profiles"
    profiles.mkdir(parents=True)
    for path in (ROOT / "profiles").glob("*.toml"):
        shutil.copy2(path, profiles / path.name)
    for name in GEMINI_WRITERS:
        if not (profiles / f"{name}.toml").exists():
            (profiles / f"{name}.toml").write_text(gemini_profile(name.rsplit("-", 1)[-1]))
    return dest


#: the R3 template's settings (805f4cd deploy/llm.env.example, comments dropped): the behaviour-only
#: rollback (`install_llm_env.sh --release-template`) installs it on the R4 code
R3_TEMPLATE = (
    "# R3 llm.env template (test copy of 805f4cd deploy/llm.env.example, settings only)\n"
    "HLM_LIBRARIAN_ENABLED=false\n"
    "HLM_LIBRARIAN_ROLE=observer\n"
    "HLM_LIBRARIAN_CONCURRENCY=3\n"
    "HLM_LIBRARIAN_DB_CONNECTIONS=8\n"
    "HLM_PROFILE=openrouter-gpt6-luna\n"
    "HLM_FALLBACK_PROFILE=openrouter-glm53-flash\n"
    "HLM_FALLBACK_PROFILE__SYNTHESIS=openrouter\n"
    "HLM_FALLBACK_PROFILE__QUERY_REWRITE=openrouter\n"
    "HLM_FALLBACK_PROFILE__RISK_JUDGE=openrouter-qwen38-27b-fast\n"
    "OPENROUTER_API_KEY=\n"
    "HLM_ENV_RELEASE=r3\n"
    "HLM_LLM_MODE=live\n"
    "HLM_LLM_TIMEOUT_S=60\n"
    "HLM_LLM_BUDGET_HOUR_USD=1\n"
    "HLM_LLM_BUDGET_DAY_USD=2\n"
    "HLM_LLM_BUDGET_MONTH_USD=10\n"
    "HLM_LLM_JOB_CALL_CAP=20\n"
    "HLM_LLM_BUDGET_DISABLED=false\n"
    "HLM_LLM_REDACT_EMAIL=false\n"
    "HLM_LLM_REDACT_PHONE=false\n"
)


def dotenv(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.startswith("#"):
            out[key.strip()] = value
    return out
