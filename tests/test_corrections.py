"""The correction pass — the part that must not corrupt ordinary text."""

import pytest

from aloud.corrections import (
    RISK_CAUTION,
    RISK_OK,
    RISK_RISKY,
    Ruleset,
    analyse,
    bias_prompt,
    normalise,
    pattern_for,
)
from aloud.dictionary import CORRECTION, TERM, Entry


def rules(*entries):
    return Ruleset(list(entries))


def correction(heard, write, **kwargs):
    return Entry(kind=CORRECTION, heard=heard, write=write, **kwargs)


def term(text, **kwargs):
    return Entry(kind=TERM, term=text, **kwargs)


# -- the glued / hyphenated forms -------------------------------------------


@pytest.mark.parametrize(
    "heard_as",
    ["cloud code", "Cloud Code", "CLOUD CODE", "cloud-code", "Cloud-Code",
     "cloud_code", "CloudCode", "cloudcode", "cloud   code"],
)
def test_a_correction_catches_every_spacing_the_model_invents(heard_as):
    result = rules(correction("cloud code", "Claude Code")).apply(f"open {heard_as} now")
    assert result.text == "open Claude Code now"


def test_a_term_canonicalises_its_own_form():
    result = rules(term("Supabase")).apply("we use supabase and Supa-base")
    assert result.text == "we use Supabase and Supabase"


@pytest.mark.parametrize(
    "heard_as", ["Supabase", "supabase", "Supa base", "Supa-base", "supa  base", "Su pa base"]
)
def test_a_long_term_is_caught_however_the_model_splits_it(heard_as):
    assert rules(term("Supabase")).apply(f"on {heard_as} today").text == "on Supabase today"


def test_a_short_word_is_not_matched_across_gaps():
    """Split tolerance is only safe for words distinctive enough to survive it."""
    ruleset = rules(term("Bun"))
    assert ruleset.apply("b u n").text == "b u n"
    assert ruleset.apply("bun").text == "Bun"


def test_split_tolerance_still_respects_word_boundaries():
    assert rules(term("Supabase")).apply("Supa basement").text == "Supa basement"


def test_the_replacement_is_written_exactly_as_typed():
    result = rules(correction("np m", "npm")).apply("Run NPM install")
    assert result.text == "Run npm install"


# -- not corrupting real words ----------------------------------------------


def test_a_two_word_pattern_never_touches_an_unrelated_word():
    """The exact case from the brief: Cloudflare and plain 'cloud' must survive."""
    ruleset = rules(correction("cloud code", "Claude Code"))
    text = "Cloudflare fronts the cloud, and clouds gather. Try cloud code."
    result = ruleset.apply(text)
    assert result.text == "Cloudflare fronts the cloud, and clouds gather. Try Claude Code."
    assert len(result.applied) == 1


@pytest.mark.parametrize("surrounding", ["barcode", "codes", "encode", "code's"])
def test_a_single_word_pattern_respects_word_boundaries(surrounding):
    result = rules(correction("code", "Code")).apply(f"a {surrounding} here")
    assert result.text == f"a {surrounding} here"


def test_possessives_are_left_alone():
    result = rules(term("Claude")).apply("claude's idea")
    assert result.text == "claude's idea"


# -- longest match first -----------------------------------------------------


def test_the_longer_pattern_wins_at_the_same_position():
    ruleset = rules(
        correction("cloud code", "Claude Code"),
        correction("cloud code sdk", "Claude Code SDK"),
    )
    assert ruleset.apply("use cloud code sdk").text == "use Claude Code SDK"


def test_order_of_entry_does_not_change_the_outcome():
    forwards = rules(correction("cloud code sdk", "Claude Code SDK"), correction("cloud code", "Claude Code"))
    backwards = rules(correction("cloud code", "Claude Code"), correction("cloud code sdk", "Claude Code SDK"))
    assert forwards.apply("cloud code sdk").text == backwards.apply("cloud code sdk").text


# -- single pass -------------------------------------------------------------


def test_a_rule_cannot_match_text_another_rule_just_produced():
    """Without a single pass, this would chain to "Claude Code" and then beyond."""
    ruleset = rules(
        correction("cloud code", "Claude Code"),
        correction("claude code", "SOMETHING ELSE"),
    )
    result = ruleset.apply("cloud code")
    assert result.text == "Claude Code"
    assert len(result.applied) == 1


def test_offsets_refer_to_the_original_transcript():
    result = rules(correction("cloud code", "Claude Code")).apply("try cloud code twice, cloud code")
    assert [a.start for a in result.applied] == [4, 22]
    assert all(result.original[a.start : a.end] == a.matched for a in result.applied)


# -- reporting ---------------------------------------------------------------


def test_nothing_is_reported_when_the_text_is_already_correct():
    result = rules(term("Supabase")).apply("Supabase is fine")
    assert result.text == "Supabase is fine"
    assert not result.changed
    assert result.summary() == ""


def test_a_single_correction_is_summarised_in_full():
    result = rules(correction("cloud code", "Claude Code")).apply("cloud code")
    assert result.summary() == "cloud code → Claude Code"
    assert result.applied[0].to_dict()["from"] == "cloud code"


def test_several_corrections_are_summarised_by_count():
    ruleset = rules(correction("cloud code", "Claude Code"), term("Supabase"))
    result = ruleset.apply("cloud code and supabase")
    assert result.summary() == "2 corrections"


def test_disabled_entries_do_not_fire():
    result = rules(correction("cloud code", "Claude Code", enabled=False)).apply("cloud code")
    assert result.text == "cloud code"


def test_an_empty_ruleset_is_a_no_op():
    ruleset = rules()
    assert ruleset.empty
    assert ruleset.apply("anything at all").text == "anything at all"


def test_duplicate_patterns_collapse_to_one_rule():
    ruleset = rules(correction("cloud code", "A"), correction("Cloud-Code", "B"))
    assert len(ruleset) == 1


# -- risk analysis -----------------------------------------------------------


def test_an_everyday_single_word_is_flagged_as_risky():
    risk = analyse(correction("cloud", "Claude"))
    assert risk.level == RISK_RISKY
    assert "everyday word" in risk.headline


def test_a_pattern_that_collapses_to_an_everyday_word_is_flagged():
    """'in put' matches 'input' because the separator may be empty."""
    risk = analyse(correction("in put", "input"))
    assert risk.level == RISK_RISKY
    assert "input" in risk.headline


def test_a_distinctive_multi_word_pattern_is_fine():
    assert analyse(correction("cloud code", "Claude Code")).level == RISK_OK


def test_a_distinctive_single_word_is_fine():
    assert analyse(term("Supabase")).level == RISK_OK
    assert analyse(term("Anthropic")).level == RISK_OK


def test_a_very_short_pattern_is_flagged():
    assert analyse(term("AI")).level == RISK_RISKY


def test_a_correction_that_only_fixes_spacing_suggests_a_term_instead():
    risk = analyse(correction("supa base", "Supabase"))
    assert risk.level == RISK_CAUTION
    assert "term entry" in risk.headline


def test_a_clashing_entry_is_flagged_against_its_neighbours():
    first = correction("cloud code", "Claude Code")
    second = correction("Cloud-Code", "Something Else")
    risk = analyse(second, others=[first])
    assert risk.level == RISK_CAUTION
    assert "Another entry" in risk.headline


def test_an_empty_pattern_is_rejected_rather_than_analysed():
    empty = term("x")
    empty.term = "   "
    assert analyse(empty).level == RISK_RISKY


# -- helpers -----------------------------------------------------------------


def test_normalise_strips_case_and_separators():
    assert normalise("  Claude-Code ") == normalise("claude code") == "claudecode"


def test_pattern_for_rejects_an_empty_phrase():
    with pytest.raises(ValueError):
        pattern_for("   ")


def test_bias_prompt_reads_as_a_sentence():
    assert bias_prompt(["Anthropic", "Vercel"]) == "Glossary: Anthropic, Vercel."
    assert bias_prompt([]) == ""
    assert bias_prompt(["  ", ""]) == ""
