from aloud.postprocess import process

OPTIONS = {
    "strip_fillers": True,
    "fillers": ["um", "uh", "you know"],
    "capitalize_first": True,
    "collapse_whitespace": True,
    "commands": {"new line": "\n", "new paragraph": "\n\n"},
}


def test_strips_standalone_fillers():
    assert process("um, so uh the thing", OPTIONS) == "So the thing"


def test_leaves_filler_substrings_alone():
    # "um" inside "umbrella" and "uh" inside "uhuru" must survive.
    assert process("the umbrella", OPTIONS) == "The umbrella"


def test_vocabulary_is_not_this_layers_job():
    """Corrections live in aloud.corrections and run before this stage."""
    assert "dictionary" not in OPTIONS
    assert process("ask Claud about wisper", OPTIONS) == "Ask Claud about wisper"


def test_expands_spoken_commands():
    assert process("first new line second", OPTIONS) == "First\nsecond"


def test_longest_command_wins():
    assert process("one new paragraph two", OPTIONS) == "One\n\ntwo"


def test_tightens_punctuation_spacing():
    assert process("hello , world .", OPTIONS) == "Hello, world."


def test_empty_input_is_empty_output():
    assert process("", OPTIONS) == ""


def test_no_options_is_a_no_op_beyond_tidying():
    assert process("  hello   there  ", {}) == "Hello there"
