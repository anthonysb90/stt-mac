"""The live input level meter.

Two decisions carry this view:

**Peak, not RMS.** The meter's job is to tell you whether you are clipping or
inaudible. RMS barely moves at the top of the range, which is exactly where the
answer matters.

**Asymmetric ballistics.** It rises with a 40 ms time constant and falls with a
180 ms one (``METER_ATTACK_SECONDS`` / ``METER_RELEASE_SECONDS``). Rising fast
is what makes the meter feel connected to your voice; falling slow is what stops
it strobing at 30 Hz. A symmetric meter reads as broken in one direction or the
other whichever constant you pick.

The view polls the controller rather than being pushed to. Levels arrive on the
audio callback thread many times a second, and marshalling each one to the main
thread would cost more than reading a float on a timer.
"""

from __future__ import annotations

from typing import Callable, Optional

import AppKit
import objc

from . import tokens as T
from .formatting import meter_colour


class LevelMeter(AppKit.NSView):
    """A horizontal bar that fills with the current input level."""

    def initWithSource_(self, source: Callable[[], float]):
        self = objc.super(LevelMeter, self).initWithFrame_(((0, 0), (0, 0)))
        if self is None:
            return None
        self._source = source
        self._level = 0.0
        self._peak_hold = 0.0
        self._peak_decay_at = 0.0
        self._timer: Optional[AppKit.NSTimer] = None
        self._active = False
        self.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.heightAnchor().constraintEqualToConstant_(
            T.METRIC["meter_height_large"]
        ).setActive_(True)
        return self

    # -- lifecycle ---------------------------------------------------------

    @objc.python_method
    def start(self) -> None:
        if self._timer is not None:
            return
        self._active = True
        interval = 1.0 / T.METER_REFRESH_HZ
        self._timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            interval, self, b"tick:", None, True
        )
        # Keep running while a menu is open or the window is being resized.
        AppKit.NSRunLoop.currentRunLoop().addTimer_forMode_(
            self._timer, AppKit.NSRunLoopCommonModes
        )

    @objc.python_method
    def stop(self) -> None:
        self._active = False
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        self._level = 0.0
        self._peak_hold = 0.0
        self.setNeedsDisplay_(True)

    def tick_(self, _timer):
        target = 0.0
        try:
            target = max(0.0, min(float(self._source()), 1.0))
        except Exception:
            target = 0.0

        step = 1.0 / T.METER_REFRESH_HZ
        tau = T.METER_ATTACK_SECONDS if target > self._level else T.METER_RELEASE_SECONDS
        self._level += (target - self._level) * min(step / tau, 1.0)

        # A peak marker that lingers, so a clip you looked away from is still
        # visible a moment later.
        if self._level >= self._peak_hold:
            self._peak_hold = self._level
            self._peak_decay_at = 0.0
        else:
            self._peak_decay_at += step
            if self._peak_decay_at > T.METER_PEAK_HOLD_SECONDS:
                self._peak_hold = max(
                    self._peak_hold - step * T.METER_PEAK_FALL_RATE, self._level
                )

        self.setNeedsDisplay_(True)

    # -- drawing -----------------------------------------------------------

    def drawRect_(self, _rect):
        bounds = self.bounds()
        radius = T.RADIUS["sm"]

        track = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, radius, radius
        )
        T.ns_color(T.METER_TRACK).set()
        track.fill()

        if not self._active or self._level <= T.METER_SILENCE_FLOOR:
            return

        width = max(bounds.size.width * self._level, T.METRIC["meter_height_large"])
        fill_rect = ((bounds.origin.x, bounds.origin.y), (width, bounds.size.height))
        fill = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            fill_rect, radius, radius
        )
        T.ns_color(meter_colour(self._level)).set()
        fill.fill()

        if self._peak_hold > self._level + T.METER_PEAK_GAP:
            marker_x = bounds.origin.x + bounds.size.width * self._peak_hold
            width = T.BORDER["focus"]
            marker = (
                (marker_x - width / 2, bounds.origin.y), (width, bounds.size.height)
            )
            T.ns_color(meter_colour(self._peak_hold)).set()
            AppKit.NSBezierPath.bezierPathWithRect_(marker).fill()

    def isFlipped(self):
        return True

    # -- accessibility -----------------------------------------------------

    def accessibilityLabel(self):
        return "Input level"

    def accessibilityValue(self):
        return f"{int(round(self._level * 100))} percent"

    def accessibilityRole(self):
        return AppKit.NSAccessibilityLevelIndicatorRole
