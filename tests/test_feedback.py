"""Start and stop cues — the only feedback you get while looking at another app."""


from aloud import feedback
from aloud.config import Config


def test_the_defaults_are_not_failure_sounds():
    """"Pop" — a hollow thunk — was the old stop cue and read as an error."""
    config = Config()
    start = config.get("feedback.start_sound")
    stop = config.get("feedback.stop_sound")
    assert start not in feedback.UNSUITABLE_FOR_CUES
    assert stop not in feedback.UNSUITABLE_FOR_CUES
    assert start != stop, "start and stop must be distinguishable"


def test_the_error_sound_is_still_allowed_to_sound_like_an_error():
    assert Config().get("feedback.error_sound") in feedback.UNSUITABLE_FOR_CUES


def test_every_default_is_a_sound_that_ships_with_macos():
    config = Config()
    for key in ("start_sound", "stop_sound", "error_sound"):
        assert config.get(f"feedback.{key}") in feedback.SOUND_DESCRIPTIONS


def test_picker_labels_describe_the_sound():
    assert feedback.describe("Bottle") == "Bottle — soft rising bloop"


def test_an_unknown_sound_falls_back_to_its_bare_name():
    assert feedback.describe("MyCustomChime") == "MyCustomChime"


def test_a_label_round_trips_back_to_its_name():
    for name in feedback.SOUND_DESCRIPTIONS:
        assert feedback.name_from_description(feedback.describe(name)) == name


def test_custom_names_round_trip_too():
    assert feedback.name_from_description("MyCustomChime") == "MyCustomChime"


def test_settings_take_effect_without_a_restart():
    cues = feedback.Feedback({"sounds": True, "start_sound": "Tink"})
    assert cues.start_sound == "Tink"
    cues.update({"sounds": False, "start_sound": "Glass"})
    assert cues.start_sound == "Glass"
    assert cues.enabled is False


def test_muting_stops_playback_without_forgetting_the_choice(monkeypatch):
    played = []
    monkeypatch.setattr(feedback, "play", lambda name: played.append(name) or True)
    cues = feedback.Feedback({"sounds": False, "start_sound": "Glass"})
    cues.recording_started()
    assert played == []
    assert cues.start_sound == "Glass"


def test_each_cue_plays_its_own_sound(monkeypatch):
    played = []
    monkeypatch.setattr(feedback, "play", lambda name: played.append(name) or True)
    cues = feedback.Feedback({"sounds": True, "start_sound": "A", "stop_sound": "B", "error_sound": "C"})
    cues.recording_started()
    cues.recording_stopped()
    cues.error()
    assert played == ["A", "B", "C"]


def test_listing_sounds_never_raises_on_a_machine_without_them(monkeypatch):
    monkeypatch.setattr(feedback, "SOUND_DIRS", (feedback.Path("/nope/not/here"),))
    assert feedback.available_sounds() == []
