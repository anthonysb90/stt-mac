"""The design system's own guard rails.

Tokens are only worth having if they stay coherent, so the rules that make them
a system — contrast, appearance parity, no stray values — are asserted rather
than left to review.
"""

import pytest

from aloud.ui import tokens as T


def _relative_luminance(hex_value: str) -> float:
    hex_value = hex_value.lstrip("#")

    def channel(component: str) -> float:
        value = int(component, 16) / 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(hex_value[i : i + 2]) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(foreground: str, background: str) -> float:
    lighter = max(_relative_luminance(foreground), _relative_luminance(background))
    darker = min(_relative_luminance(foreground), _relative_luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


#: Tokens that are rendered as text and therefore owe WCAG AA (4.5:1).
TEXT_TOKENS = [
    "TEXT_PRIMARY",
    "TEXT_SECONDARY",
    "TEXT_ACCENT",
    "STATUS_RECORDING",
    "STATUS_TRANSCRIBING",
    "STATUS_SUCCESS",
    "STATUS_WARNING",
    "STATUS_ERROR",
]

BACKGROUNDS = ["BG_WINDOW", "BG_SURFACE"]


@pytest.mark.parametrize("token_name", TEXT_TOKENS)
@pytest.mark.parametrize("background_name", BACKGROUNDS)
@pytest.mark.parametrize("dark", [False, True])
def test_text_tokens_meet_wcag_aa(token_name, background_name, dark):
    token = getattr(T, token_name)
    background = getattr(T, background_name)
    measured = contrast(token.for_appearance(dark), background.for_appearance(dark))
    assert measured >= 4.5, (
        f"{token_name} on {background_name} "
        f"({'dark' if dark else 'light'}) is {measured:.2f}:1"
    )


@pytest.mark.parametrize("token_name", ["TEXT_TERTIARY", "STATUS_IDLE"])
@pytest.mark.parametrize("dark", [False, True])
def test_dim_text_tokens_meet_the_three_to_one_floor(token_name, dark):
    """Placeholders are exempt from 4.5:1 but not from being visible at all."""
    token = getattr(T, token_name)
    measured = contrast(token.for_appearance(dark), T.BG_WINDOW.for_appearance(dark))
    assert measured >= 3.0, f"{token_name} is {measured:.2f}:1"


@pytest.mark.parametrize("dark", [False, True])
def test_border_weights_ascend_in_contrast(dark):
    """Separators are deliberately faint — what matters is that the three
    weights are actually distinguishable from each other and from the surface."""
    background = T.BG_SURFACE.for_appearance(dark)
    subtle, default, strong = (
        contrast(getattr(T, name).for_appearance(dark), background)
        for name in ("BORDER_SUBTLE", "BORDER_DEFAULT", "BORDER_STRONG")
    )
    assert 1.0 < subtle < default < strong
    assert strong >= 2.0, f"BORDER_STRONG is only {strong:.2f}:1 — invisible"


def test_every_colour_defines_both_appearances():
    for name, color in T.COLORS.items():
        assert color.light.startswith("#") and len(color.light) == 7, name
        assert color.dark.startswith("#") and len(color.dark) == 7, name
        assert 0.0 <= color.alpha <= 1.0, name


def test_every_colour_is_documented():
    """A token nobody can explain is a token that will be misused."""
    for name, color in T.COLORS.items():
        assert color.description, f"{name} has no description"


def test_type_scale_is_strictly_ordered():
    sizes = [
        T.TYPE_DISPLAY.size, T.TYPE_TITLE_1.size, T.TYPE_TITLE_2.size,
        T.TYPE_TITLE_3.size, T.TYPE_BODY.size, T.TYPE_CALLOUT.size,
        T.TYPE_CAPTION.size, T.TYPE_LABEL.size,
    ]
    assert sizes == sorted(sizes, reverse=True)


def test_transcript_text_is_larger_and_looser_than_body():
    """Transcripts are read, not scanned — the scale should say so."""
    assert T.TYPE_TRANSCRIPT.size > T.TYPE_BODY.size
    assert T.TYPE_TRANSCRIPT.line_height > T.TYPE_BODY.line_height


def test_spacing_scale_is_ascending_and_on_grid():
    values = list(T.SPACE.values())
    assert values == sorted(values)
    # Half-steps (2, 6) exist below 8 for optical adjustment inside controls;
    # from 8 up, everything sits on the 4pt grid.
    assert all(value % 2 == 0 for value in values)
    assert all(value % 4 == 0 for value in values if value >= 8)


def test_insets_are_drawn_from_the_spacing_scale():
    """No component may invent a padding value."""
    allowed = set(T.SPACE.values())
    assert set(T.INSET.values()) <= allowed


def test_radius_and_border_scales_ascend():
    assert list(T.RADIUS.values()) == sorted(T.RADIUS.values())
    assert list(T.BORDER.values()) == sorted(T.BORDER.values())


def test_shadows_get_heavier_with_elevation():
    order = ["none", "raised", "overlay", "modal"]
    for lighter, heavier in zip(order, order[1:]):
        assert T.SHADOW[heavier].blur > T.SHADOW[lighter].blur
        assert T.SHADOW[heavier].light_alpha >= T.SHADOW[lighter].light_alpha
        # Dark mode needs more shadow to read at all.
        assert T.SHADOW[heavier].dark_alpha > T.SHADOW[heavier].light_alpha


def test_durations_ascend_and_stay_under_a_third_of_a_second():
    values = list(T.DURATION.values())
    assert values == sorted(values)
    assert max(values) <= 0.35


def test_meter_falls_slower_than_it_rises():
    """Asymmetric ballistics: instant attack feels connected, slow release stops strobing."""
    assert T.METER_ATTACK_SECONDS < T.METER_RELEASE_SECONDS


def test_tokens_serialise_to_plain_data():
    payload = T.as_dict()
    assert set(payload) == {
        "color", "type", "space", "inset", "radius",
        "border", "shadow", "duration", "easing", "metric", "meter",
    }
    assert T.as_json().startswith("{")


def test_rgba_conversion_carries_alpha():
    r, g, b, a = T.BG_SELECTION.rgba(dark=False)
    assert a == pytest.approx(0.14)
    assert all(0.0 <= channel <= 1.0 for channel in (r, g, b))
