"""Design tokens — the single source of truth for how Aloud looks.

Every view pulls from here. No component defines its own colour, size, radius,
duration, or spacing value; if something is needed that this file does not
have, the token is added here first.

Why tokens rather than styling each view: an AppKit app has no stylesheet, so
without a deliberate system the values scatter across a dozen controllers and
light/dark parity rots immediately. Naming them once makes the design
reviewable as a design, and makes "tighten every gap by 2pt" a one-line change.

Structure
---------
* Colours are **semantic**, not literal: views ask for ``TEXT_SECONDARY``,
  never ``#5C5C6B``. Each carries a light and a dark value so appearance
  switching is automatic.
* Everything else is a scale with named steps. Views compose from the scale
  rather than inventing intermediate values.

This module deliberately imports no AppKit at module scope so the tokens stay
testable and dumpable off a Mac. :func:`ns_color` and :func:`ns_font` bridge to
the real objects lazily.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Dict, Tuple

# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Color:
    """A semantic colour with a value for each appearance.

    ``alpha`` applies to both. Keeping it separate from the hex means a token
    like ``BG_SELECTION`` can be "the brand colour at 12%" and stay correct if
    the brand colour changes.
    """

    light: str
    dark: str
    alpha: float = 1.0
    description: str = ""

    def for_appearance(self, dark: bool) -> str:
        return self.dark if dark else self.light

    def rgba(self, dark: bool) -> Tuple[float, float, float, float]:
        hex_value = self.for_appearance(dark).lstrip("#")
        r, g, b = (int(hex_value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
        return r, g, b, self.alpha


# -- brand ------------------------------------------------------------------
# Carried over from the app mark: an indigo-to-violet gradient. Dark-mode
# variants are lifted and desaturated, because the same indigo on a dark
# surface reads as muddy and fails contrast against secondary text.

BRAND_PRIMARY = Color("#4C3AE0", "#8A7BFF", description="Primary action, focus, active state")
BRAND_SECONDARY = Color("#8B3FD9", "#B98BF0", description="Gradient partner, transcribing state")
BRAND_GRADIENT_START = Color("#4C3AE0", "#6E5CF5", description="Mark and hero gradient, top")
BRAND_GRADIENT_END = Color("#8B3FD9", "#A96BE8", description="Mark and hero gradient, bottom")

# -- surfaces ---------------------------------------------------------------
# Four levels only. More than four and the hierarchy stops reading.

BG_WINDOW = Color("#F6F6F9", "#1B1B20", description="Window background, behind everything")
BG_SURFACE = Color("#FFFFFF", "#25252B", description="Cards, list rows, panels")
BG_RAISED = Color("#FFFFFF", "#2E2E36", description="Popovers, menus, hovered rows")
BG_INSET = Color("#EDEDF2", "#161619", description="Search fields, code blocks, wells")
BG_SELECTION = Color("#4C3AE0", "#8A7BFF", alpha=0.14, description="Selected row fill")
BG_HOVER = Color("#000000", "#FFFFFF", alpha=0.04, description="Hover wash, appearance-neutral")
BG_SCRIM = Color("#000000", "#000000", alpha=0.32, description="Behind modal sheets")

# -- content ----------------------------------------------------------------
# Three text weights is the whole hierarchy. Primary for content, secondary for
# labels and metadata, tertiary for text that is present but not to be read.

TEXT_PRIMARY = Color("#17171C", "#F2F2F6", description="Transcripts, entry text, titles")
TEXT_SECONDARY = Color("#585868", "#A2A2B2", description="Labels, timestamps, counts")
TEXT_TERTIARY = Color("#8A8A9B", "#6E6E7E", description="Placeholders, disabled, hints")
TEXT_ON_ACCENT = Color("#FFFFFF", "#FFFFFF", description="Text on a brand-filled surface")
TEXT_ACCENT = Color("#4438C9", "#9C90FF", description="Links and accented labels")
TEXT_INVERSE = Color("#FFFFFF", "#17171C", description="Text on an inverted surface")

# -- lines ------------------------------------------------------------------

BORDER_SUBTLE = Color("#E4E4EC", "#33333C", description="Row separators, card edges")
BORDER_DEFAULT = Color("#D3D3DE", "#3E3E49", description="Field and control outlines")
BORDER_STRONG = Color("#B4B4C4", "#5C5C6B", description="Emphasised outlines, dividers")
BORDER_FOCUS = Color("#4C3AE0", "#8A7BFF", description="Focus ring")

# -- status -----------------------------------------------------------------
# These are used as *text* — "this entry looks risky", "delivery failed" — so
# the light values are set to clear 4.5:1 on BG_WINDOW rather than to be as
# saturated as possible. Verified by tests/test_tokens.py.

STATUS_IDLE = Color("#8A8A9B", "#6E6E7E", description="Not recording")
STATUS_RECORDING = Color("#C52328", "#FF6167", description="Microphone is live")
STATUS_TRANSCRIBING = Color("#7A2FC4", "#B98BF0", description="Model is working")
STATUS_SUCCESS = Color("#177340", "#42CC85", description="Delivered, saved, valid")
STATUS_WARNING = Color("#945E00", "#FFC148", description="Risky dictionary entry")
STATUS_ERROR = Color("#C52328", "#FF6167", description="Failure that needs attention")

#: The one colour permitted to be loud. Fills only — the recording dot and the
#: menu bar glyph — where the 3:1 non-text threshold applies, not 4.5:1.
STATUS_RECORDING_FILL = Color("#E0393E", "#FF6167", description="Live-microphone dot and meter")

# -- level meter ------------------------------------------------------------
# A three-stop ramp rather than a single colour: the point of a meter is to
# show headroom, which a monochrome bar cannot do.

METER_TRACK = Color("#E4E4EC", "#2E2E36", description="Unfilled meter")
METER_LOW = Color("#42A05F", "#4FBF72", description="Healthy speaking level")
METER_MID = Color("#C79014", "#FFC148", description="Loud but usable")
METER_PEAK = Color("#E0393E", "#FF6167", description="Clipping")

#: Meter stops are fills, so they follow the 3:1 rule and stay saturated.
METER_STOPS = ((0.00, "METER_LOW"), (0.70, "METER_MID"), (0.92, "METER_PEAK"))

# -- correction highlighting ------------------------------------------------
# Used in history to show what the dictionary changed. Deliberately a wash
# rather than a border, so a transcript with six corrections is still readable.

HIGHLIGHT_CORRECTION = Color("#B87503", "#FFC148", alpha=0.20, description="Corrected span")
HIGHLIGHT_MATCH = Color("#4C3AE0", "#8A7BFF", alpha=0.22, description="Search hit")

COLORS: Dict[str, Color] = {
    name: value
    for name, value in list(globals().items())
    if isinstance(value, Color)
}


# ---------------------------------------------------------------------------
# Type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextStyle:
    """One step of the type scale.

    ``size`` and ``tracking`` are in points; ``line_height`` is a multiplier of
    the size. ``weight`` uses the AppKit names so the bridge is unambiguous.
    """

    size: float
    weight: str  # regular · medium · semibold · bold
    line_height: float = 1.35
    tracking: float = 0.0
    mono: bool = False
    uppercase: bool = False
    description: str = ""


# The scale is deliberately short and macOS-native: 13pt is the system control
# size, and everything else is a ratio away from it. A scale with eleven steps
# is a scale nobody follows.

TYPE_DISPLAY = TextStyle(28, "semibold", 1.15, -0.4, description="Empty-state headline")
TYPE_TITLE_1 = TextStyle(22, "semibold", 1.20, -0.3, description="Window / section title")
TYPE_TITLE_2 = TextStyle(17, "semibold", 1.25, -0.2, description="Panel heading")
TYPE_TITLE_3 = TextStyle(15, "semibold", 1.30, description="Group heading, row title")
TYPE_BODY = TextStyle(13, "regular", 1.40, description="Default control and content size")
TYPE_BODY_STRONG = TextStyle(13, "medium", 1.40, description="Emphasised body, entry term")
TYPE_TRANSCRIPT = TextStyle(14, "regular", 1.55, description="Transcript text — set larger and looser than body because it is read, not scanned")
TYPE_CALLOUT = TextStyle(12, "regular", 1.35, description="Secondary metadata")
TYPE_CAPTION = TextStyle(11, "regular", 1.30, description="Timestamps, counts, hints")
TYPE_LABEL = TextStyle(10, "medium", 1.20, 0.5, uppercase=True, description="Section eyebrow")
TYPE_MONO = TextStyle(12, "regular", 1.45, mono=True, description="Patterns, file paths")

TYPE_SCALE: Dict[str, TextStyle] = {
    name: value for name, value in list(globals().items()) if isinstance(value, TextStyle)
}


# ---------------------------------------------------------------------------
# Spacing
# ---------------------------------------------------------------------------

#: A 4pt grid, with 2pt and 6pt half-steps below 8 for optical adjustment
#: inside controls. Every margin, gap, and inset in the app is one of these.
SPACE: Dict[str, float] = {
    "none": 0,
    "hair": 2,
    "xs": 4,
    "sm": 6,
    "md": 8,
    "lg": 12,
    "xl": 16,
    "2xl": 20,
    "3xl": 24,
    "4xl": 32,
    "5xl": 40,
    "6xl": 48,
}

#: Named compositions, so "the padding inside a card" is one decision made once.
INSET: Dict[str, float] = {
    "control": SPACE["md"],      # inside a button or field
    "row": SPACE["lg"],          # inside a list row
    "card": SPACE["xl"],         # inside a card or panel
    "window": SPACE["3xl"],      # window edge margin
    "section": SPACE["4xl"],     # between major sections
}


# ---------------------------------------------------------------------------
# Radius, border, shadow
# ---------------------------------------------------------------------------

RADIUS: Dict[str, float] = {
    "none": 0,
    "sm": 4,     # tags, chips, meter bar
    "md": 6,     # buttons, text fields — matches the macOS control radius
    "lg": 10,    # cards, list rows
    "xl": 14,    # sheets, the window itself
    "pill": 999, # status pills, segmented toggles
}

BORDER: Dict[str, float] = {
    "none": 0,
    "hairline": 1,   # separators
    "default": 1,    # control outlines
    "strong": 1.5,   # emphasised outline
    "focus": 2,      # focus ring, drawn outside the control bounds
}


@dataclass(frozen=True)
class Shadow:
    """A single elevation step. macOS is restrained here; three levels is plenty."""

    y_offset: float
    blur: float
    light_alpha: float
    dark_alpha: float
    description: str = ""

    def alpha(self, dark: bool) -> float:
        return self.dark_alpha if dark else self.light_alpha


SHADOW: Dict[str, Shadow] = {
    "none": Shadow(0, 0, 0.0, 0.0, "Flat — the default for anything in flow"),
    "raised": Shadow(1, 3, 0.08, 0.34, "Cards and rows lifted off the window"),
    "overlay": Shadow(4, 16, 0.14, 0.46, "Popovers, the dictionary editor sheet"),
    "modal": Shadow(12, 40, 0.20, 0.58, "Sheets over a scrim"),
}


# ---------------------------------------------------------------------------
# Motion
# ---------------------------------------------------------------------------

#: Durations in seconds. Anything over `slow` on a desktop app feels broken.
DURATION: Dict[str, float] = {
    "instant": 0.0,
    "fast": 0.12,    # hover, press, checkbox
    "base": 0.20,    # panel swap, row insert, sheet present
    "slow": 0.32,    # window-scale transitions only
}

#: Cubic-bezier control points, matching the CAMediaTimingFunction constructor.
EASING: Dict[str, Tuple[float, float, float, float]] = {
    "standard": (0.2, 0.0, 0.0, 1.0),    # default for anything moving on screen
    "decelerate": (0.0, 0.0, 0.2, 1.0),  # entering
    "accelerate": (0.4, 0.0, 1.0, 1.0),  # leaving
    "linear": (0.0, 0.0, 1.0, 1.0),      # the level meter only
}

#: The meter is asymmetric on purpose: it has to jump to a new peak immediately
#: to feel connected to your voice, but fall slowly or it strobes at 60fps.
METER_ATTACK_SECONDS = 0.04
METER_RELEASE_SECONDS = 0.18
METER_REFRESH_HZ = 30

#: How long the peak marker sits before it starts sliding back, and how fast it
#: falls once it does. A clip you glanced away from should still be visible.
METER_PEAK_HOLD_SECONDS = 1.2
METER_PEAK_FALL_RATE = 0.6
#: Below this the meter reads as silent and draws nothing.
METER_SILENCE_FLOOR = 0.001
#: The peak marker is only drawn when it is clear of the bar itself.
METER_PEAK_GAP = 0.02

#: Views must check this before animating; macOS exposes it as
#: NSWorkspace.accessibilityDisplayShouldReduceMotion.
REDUCE_MOTION_FALLBACK_DURATION = DURATION["instant"]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

#: Sizes that are neither spacing nor type, but still must not be invented
#: per-view: control heights, row heights, and the window's own geometry.
METRIC: Dict[str, float] = {
    "control_height": 22,
    "control_height_large": 28,
    "field_height": 24,
    "row_height_compact": 28,
    "row_height": 40,
    "row_height_transcript": 64,
    "sidebar_width": 196,
    "sidebar_width_min": 160,
    "sidebar_width_max": 280,
    "detail_width_min": 420,
    "window_width": 940,
    "window_height": 620,
    "window_width_min": 720,
    "window_height_min": 440,
    "settings_width": 560,
    "settings_height": 420,
    "meter_height": 6,
    "meter_height_large": 10,
    "meter_width_min": 200,
    "meter_readout_width": 56,
    "state_pill_width": 104,
    "form_label_width": 96,
    "field_width_min": 200,
    "search_width_min": 220,
    "icon_sm": 12,
    "icon_md": 16,
    "icon_lg": 20,
}


# ---------------------------------------------------------------------------
# Bridges and export
# ---------------------------------------------------------------------------

_WEIGHTS = {"regular": 0.0, "medium": 0.23, "semibold": 0.3, "bold": 0.4}


def ns_color(token: Color):
    """Bridge a token to a dynamic NSColor that follows the system appearance."""
    import AppKit

    def provider(appearance) -> "AppKit.NSColor":
        names = [AppKit.NSAppearanceNameDarkAqua, AppKit.NSAppearanceNameVibrantDark]
        is_dark = appearance.bestMatchFromAppearancesWithNames_(
            [AppKit.NSAppearanceNameAqua] + names
        ) in names
        r, g, b, a = token.rgba(is_dark)
        return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)

    return AppKit.NSColor.colorWithName_dynamicProvider_(None, provider)


def ns_font(style: TextStyle):
    """Bridge a type-scale step to an NSFont."""
    import AppKit

    weight = _WEIGHTS.get(style.weight, 0.0)
    if style.mono:
        return AppKit.NSFont.monospacedSystemFontOfSize_weight_(style.size, weight)
    return AppKit.NSFont.systemFontOfSize_weight_(style.size, weight)


def ns_shadow(style: Shadow, dark: bool):
    """Bridge a shadow token to an NSShadow.

    Shadow colour is the one value that cannot be drawn lazily -- AppKit bakes
    it when the shadow is set -- so it is derived here, and views re-derive it
    when the appearance changes.
    """
    import AppKit

    if style.blur == 0:
        return None
    shadow = AppKit.NSShadow.alloc().init()
    shadow.setShadowOffset_((0, -style.y_offset))
    shadow.setShadowBlurRadius_(style.blur)
    shadow.setShadowColor_(
        AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0, 0, 0, style.alpha(dark))
    )
    return shadow


def is_dark(view) -> bool:
    """Whether a view is currently rendering in a dark appearance."""
    import AppKit

    names = [AppKit.NSAppearanceNameDarkAqua, AppKit.NSAppearanceNameVibrantDark]
    match = view.effectiveAppearance().bestMatchFromAppearancesWithNames_(
        [AppKit.NSAppearanceNameAqua] + names
    )
    return match in names


def as_dict() -> dict:
    """The whole system as plain data, for docs and for diffing a redesign."""
    return {
        "color": {name: asdict(value) for name, value in sorted(COLORS.items())},
        "type": {name: asdict(value) for name, value in sorted(TYPE_SCALE.items())},
        "space": SPACE,
        "inset": INSET,
        "radius": RADIUS,
        "border": BORDER,
        "shadow": {name: asdict(value) for name, value in SHADOW.items()},
        "duration": DURATION,
        "easing": {name: list(value) for name, value in EASING.items()},
        "metric": METRIC,
        "meter": {
            "attack_seconds": METER_ATTACK_SECONDS,
            "release_seconds": METER_RELEASE_SECONDS,
            "refresh_hz": METER_REFRESH_HZ,
        },
    }


def as_json(indent: int = 2) -> str:
    return json.dumps(as_dict(), indent=indent, sort_keys=False)
