"""Token-driven building blocks.

Every view composes from these, and these are the only place that reads
:mod:`aloud.ui.tokens`. That is what keeps the rule enforceable: a component
here may name a token, a view may not name a value.

Two AppKit details worth knowing before reading further:

* Colours are *drawn*, not assigned to layers. A dynamic ``NSColor`` resolves
  against the current appearance when it is `set()` inside ``drawRect:``; baked
  into a ``CGColor`` on a layer it freezes at whatever the appearance was when
  the layer was configured, and the app stops following dark mode.
* Target/action needs an Objective-C object, so :class:`Action` wraps a Python
  callable. Callers must keep a reference to it — AppKit's target is a weak
  reference and a garbage-collected handler is a crash, not an error.
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

import AppKit
import Foundation
import objc

from . import tokens as T

# ---------------------------------------------------------------------------
# Target/action plumbing
# ---------------------------------------------------------------------------


class Action(Foundation.NSObject):
    """An Objective-C target that forwards to a Python callable."""

    def initWithHandler_(self, handler):
        self = objc.super(Action, self).init()
        if self is None:
            return None
        self._handler = handler
        return self

    def invoke_(self, sender):
        self._handler(sender)


def action(handler: Callable) -> Action:
    return Action.alloc().initWithHandler_(handler)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def label(
    text: str,
    style: T.TextStyle = T.TYPE_BODY,
    color: T.Color = T.TEXT_PRIMARY,
    wraps: bool = False,
    align: str = "left",
) -> AppKit.NSTextField:
    """A non-editable text field, which is how AppKit spells "a label"."""
    field = AppKit.NSTextField.alloc().init()
    field.setStringValue_(text.upper() if style.uppercase else text)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(False)
    field.setSelectable_(not wraps)
    field.setFont_(T.ns_font(style))
    field.setTextColor_(T.ns_color(color))
    field.setLineBreakMode_(
        AppKit.NSLineBreakByWordWrapping if wraps else AppKit.NSLineBreakByTruncatingTail
    )
    field.setUsesSingleLineMode_(not wraps)
    field.setAlignment_(
        {"left": AppKit.NSTextAlignmentLeft,
         "right": AppKit.NSTextAlignmentRight,
         "center": AppKit.NSTextAlignmentCenter}[align]
    )
    field.setTranslatesAutoresizingMaskIntoConstraints_(False)
    if style.tracking:
        _apply_tracking(field, style)
    if wraps:
        field.setPreferredMaxLayoutWidth_(0)
        field.cell().setWraps_(True)
    return field


def _apply_tracking(field: AppKit.NSTextField, style: T.TextStyle) -> None:
    attributed = Foundation.NSMutableAttributedString.alloc().initWithString_(
        field.stringValue()
    )
    attributed.addAttribute_value_range_(
        AppKit.NSKernAttributeName, style.tracking, (0, attributed.length())
    )
    attributed.addAttribute_value_range_(
        AppKit.NSFontAttributeName, T.ns_font(style), (0, attributed.length())
    )
    attributed.addAttribute_value_range_(
        AppKit.NSForegroundColorAttributeName, field.textColor(), (0, attributed.length())
    )
    field.setAttributedStringValue_(attributed)


def selectable_text(text: str, style: T.TextStyle = T.TYPE_TRANSCRIPT) -> AppKit.NSTextField:
    """Wrapping text the user can select and copy — transcripts, mostly."""
    field = label(text, style, T.TEXT_PRIMARY, wraps=True)
    field.setSelectable_(True)
    return field


def highlighted_text(
    text: str, spans: Sequence[tuple], style: T.TextStyle = T.TYPE_TRANSCRIPT
) -> AppKit.NSTextField:
    """Wrapping text with ``(start, length)`` ranges washed in the correction colour.

    A wash rather than a border or a colour change: a transcript with six
    corrections in it still has to be readable as a sentence.
    """
    field = selectable_text(text, style)
    attributed = Foundation.NSMutableAttributedString.alloc().initWithString_(text)
    full = (0, attributed.length())
    attributed.addAttribute_value_range_(AppKit.NSFontAttributeName, T.ns_font(style), full)
    attributed.addAttribute_value_range_(
        AppKit.NSForegroundColorAttributeName, T.ns_color(T.TEXT_PRIMARY), full
    )
    for start, length in spans:
        if start < 0 or length <= 0 or start + length > attributed.length():
            continue
        attributed.addAttribute_value_range_(
            AppKit.NSBackgroundColorAttributeName,
            T.ns_color(T.HIGHLIGHT_CORRECTION),
            (start, length),
        )
    field.setAttributedStringValue_(attributed)
    return field


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------


class TokenBox(AppKit.NSView):
    """A rectangle that paints itself from tokens.

    Used for every card, row, well and pill in the app. Drawing rather than
    layer-backing is what keeps the fill correct across an appearance change.
    """

    def initWithFill_border_radius_(self, fill, border, radius):
        self = objc.super(TokenBox, self).initWithFrame_(((0, 0), (0, 0)))
        if self is None:
            return None
        self._fill = fill
        self._border = border
        self._radius = radius
        self._border_width = T.BORDER["default"]
        self._shadow = None
        self.setTranslatesAutoresizingMaskIntoConstraints_(False)
        return self

    @objc.python_method
    def set_fill(self, fill: Optional[T.Color]) -> None:
        self._fill = fill
        self.setNeedsDisplay_(True)

    @objc.python_method
    def set_border(self, border: Optional[T.Color], width: Optional[float] = None) -> None:
        self._border = border
        if width is not None:
            self._border_width = width
        self.setNeedsDisplay_(True)

    @objc.python_method
    def set_shadow(self, shadow: Optional[T.Shadow]) -> None:
        self._shadow = shadow
        self.setShadow_(None if shadow is None else T.ns_shadow(shadow, T.is_dark(self)))

    def drawRect_(self, rect):
        bounds = self.bounds()
        inset = self._border_width / 2.0 if self._border else 0.0
        frame = Foundation.NSInsetRect(bounds, inset, inset)
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            frame, self._radius, self._radius
        )
        if self._fill is not None:
            T.ns_color(self._fill).set()
            path.fill()
        if self._border is not None:
            T.ns_color(self._border).set()
            path.setLineWidth_(self._border_width)
            path.stroke()

    def viewDidChangeEffectiveAppearance(self):
        # Shadow alpha is baked, unlike the drawn fill, so it needs re-deriving.
        if self._shadow is not None:
            self.set_shadow(self._shadow)
        self.setNeedsDisplay_(True)


def card(
    fill: T.Color = T.BG_SURFACE,
    border: Optional[T.Color] = T.BORDER_SUBTLE,
    radius: float = T.RADIUS["lg"],
    shadow: Optional[T.Shadow] = None,
) -> TokenBox:
    box = TokenBox.alloc().initWithFill_border_radius_(fill, border, radius)
    if shadow is not None:
        box.set_shadow(shadow)
    return box


def well() -> TokenBox:
    """An inset surface — search fields, code, the raw-transcript reveal."""
    return TokenBox.alloc().initWithFill_border_radius_(
        T.BG_INSET, None, T.RADIUS["md"]
    )


def pill(text: str, color: T.Color, fill: Optional[T.Color] = None) -> AppKit.NSView:
    box = TokenBox.alloc().initWithFill_border_radius_(fill, color, T.RADIUS["pill"])
    box.set_border(color, T.BORDER["hairline"])
    text_view = label(text, T.TYPE_CAPTION, color)
    box.addSubview_(text_view)
    pad_x, pad_y = T.SPACE["md"], T.SPACE["hair"]
    AppKit.NSLayoutConstraint.activateConstraints_([
        text_view.leadingAnchor().constraintEqualToAnchor_constant_(box.leadingAnchor(), pad_x),
        text_view.trailingAnchor().constraintEqualToAnchor_constant_(box.trailingAnchor(), -pad_x),
        text_view.topAnchor().constraintEqualToAnchor_constant_(box.topAnchor(), pad_y),
        text_view.bottomAnchor().constraintEqualToAnchor_constant_(box.bottomAnchor(), -pad_y),
    ])
    return box


def separator() -> TokenBox:
    line = TokenBox.alloc().initWithFill_border_radius_(T.BORDER_SUBTLE, None, 0)
    line.heightAnchor().constraintEqualToConstant_(T.BORDER["hairline"]).setActive_(True)
    return line


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------


def button(
    title: str,
    handler: Callable,
    keeper: list,
    prominent: bool = False,
    destructive: bool = False,
) -> AppKit.NSButton:
    """A standard push button. ``keeper`` holds the target so it stays alive."""
    target = action(handler)
    keeper.append(target)
    control = AppKit.NSButton.buttonWithTitle_target_action_(title, target, b"invoke:")
    control.setBezelStyle_(AppKit.NSBezelStyleRounded)
    control.setFont_(T.ns_font(T.TYPE_BODY))
    control.setTranslatesAutoresizingMaskIntoConstraints_(False)
    control.heightAnchor().constraintEqualToConstant_(
        T.METRIC["control_height_large"]
    ).setActive_(True)
    if prominent:
        control.setKeyEquivalent_("\r")
        if hasattr(control, "setBezelColor_"):
            control.setBezelColor_(T.ns_color(T.BRAND_PRIMARY))
    if destructive:
        control.setContentTintColor_(T.ns_color(T.STATUS_ERROR))
    return control


def search_field(placeholder: str, handler: Callable, keeper: list) -> AppKit.NSSearchField:
    target = action(handler)
    keeper.append(target)
    field = AppKit.NSSearchField.alloc().init()
    field.setPlaceholderString_(placeholder)
    field.setFont_(T.ns_font(T.TYPE_BODY))
    field.setTarget_(target)
    field.setAction_(b"invoke:")
    field.setSendsWholeSearchString_(False)
    field.setSendsSearchStringImmediately_(True)
    field.setTranslatesAutoresizingMaskIntoConstraints_(False)
    field.heightAnchor().constraintEqualToConstant_(
        T.METRIC["control_height_large"]
    ).setActive_(True)
    return field


def text_field(value: str, placeholder: str = "", handler: Optional[Callable] = None,
               keeper: Optional[list] = None) -> AppKit.NSTextField:
    field = AppKit.NSTextField.alloc().init()
    field.setStringValue_(value)
    field.setPlaceholderString_(placeholder)
    field.setFont_(T.ns_font(T.TYPE_BODY))
    field.setTranslatesAutoresizingMaskIntoConstraints_(False)
    field.heightAnchor().constraintEqualToConstant_(T.METRIC["field_height"]).setActive_(True)
    if handler is not None and keeper is not None:
        target = action(handler)
        keeper.append(target)
        field.setTarget_(target)
        field.setAction_(b"invoke:")
    return field


def popup(titles: Sequence[str], selected: str, handler: Callable,
          keeper: list) -> AppKit.NSPopUpButton:
    target = action(handler)
    keeper.append(target)
    control = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
        ((0, 0), (T.METRIC["field_width_min"], T.METRIC["control_height"])), False
    )
    control.addItemsWithTitles_(list(titles))
    if selected in titles:
        control.selectItemWithTitle_(selected)
    control.setTarget_(target)
    control.setAction_(b"invoke:")
    control.setFont_(T.ns_font(T.TYPE_BODY))
    control.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return control


def checkbox(title: str, checked: bool, handler: Callable, keeper: list) -> AppKit.NSButton:
    target = action(handler)
    keeper.append(target)
    control = AppKit.NSButton.checkboxWithTitle_target_action_(title, target, b"invoke:")
    control.setState_(AppKit.NSControlStateValueOn if checked else AppKit.NSControlStateValueOff)
    control.setFont_(T.ns_font(T.TYPE_BODY))
    control.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return control


def icon_button(symbol: str, tooltip: str, handler: Callable, keeper: list) -> AppKit.NSButton:
    """A small borderless button. ``symbol`` is an SF Symbol name."""
    target = action(handler)
    keeper.append(target)
    image = None
    if hasattr(AppKit.NSImage, "imageWithSystemSymbolName_accessibilityDescription_"):
        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            symbol, tooltip
        )
    if image is None:  # pre-Big Sur, or a symbol this macOS does not ship
        control = AppKit.NSButton.buttonWithTitle_target_action_(tooltip, target, b"invoke:")
    else:
        control = AppKit.NSButton.buttonWithImage_target_action_(image, target, b"invoke:")
    control.setBezelStyle_(AppKit.NSBezelStyleTexturedRounded)
    control.setBordered_(False)
    control.setToolTip_(tooltip)
    control.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return control


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def stack(
    views: Sequence[AppKit.NSView],
    vertical: bool = True,
    spacing: float = T.SPACE["lg"],
    alignment=None,
    distribution=None,
) -> AppKit.NSStackView:
    view = AppKit.NSStackView.alloc().init()
    view.setOrientation_(
        AppKit.NSUserInterfaceLayoutOrientationVertical if vertical
        else AppKit.NSUserInterfaceLayoutOrientationHorizontal
    )
    view.setSpacing_(spacing)
    if alignment is None:
        alignment = AppKit.NSLayoutAttributeLeading if vertical else AppKit.NSLayoutAttributeCenterY
    view.setAlignment_(alignment)
    if distribution is not None:
        view.setDistribution_(distribution)
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    for item in views:
        view.addArrangedSubview_(item)
    return view


def spacer(vertical: bool = False) -> AppKit.NSView:
    """An empty view that soaks up slack in a stack."""
    view = AppKit.NSView.alloc().init()
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    axis = (AppKit.NSLayoutConstraintOrientationVertical if vertical
            else AppKit.NSLayoutConstraintOrientationHorizontal)
    view.setContentHuggingPriority_forOrientation_(1, axis)
    return view


def scroller(document: AppKit.NSView) -> AppKit.NSScrollView:
    view = AppKit.NSScrollView.alloc().init()
    view.setHasVerticalScroller_(True)
    view.setHasHorizontalScroller_(False)
    view.setDrawsBackground_(False)
    view.setAutohidesScrollers_(True)
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    view.setDocumentView_(document)
    document.setTranslatesAutoresizingMaskIntoConstraints_(False)
    AppKit.NSLayoutConstraint.activateConstraints_([
        document.leadingAnchor().constraintEqualToAnchor_(view.contentView().leadingAnchor()),
        document.trailingAnchor().constraintEqualToAnchor_(view.contentView().trailingAnchor()),
        document.topAnchor().constraintEqualToAnchor_(view.contentView().topAnchor()),
    ])
    return view


def pad(view: AppKit.NSView, inset: float = T.INSET["card"],
        container: Optional[AppKit.NSView] = None) -> AppKit.NSView:
    """Wrap a view with uniform padding."""
    host = container if container is not None else AppKit.NSView.alloc().init()
    host.setTranslatesAutoresizingMaskIntoConstraints_(False)
    host.addSubview_(view)
    AppKit.NSLayoutConstraint.activateConstraints_([
        view.leadingAnchor().constraintEqualToAnchor_constant_(host.leadingAnchor(), inset),
        view.trailingAnchor().constraintEqualToAnchor_constant_(host.trailingAnchor(), -inset),
        view.topAnchor().constraintEqualToAnchor_constant_(host.topAnchor(), inset),
        view.bottomAnchor().constraintEqualToAnchor_constant_(host.bottomAnchor(), -inset),
    ])
    return host


def clear(view: AppKit.NSStackView) -> None:
    """Empty a stack view, which AppKit does not offer a one-liner for."""
    for child in list(view.arrangedSubviews()):
        view.removeArrangedSubview_(child)
        child.removeFromSuperview()


def empty_state(headline: str, detail: str) -> AppKit.NSView:
    return stack(
        [
            label(headline, T.TYPE_DISPLAY, T.TEXT_TERTIARY),
            label(detail, T.TYPE_BODY, T.TEXT_TERTIARY, wraps=True),
        ],
        spacing=T.SPACE["md"],
    )


__all__ = [
    "Action", "TokenBox", "action", "button", "card", "checkbox", "clear",
    "empty_state", "highlighted_text", "icon_button", "label", "pad", "pill",
    "popup", "scroller", "search_field", "selectable_text", "separator",
    "spacer", "stack", "text_field", "well",
]
