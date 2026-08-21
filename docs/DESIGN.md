# Aloud — design system

The canonical definition is [`src/aloud/ui/tokens.py`](../src/aloud/ui/tokens.py).
This document explains the reasoning; the code is the source of truth, and
`tests/test_tokens.py` enforces the rules below. `make tokens` dumps the whole
system to `docs/tokens.json` if you want to diff a redesign.

## The rule

**Every view pulls from the tokens. No component defines its own value.** If a
view needs something the system does not have, the token is added here first.

An AppKit app has no stylesheet. Without a deliberate system the values scatter
across a dozen controllers and light/dark parity rots within a week. Naming them
once makes the design reviewable *as a design*, and makes "tighten every gap by
2pt" a one-line change instead of an archaeology project.

## Colour

Colours are **semantic, not literal**. A view asks for `TEXT_SECONDARY`, never
`#585868`. Each token carries a light and a dark value, so appearance switching
is automatic and cannot be forgotten.

| Group | Tokens | Notes |
| --- | --- | --- |
| Brand | `BRAND_PRIMARY`, `BRAND_SECONDARY`, `BRAND_GRADIENT_*` | The indigo→violet of the app mark. Dark variants are lifted and desaturated — the same indigo on a dark surface reads muddy and fails contrast. |
| Surface | `BG_WINDOW`, `BG_SURFACE`, `BG_RAISED`, `BG_INSET` | Four levels only. Past four, the hierarchy stops reading. |
| State fills | `BG_SELECTION`, `BG_HOVER`, `BG_SCRIM` | Alpha over the brand or a neutral, so they survive a palette change. |
| Content | `TEXT_PRIMARY`, `TEXT_SECONDARY`, `TEXT_TERTIARY`, `TEXT_ACCENT`, `TEXT_ON_ACCENT`, `TEXT_INVERSE` | Three weights is the whole hierarchy: content, labels, and text that is present but not to be read. |
| Lines | `BORDER_SUBTLE`, `BORDER_DEFAULT`, `BORDER_STRONG`, `BORDER_FOCUS` | Deliberately faint. What matters is that the three weights are distinguishable from each other. |
| Status | `STATUS_IDLE/RECORDING/TRANSCRIBING/SUCCESS/WARNING/ERROR` | These render as **text**, so light values are tuned to clear 4.5:1 rather than to be maximally saturated. |
| Meter | `METER_TRACK/LOW/MID/PEAK` | A three-stop ramp. A monochrome bar cannot show headroom, which is the only reason to have a meter. |
| Highlight | `HIGHLIGHT_CORRECTION`, `HIGHLIGHT_MATCH` | Washes, not borders — a transcript with six corrections must stay readable. |

### Contrast

Enforced by test, in both appearances and against both `BG_WINDOW` and
`BG_SURFACE`:

* Text tokens: **≥ 4.5:1** (WCAG AA).
* Dim text — placeholders, `STATUS_IDLE`: **≥ 3:1**.
* Border weights must ascend in contrast, with `BORDER_STRONG` ≥ 2:1.

One exception is named rather than smuggled: `STATUS_RECORDING_FILL` keeps the
vivid red for the recording dot and the menu bar glyph, where the 3:1 non-text
threshold applies. It is never used for text.

## Type

macOS-native and short. 13pt is the system control size; everything else is a
ratio away from it. A scale with eleven steps is a scale nobody follows.

| Token | Size / weight | Used for |
| --- | --- | --- |
| `TYPE_DISPLAY` | 28 semibold | Empty-state headline |
| `TYPE_TITLE_1` | 22 semibold | Window and section titles |
| `TYPE_TITLE_2` | 17 semibold | Panel headings |
| `TYPE_TITLE_3` | 15 semibold | Group headings, row titles |
| `TYPE_BODY` | 13 regular | Default control and content size |
| `TYPE_BODY_STRONG` | 13 medium | Dictionary term, emphasised body |
| `TYPE_TRANSCRIPT` | 14 regular, 1.55 | **Transcripts only** |
| `TYPE_CALLOUT` | 12 regular | Secondary metadata |
| `TYPE_CAPTION` | 11 regular | Timestamps, counts, hints |
| `TYPE_LABEL` | 10 medium, uppercase | Section eyebrows |
| `TYPE_MONO` | 12 mono | Patterns, file paths |

`TYPE_TRANSCRIPT` is the one deliberate break from the scale: transcripts are
*read*, not scanned, so they are set larger and looser than body text. The test
suite asserts this stays true.

## Spacing

A 4pt grid, with 2pt and 6pt half-steps below 8 for optical adjustment inside
controls. Every margin, gap and inset in the app is one of these numbers.

```
none 0 · hair 2 · xs 4 · sm 6 · md 8 · lg 12 · xl 16 · 2xl 20 · 3xl 24 · 4xl 32 · 5xl 40 · 6xl 48
```

`INSET` composes them into decisions made once: `control` 8, `row` 12,
`card` 16, `window` 24, `section` 32. A component may not invent a padding
value — the test asserts every inset comes from the spacing scale.

## Radius and border

```
RADIUS   none 0 · sm 4 · md 6 · lg 10 · xl 14 · pill 999
BORDER   none 0 · hairline 1 · default 1 · strong 1.5 · focus 2
```

`RADIUS["md"]` is 6 because that is the macOS control radius; matching it is
what keeps buttons from looking foreign.

## Shadow

Three elevations plus flat. macOS is restrained here and so is this.

| Token | Offset / blur | Used for |
| --- | --- | --- |
| `none` | — | Anything in normal flow, which is most things |
| `raised` | 1 / 3 | Cards and rows lifted off the window |
| `overlay` | 4 / 16 | Popovers, the dictionary editor |
| `modal` | 12 / 40 | Sheets over a scrim |

Dark mode carries a heavier alpha at every level, because a shadow that reads
on white is invisible on `#1B1B20`.

## Motion

```
DURATION   instant 0 · fast 120ms · base 200ms · slow 320ms
EASING     standard · decelerate (entering) · accelerate (leaving) · linear (meter only)
```

Nothing on a desktop app should exceed `slow`. Views must check
`NSWorkspace.accessibilityDisplayShouldReduceMotion` and fall back to
`instant`.

The level meter gets its own ballistics, and they are asymmetric on purpose:

```
attack 40ms · release 180ms · refresh 30Hz
```

It has to jump to a new peak immediately to feel connected to your voice, but
fall slowly or it strobes.

## Metrics

Sizes that are neither spacing nor type but still must not be invented per
view — control and row heights, sidebar and window geometry, icon sizes. See
`METRIC` in the tokens module.

## What this does not cover

Iconography and the app mark. The current mark is procedural placeholder art
(`scripts/make_icon_png.py`); a real identity pass would replace it and add a
template menu bar icon set with a distinct recording state. The colour tokens
are built to survive that.
