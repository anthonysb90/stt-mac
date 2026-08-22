"""Settings: the hotkey, and the model.

Deliberately two sections and no more. Everything else in ``config.json``
stays there — a preferences window that mirrors every key is a window nobody
finishes reading, and the file is already the better editor for the long tail.

Changes apply immediately. There is no Save button because there is nothing to
save: the hotkey is reinstalled and the engine reselected as you change them,
which is also the only way to find out whether your new hotkey actually works.
"""

from __future__ import annotations

from typing import Optional

import AppKit

from .. import APP_NAME, engines, feedback, secrets
from ..hotkey import available_keys, describe
from . import components as C
from . import tokens as T

MODE_LABELS = {"hold": "Hold to talk", "toggle": "Tap to start and stop"}

#: Suggestions per engine, shown under the model field. Free text is allowed —
#: these models change faster than this app will.
MODEL_HINTS = {
    "parakeet_mlx": "mlx-community/parakeet-tdt-0.6b-v3 · …-v2 is English-only and smaller",
    "faster_whisper": "tiny.en · base.en · small.en · medium.en · large-v3",
    "whisper_cpp": "Set a path to a ggml-*.bin file, or leave blank to use the newest",
    "deepgram": "nova-3 (needed for keyterm prompting) · nova-2 · whisper-large",
    "openai": "whisper-1 · gpt-4o-transcribe",
    "mock": "Not used",
}


class SettingsWindow:
    """A small, single-panel settings window on Cmd-comma."""

    def __init__(self, controller, on_hotkey_changed=None) -> None:
        self.controller = controller
        self.config = controller.config
        self._on_hotkey_changed = on_hotkey_changed
        self._keeper: list = []
        self.window: Optional[AppKit.NSWindow] = None
        self._model_field = None
        self._model_hint = None
        self._engine_detail = None
        self._key_field = None
        self._key_status = None
        self._key_buttons = None
        self._key_rows = None
        self._start_popup = None
        self._stop_popup = None

    # -- presentation ------------------------------------------------------

    def show(self) -> None:
        if self.window is None:
            self._build()
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def _build(self) -> None:
        content = C.stack(
            [
                self._hotkey_section(),
                C.separator(),
                self._model_section(),
                C.separator(),
                self._credentials_section(),
                C.separator(),
                self._sounds_section(),
            ],
            spacing=T.INSET["section"],
        )
        content.setAlignment_(AppKit.NSLayoutAttributeLeading)

        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (T.METRIC["settings_width"], T.METRIC["settings_height"])),
            AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window.setTitle_(f"{APP_NAME} Settings")
        window.setReleasedWhenClosed_(False)

        host = AppKit.NSView.alloc().init()
        window.setContentView_(host)
        C.pad(content, T.INSET["window"], container=host)
        content.widthAnchor().constraintEqualToConstant_(
            T.METRIC["settings_width"] - 2 * T.INSET["window"]
        ).setActive_(True)

        self.window = window

    # -- hotkey ------------------------------------------------------------

    def _hotkey_section(self) -> AppKit.NSView:
        key = str(self.config.get("hotkey.key", "right_option"))
        mode = str(self.config.get("hotkey.mode", "hold"))

        key_names = available_keys()
        key_popup = C.popup(
            [describe(name) for name in key_names], describe(key),
            lambda sender: self._set_hotkey_key(key_names[sender.indexOfSelectedItem()]),
            self._keeper,
        )
        mode_popup = C.popup(
            list(MODE_LABELS.values()), MODE_LABELS[mode],
            lambda sender: self._set_hotkey_mode(
                list(MODE_LABELS)[sender.indexOfSelectedItem()]
            ),
            self._keeper,
        )

        note = C.label(
            "Right Option is the default because it does nothing on its own. "
            "The Fn key works too, but macOS gives it a built-in action — set "
            "Keyboard → Press 🌐 key to → Do Nothing first, or it will open the "
            "emoji picker every time you dictate.",
            T.TYPE_CALLOUT, T.TEXT_TERTIARY, wraps=True,
        )
        return self._section("Hotkey", [
            self._field_row("Key", key_popup),
            self._field_row("Behaviour", mode_popup),
            note,
        ])

    def _set_hotkey_key(self, key: str) -> None:
        self.config.set("hotkey.key", key)
        self.config.save()
        self.controller.install_hotkey()
        if self._on_hotkey_changed is not None:
            self._on_hotkey_changed()

    def _set_hotkey_mode(self, mode: str) -> None:
        self.config.set("hotkey.mode", mode)
        self.config.save()
        self.controller.install_hotkey()
        if self._on_hotkey_changed is not None:
            self._on_hotkey_changed()

    # -- model -------------------------------------------------------------

    def _model_section(self) -> AppKit.NSView:
        names = [engines.AUTO] + engines.names()
        labels = ["Automatic"] + [engines.REGISTRY[n].label for n in engines.names()]
        current = str(self.config.get("engine", engines.AUTO))
        selected = labels[names.index(current)] if current in names else labels[0]

        engine_popup = C.popup(
            labels, selected,
            lambda sender: self._set_engine(names[sender.indexOfSelectedItem()]),
            self._keeper,
        )
        self._engine_detail = C.label("", T.TYPE_CAPTION, T.TEXT_TERTIARY, wraps=True)

        self._model_field = C.text_field(
            self._current_model(), "Leave blank for the default",
            lambda sender: self._set_model(sender.stringValue()), self._keeper,
        )
        self._model_hint = C.label("", T.TYPE_CAPTION, T.TEXT_TERTIARY, wraps=True)
        self._refresh_model_section()

        return self._section("Model", [
            self._field_row("Engine", engine_popup),
            self._engine_detail,
            self._field_row("Model", self._model_field),
            self._model_hint,
        ])

    def _active_engine_name(self) -> str:
        return self.controller.engine.name

    def _current_model(self) -> str:
        return str(self.config.get(f"engines.{self._active_engine_name()}.model", "") or "")

    def _set_engine(self, name: str) -> None:
        self.controller.use_engine(name)
        self._refresh_model_section()
        self._refresh_credentials()

    def _set_model(self, value: str) -> None:
        self.config.set(f"engines.{self._active_engine_name()}.model", value.strip())
        self.config.save()
        # Rebuild so the new model is loaded rather than the old one lingering.
        self.controller.use_engine(str(self.config.get("engine", engines.AUTO)))
        self._refresh_model_section()

    def _refresh_model_section(self) -> None:
        name = self._active_engine_name()
        ok, detail = self.controller.engine.check()
        prefix = "" if ok else "⚠ "
        configured = str(self.config.get("engine", engines.AUTO))
        resolved = f"Resolved to {self.controller.engine.label}. " if configured == engines.AUTO else ""
        self._engine_detail.setStringValue_(f"{prefix}{resolved}{detail}")
        self._engine_detail.setTextColor_(
            T.ns_color(T.TEXT_TERTIARY if ok else T.STATUS_WARNING)
        )
        self._model_field.setStringValue_(self._current_model())
        self._model_field.setEnabled_(name != "mock")
        self._model_hint.setStringValue_(MODEL_HINTS.get(name, ""))

    # -- sounds ------------------------------------------------------------

    def _sounds_section(self) -> AppKit.NSView:
        """Start and stop cues.

        These are the only feedback you get while looking at another app, so
        they matter more than their size suggests. Which one reads as cheerful
        rather than as a failure is entirely subjective, so the picker plays
        each sound as you select it rather than making you guess from a name.
        """
        names = [n for n in feedback.available_sounds()] or ["Bottle", "Glass"]
        labels = [feedback.describe(name) for name in names]

        toggle = C.checkbox(
            "Play a sound when recording starts and stops",
            bool(self.config.get("feedback.sounds", True)),
            lambda sender: self._set_sounds_enabled(
                sender.state() == AppKit.NSControlStateValueOn
            ),
            self._keeper,
        )

        self._start_popup = C.popup(
            labels,
            feedback.describe(str(self.config.get("feedback.start_sound", "Bottle"))),
            lambda sender: self._set_sound("start_sound", names[sender.indexOfSelectedItem()]),
            self._keeper,
        )
        self._stop_popup = C.popup(
            labels,
            feedback.describe(str(self.config.get("feedback.stop_sound", "Glass"))),
            lambda sender: self._set_sound("stop_sound", names[sender.indexOfSelectedItem()]),
            self._keeper,
        )

        hint = C.label(
            "Choosing a sound plays it. Basso, Funk and Sosumi are alert sounds — "
            "they will read as something going wrong.",
            T.TYPE_CAPTION, T.TEXT_TERTIARY, wraps=True,
        )

        return self._section("Sounds", [
            toggle,
            self._field_row("Start", self._start_popup),
            self._field_row("Stop", self._stop_popup),
            hint,
        ])

    def _set_sounds_enabled(self, enabled: bool) -> None:
        self.config.set("feedback.sounds", enabled)
        self.config.save()
        self.controller.reload_feedback()
        if enabled:
            feedback.play(str(self.config.get("feedback.start_sound", "Bottle")))

    def _set_sound(self, key: str, name: str) -> None:
        self.config.set(f"feedback.{key}", name)
        self.config.save()
        self.controller.reload_feedback()
        # Play it now: the whole point is hearing it before you commit.
        feedback.play(name)

    # -- credentials -------------------------------------------------------

    def _credentials_section(self) -> AppKit.NSView:
        """Where a cloud engine's API key gets pasted.

        Storing it here rather than in the environment is not a convenience: a
        GUI app launched from the Dock does not inherit your shell, so an
        exported variable works in a terminal and silently fails in the app.
        """
        self._key_field = C.secure_field(
            "Paste your API key", lambda _s: self._save_key(), self._keeper
        )
        self._key_status = C.label("", T.TYPE_CAPTION, T.TEXT_TERTIARY, wraps=True)

        save = C.button("Save Key", lambda _s: self._save_key(), self._keeper)
        clear = C.button("Remove", lambda _s: self._clear_key(), self._keeper)
        self._key_buttons = C.stack(
            [C.spacer(), clear, save], vertical=False, spacing=T.SPACE["md"]
        )

        self._key_rows = C.stack(
            [self._field_row("API key", self._key_field), self._key_buttons, self._key_status],
            spacing=T.SPACE["lg"],
        )
        self._key_rows.setAlignment_(AppKit.NSLayoutAttributeLeading)

        section = self._section("Credentials", [self._key_rows])
        self._refresh_credentials()
        return section

    def _refresh_credentials(self) -> None:
        engine = self.controller.engine
        needed = bool(getattr(engine, "needs_api_key", False))
        for view in (self._key_field, self._key_buttons):
            view.setHidden_(not needed)

        if not needed:
            self._key_status.setStringValue_(
                f"{engine.label} runs on this Mac and needs no key."
            )
            self._key_status.setTextColor_(T.ns_color(T.TEXT_TERTIARY))
            return

        source = secrets.describe_source(engine.api_key_env, engine.name)
        self._key_field.setStringValue_("")
        if source == "not set":
            self._key_status.setStringValue_(
                f"No key stored for {engine.label}. Paste one above and press Save."
            )
            self._key_status.setTextColor_(T.ns_color(T.STATUS_WARNING))
        else:
            self._key_status.setStringValue_(f"A key is stored — read from {source}.")
            self._key_status.setTextColor_(T.ns_color(T.STATUS_SUCCESS))

    def _save_key(self) -> None:
        engine = self.controller.engine
        value = self._key_field.stringValue().strip()
        if not value:
            self._key_status.setStringValue_("Nothing to save — the field is empty.")
            self._key_status.setTextColor_(T.ns_color(T.STATUS_WARNING))
            return
        secrets.store_key(engine.name, value)
        self._key_field.setStringValue_("")
        # Rebuild so the engine picks the key up without a restart.
        self.controller.use_engine(str(self.config.get("engine", engines.AUTO)))
        self._refresh_model_section()
        self._refresh_credentials()

    def _clear_key(self) -> None:
        engine = self.controller.engine
        secrets.forget_key(engine.name)
        self.controller.use_engine(str(self.config.get("engine", engines.AUTO)))
        self._refresh_model_section()
        self._refresh_credentials()

    # -- layout helpers ----------------------------------------------------

    def _section(self, title: str, rows) -> AppKit.NSView:
        stack = C.stack(
            [C.label(title, T.TYPE_TITLE_2)] + list(rows), spacing=T.SPACE["lg"]
        )
        stack.setAlignment_(AppKit.NSLayoutAttributeLeading)
        for row in rows:
            row.widthAnchor().constraintEqualToAnchor_(stack.widthAnchor()).setActive_(True)
        return stack

    def _field_row(self, caption: str, control: AppKit.NSView) -> AppKit.NSView:
        caption_label = C.label(caption, T.TYPE_BODY, T.TEXT_SECONDARY, align="right")
        caption_label.widthAnchor().constraintEqualToConstant_(T.METRIC["form_label_width"]).setActive_(True)
        row = C.stack([caption_label, control], vertical=False, spacing=T.SPACE["lg"])
        control.setContentHuggingPriority_forOrientation_(
            1, AppKit.NSLayoutConstraintOrientationHorizontal
        )
        return row
