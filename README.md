<div align="center">

<img src="assets/Aloud-1024.png" width="120" alt="Aloud">

# Aloud

**Say it aloud, see it typed.** Push-to-talk dictation for macOS: hold a key,
talk, release — your words land in whatever app you're looking at.

</div>

A local-first take on [Wispr Flow](https://wisprflow.ai/features). Transcription
runs on your machine — Parakeet on Apple Silicon, faster-whisper on Intel — so
nothing leaves it unless you switch to the cloud engine on purpose.

> **Status: built, not yet run on hardware.** Everything is wired and covered by
> tests, but no part of it has executed on a Mac — see
> [Verification status](#verification-status).

---

## Install

Full step-by-step instructions for each machine, including the permissions
macOS makes you grant by hand: **[docs/INSTALL.md](docs/INSTALL.md)**, or the
same guide as a page you can follow on the machine itself —
`docs/install-guide.html`.

The short version:

```sh
git clone <this repo> && cd stt-mac
./scripts/bootstrap.sh     # toolchain, engine for this Mac, model
make install               # builds Aloud.app into /Applications
```

Then add Aloud under **System Settings → Privacy & Security → Accessibility**,
quit it, and open it again. Hold **Right Option**, speak, release.

Requirements: macOS 11+, Xcode **Command Line Tools** (not Xcode), and Python
3.10+ on Apple Silicon / 3.9+ on Intel. Bootstrap checks all three and tells you
what to do.

## The app

**Main window** — `⌘1` History, `⌘2` Dictionary.

* **History** — every dictation, searchable, one click to copy. When the
  Dictionary changed something, the row says what fired and washes the changed
  words in the transcript, so you can tell whether it is earning its keep.
* **Dictionary** — words to teach it, and corrections to apply. Add, edit,
  delete, search, disable without deleting.
* **Transport** — live level meter, state, start/stop. Pinned above both panes,
  because whether the microphone is live is true regardless of what you are
  looking at.

**Transcribe a file** (`⌘O`) — point it at an mp3, m4a, wav, or the audio track
of a screen recording. Converted to 16 kHz mono first where ffmpeg allows, so
it takes the same path a dictation does. The result opens in its own window,
copyable and saveable, and is deliberately *not* typed into whatever app you
had open.

**Settings** (`⌘,`) — the hotkey, the model, API keys for the cloud engines,
and the start/stop sounds (which play as you pick them, since the only way to
judge a cue is to hear it). Paste a key straight in; it is stored where a Dock-launched app can
read it, which your shell environment is not. Everything else stays in
`config.json`, the better editor for a long tail of options.

**Menu bar item** — secondary, but the one you see most, since you dictate *into*
other apps. The glyph carries state: `◌` idle, `●` recording, `◍` transcribing.

## The Dictionary

Two kinds of entry, and both mechanisms behind them, because neither is enough
alone.

| | |
| --- | --- |
| **Word** | Something it should know — `Anthropic`, `Supabase`. Biases the engine before it decodes, and canonicalises its own spelling afterwards. |
| **Correction** | When you hear X, write Y — `cloud code` → `Claude Code`. The guaranteed path. |

**Biasing** primes the engine, capped at 12 terms — a long prompt makes these
models drift and invent text over quiet audio. Deepgram gets them as *keyterms*,
Whisper-family engines as an initial prompt, and Parakeet not at all (its
decoder has nowhere to put one), which is exactly why biasing alone is not the
answer.

**The correction pass** is one regex over the original text: longest match
first, case-insensitive, whole-word. It is tolerant of the two ways models
mangle names — glued or hyphenated (`CloudCode`, `Cloud-Code`) and split apart
(`Supa base`) — while requiring the *full* pattern, so an entry for
`Claude Code` can never touch `Cloudflare` or the ordinary word `cloud`.

Entries are checked as you type. An entry that would rewrite an everyday word
gets a warning before it exists, including the subtle case: `in put` would match
every `input`, because the separator is allowed to be empty.

The file is `~/Library/Application Support/Aloud/dictionary.json` and is meant
to be hand-edited — omit `id` and `kind` and they are inferred, unknown keys
survive a round trip, and edits made while the app is running are picked up.

## Everyday commands

```sh
make doctor      # engine, model, input device, and permission status
make warm        # load the engine now (downloads the model on first run)
make install     # build Aloud.app and put it in /Applications
make dev-app     # build a bundle that links back to this checkout
make run         # run in the terminal (permissions attach to the terminal)
make test        # run the test suite
make tokens      # dump the design tokens to docs/tokens.json
make icon        # regenerate the app icon
```

The CLI also works standalone, which is handy for isolating problems:

```sh
.venv/bin/python -m aloud doctor          # which engine is active, and why
.venv/bin/python -m aloud warm            # pre-load the model
.venv/bin/python -m aloud key deepgram    # store a cloud API key
.venv/bin/python -m aloud transcribe x.wav
.venv/bin/python -m aloud history -n 20
```

## Configuration

`~/Library/Application Support/Aloud/config.json`, or **Open Config…** in the
menu. Anything you leave out falls back to a default, so a two-line file is
valid.

```jsonc
{
  "hotkey": { "mode": "hold", "key": "right_option" },
  "engine": "auto",
  "output": { "mode": "paste", "trailing_space": true },
  "postprocess": {
    "dictionary": { "clawed": "Claude" },
    "commands": { "new line": "\n" }
  }
}
```

**`hotkey.key`** — `right_option` (default), `left_option`, `right_command`,
`left_command`, `right_control`, `left_control`, `right_shift`, `left_shift`,
`fn`.

> Right Option is the default because it does nothing on its own. `fn` is the
> Wispr-like choice, but macOS gives it a built-in action — set **System
> Settings → Keyboard → Press 🌐 key to → Do Nothing** first, or it will open
> the emoji picker every time you dictate.

**`hotkey.mode`** — `hold` for true push-to-talk, `toggle` to tap once to start
and again to stop.

**`output.mode`** — `paste` (clipboard + Cmd-V; fast, near-universal, restores
your clipboard), `type` (synthesizes each character; slower but never touches
the clipboard), `clipboard` (copy only).

**`engine`** — `auto` by default, which resolves per machine:

| Value | What it is |
| --- | --- |
| `auto` | Parakeet on Apple Silicon, faster-whisper on Intel, whisper.cpp if neither is installed yet |
| `parakeet_mlx` | Apple Silicon only. Fastest local option, and doesn't hallucinate over silence |
| `faster_whisper` | CPU, int8. The Intel default; also works on Apple Silicon |
| `whisper_cpp` | Offline fallback. No Python ML stack, but reloads the model every dictation |
| `deepgram` | Cloud. Transcription **and** cleanup in one call, plus keyterm prompting |
| `openai` | Cloud. Whisper via an OpenAI-compatible endpoint |
| `mock` | Fixed text, for testing the loop without a model |

Cloud engines upload your audio, so they are never selected automatically.
Store their keys with `aloud key deepgram` — an app launched from the Dock does
not inherit your shell environment, so an exported variable would work in
Terminal and silently fail in the app.

`auto` skips any engine that isn't ready and falls through to the next. An
engine you name explicitly is always used, even if it's broken — the menu bar
tells you why rather than quietly substituting a different one.

Swap models per engine under `engines.<name>.model`, e.g. `small.en` for
faster-whisper or `mlx-community/parakeet-tdt-0.6b-v2` (English-only, smaller)
for Parakeet. For the whisper.cpp fallback the model is a file:

```sh
./scripts/fetch_model.sh medium.en   # tiny.en · base.en · small.en · medium.en · large-v3-turbo
```

## How it works

```
hotkey (Quartz event tap) → recorder (16 kHz mono) → engine (resident model)
    → corrections (the Dictionary) → postprocess → injector (paste into focused app)
```

`core.py` owns all of that and imports no AppKit, so the pipeline is testable
without a window and the views never touch a thread they shouldn't. The engine
layer is an interface with six implementations; the two local defaults keep the
model **resident in-process**, so after a one-off warm-up there's no process
spawn and no model reload per dictation.

Everything visual pulls from [`src/aloud/ui/tokens.py`](src/aloud/ui/tokens.py).
No view defines its own colour, size, radius, duration or spacing — and a test
enforces it. See [`docs/DESIGN.md`](docs/DESIGN.md) for the system and its
reasoning.

[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) covers the rest: why a different
engine per architecture, why Python + PyObjC rather than Swift, the threading
rules, the latency budget, and who owns cleanup.

## Verification status

Written and tested on Linux, which means:

* **Verified** — 293 unit tests pass, covering the hotkey state machine, the
  correction engine's matching and risk analysis, the Dictionary file format,
  per-architecture engine selection, Deepgram's request shaping, key lookup,
  cleanup ownership, the design tokens' own contrast and scale rules, and the
  full press → transcribe → correct → deliver path. A structural test asserts no
  view hard-codes a colour, font, or size. Every module compiles; every shell
  script passes `bash -n`.
* **Not yet verified** — anything needing real hardware: the Quartz event tap
  against a physical keyboard, PortAudio capture, **every AppKit view**, loading
  Parakeet or faster-whisper, live Deepgram calls, the py2app build, code
  signing, and TCC prompts. The AppKit code follows documented API but has not
  been run — expect to shake out layout and constraint issues on the first
  launch. Start with `make doctor`, then `make warm`, then `make install`.

## Troubleshooting

**The hotkey does nothing.** Accessibility is not granted, or it was granted to
a previous build. Menu → *Check Permissions…*. After an ad-hoc-signed rebuild
you have to remove and re-add Aloud in the Accessibility list — see the
signing note in the architecture doc for how to avoid that.

**Text goes to the wrong place.** `paste` mode sends Cmd-V to whatever has
focus. Don't click away while it's transcribing.

**The first dictation after launch is slow.** That's the model load. `make
warm` pays it up front; the menu bar engine line says `loaded` once it's done.

**"parakeet-mlx is not installed" on the M1.** Almost always Python: it needs
3.10+, and macOS ships 3.9. See [Requirements](#requirements).

**"ffmpeg not found".** `brew install ffmpeg` — Parakeet decodes audio with it.

**"whisper-cli not found".** Only relevant if you selected `whisper_cpp`.
`./scripts/bootstrap.sh --with-whisper-cpp`, or set
`engines.whisper_cpp.binary` to an absolute path.

**It's slow on the Intel Mac.** Expected — there's no GPU path there. Drop
`engines.faster_whisper.model` to `tiny.en`, or switch that machine to the
`openai` engine.

**Nothing at all happens.** `tail -f ~/Library/Logs/Aloud/aloud.log`.

## Layout

```
src/aloud/
  core.py         the pipeline: state machine, job queue, no AppKit
  app.py          NSApplication delegate; the seam between core and views
  hotkey.py       Quartz event tap, push-to-talk edges
  audio.py        microphone capture → WAV, plus the level the meter reads
  engines/        Parakeet/MLX · faster-whisper · whisper.cpp · Deepgram ·
                  OpenAI · mock, plus per-architecture `auto` selection
  dictionary.py   entries, and the hand-editable JSON behind them
  corrections.py  the correction pass and its risk analysis
  postprocess.py  fillers and spoken commands, skipped when the engine did them
  injector.py     paste or type into the focused app
  secrets.py      API keys, findable from a Dock launch
  ui/
    tokens.py     the design system — the only file with values in it
    components.py token-driven building blocks
    main_window.py · history_view.py · dictionary_view.py
    settings_window.py · menu_bar.py · app_menu.py · meter.py
    formatting.py presentation logic with no AppKit in it
scripts/          bootstrap · model download · icon · app build
docs/             INSTALL · ARCHITECTURE · DESIGN
tests/            unit tests
```

## Licence

Not yet chosen.
