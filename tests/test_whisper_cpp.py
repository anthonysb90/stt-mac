from aloud.engines.whisper_cpp import clean_output


def test_strips_timestamps():
    raw = "[00:00:00.000 --> 00:00:02.000]  Hello there.\n"
    assert clean_output(raw) == "Hello there."


def test_joins_segments_into_one_line():
    raw = "[00:00:00.000 --> 00:00:01.000]  First part.\n" \
          "[00:00:01.000 --> 00:00:02.000]  Second part.\n"
    assert clean_output(raw) == "First part. Second part."


def test_drops_non_speech_annotations():
    assert clean_output("(BLANK_AUDIO)") == ""
    assert clean_output("[MUSIC] Hello") == "Hello"


def test_blank_output_stays_blank():
    assert clean_output("\n  \n") == ""
