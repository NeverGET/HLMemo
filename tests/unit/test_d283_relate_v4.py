"""D-283: relate/v4 is relate/v3 plus one block (the D-282 test), opt-in, with the same answer shape."""

from hlmemo.librarian.prompts import OPT_IN_VERSIONS, load_task, versions

BLOCK_START = '- THE TEST for "contradicts": after the other item, is the replaced statement FALSE as written'
ANCHOR = "- HISTORY is never contradicted:"


def test_relate_v4_is_opt_in_and_changes_no_default() -> None:
    assert 4 in versions("relate") and 4 in OPT_IN_VERSIONS["relate"]
    default = max(set(versions("relate")) - OPT_IN_VERSIONS["relate"])  # the newest non-opt-in version
    assert default < 4 and load_task("relate").prompt_version == f"v{default}"
    assert load_task("relate", 3).prompt_version == "v3"
    v4 = load_task("relate", 4)
    assert v4.prompt_version == "v4" and v4.max_tokens == 12000


def test_relate_v4_is_v3_plus_one_block_before_the_history_rule() -> None:
    v3, v4 = load_task("relate", 3), load_task("relate", 4)
    start = v4.system.index(BLOCK_START)
    end = v4.system.index(ANCHOR)
    assert start < end
    assert v4.system[:start] + v4.system[end:] == v3.system  # nothing else moved or changed
    block = v4.system[start:end]
    for phrase in (
        "NOT IN EFFECT",
        "not built, not merged, on a branch, not deployed, postponed or parked",
        "still true but less precise or less complete",
        "is in effect as soon as it is decided",
        "A statement of intent",
    ):
        assert phrase in block


def test_relate_v4_keeps_the_answer_shape() -> None:
    assert load_task("relate", 4).schema == load_task("relate", 3).schema  # the guards apply unchanged
