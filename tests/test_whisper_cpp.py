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


# -- the timeout has to fit the audio ----------------------------------------


def _engine(**options):
    from aloud.engines.whisper_cpp import WhisperCppEngine

    return WhisperCppEngine(options)


def _wav_seconds(path, seconds):
    import wave

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


def test_timeout_scales_with_long_audio(tmp_path):
    """A flat 120s killed hour-long imports that were progressing normally."""
    hour = _wav_seconds(tmp_path / "hour.wav", 3600)
    assert _engine()._timeout_for(hour) == 3600 * 4.0


def test_timeout_keeps_a_floor_for_short_dictation(tmp_path):
    blip = _wav_seconds(tmp_path / "blip.wav", 3)
    assert _engine()._timeout_for(blip) == 120.0


def test_an_explicit_timeout_still_wins(tmp_path):
    hour = _wav_seconds(tmp_path / "hour.wav", 3600)
    assert _engine(timeout=42)._timeout_for(hour) == 42.0


def test_an_unreadable_file_falls_back_to_the_floor(tmp_path):
    missing = tmp_path / "nowhere.wav"
    assert _engine()._timeout_for(missing) == 120.0
