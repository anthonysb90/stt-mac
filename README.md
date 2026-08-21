<div align="center">

<img src="assets/Aloud-1024.png" width="120" alt="Aloud">

# Aloud

**Say it aloud, see it typed.** Push-to-talk dictation for macOS: hold a key,
talk, release — your words land in whatever app you're looking at.

</div>

A local-first take on [Wispr Flow](https://wisprflow.ai/features). Transcription
runs on your machine via [whisper.cpp](https://github.com/ggml-org/whisper.cpp),
so nothing leaves it unless you switch to the cloud engine on purpose.

> **Status: skeleton.** The full pipeline is wired end to end and covered by
> tests, but it has not yet been run on real hardware — see
> [Verification status](#verification-status).

---

## Requirements

* macOS 10.15 or newer, Intel **or** Apple Silicon
* Python 3.9+ (the system `python3` is fine)
* Xcode **Command Line Tools** — *not* Xcode itself. Free, ~2 GB,
  `xcode-select --install`. `bootstrap.sh` prompts you if it's missing.

## Setup

```sh
git clone <this repo> && cd stt-mac
./scripts/bootstrap.sh
```

That installs the Command Line Tools if needed, creates a virtualenv, installs
dependencies, gets the whisper.cpp CLI (Homebrew, or a source build as a
fallback), and downloads a model sized for your CPU — `small.en` on Apple
Silicon, `base.en` on Intel.

Then build and launch the app:

```sh
make dev-app
```

On first launch macOS asks for **Microphone** access. You must also add Aloud
under **System Settings → Privacy & Security → Accessibility** by hand — there
is no way for an app to grant that itself. Quit and reopen Aloud afterwards.

Now hold **Right Option**, say something, and release.

## Everyday commands

```sh
make doctor      # engine, model, input device, and permission status
make run         # run in the terminal (permissions attach to the terminal)
make dev-app     # build the alias .app and open it
make app         # build a standalone, distributable .app
make test        # run the test suite
make icon        # regenerate the app icon
```

The CLI also works standalone, which is handy for isolating problems:

```sh
.venv/bin/python -m aloud doctor
.venv/bin/python -m aloud transcribe some.wav
.venv/bin/python -m aloud history -n 20
```

## Configuration

`~/Library/Application Support/Aloud/config.json`, or **Open Config…** in the
menu. Anything you leave out falls back to a default, so a two-line file is
valid.

```jsonc
{
  "hotkey": { "mode": "hold", "key": "right_option" },
  "engine": "whisper_cpp",
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

**`engine`** — `whisper_cpp` (local, default), `openai` (cloud; set
`OPENAI_API_KEY` and note that this uploads your audio), `mock` (fixed text,
for testing the loop without a model).

Swap the Whisper model at any time:

```sh
./scripts/fetch_model.sh medium.en   # tiny.en · base.en · small.en · medium.en · large-v3-turbo
```

## How it works

```
hotkey (Quartz event tap) → recorder (16 kHz mono) → engine (whisper.cpp)
    → postprocess (fillers, dictionary, commands) → injector (paste into focused app)
```

Each stage is a module with one job, and the engine layer is an interface with
three implementations. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) covers the
reasoning: why whisper.cpp rather than the faster Apple-Silicon-only runtimes,
why Python + PyObjC rather than Swift, the threading rules, the latency budget,
and what a Swift port would replace.

## Verification status

Written and tested on Linux, which means:

* **Verified** — 45 unit tests pass, covering the hotkey state machine,
  post-processing, config merging, the engine registry, both injection modes,
  and the full press → transcribe → deliver path against framework stubs.
  Every module compiles; every shell script passes `bash -n`.
* **Not yet verified** — anything that needs real hardware: the Quartz event
  tap against a physical keyboard, PortAudio capture, the py2app build, code
  signing, and TCC permission prompts. Run `make doctor` first, then
  `make dev-app`, and expect to shake out a few things on the first pass.

## Troubleshooting

**The hotkey does nothing.** Accessibility is not granted, or it was granted to
a previous build. Menu → *Check Permissions…*. After an ad-hoc-signed rebuild
you have to remove and re-add Aloud in the Accessibility list — see the
signing note in the architecture doc for how to avoid that.

**Text goes to the wrong place.** `paste` mode sends Cmd-V to whatever has
focus. Don't click away while it's transcribing.

**"whisper-cli not found".** `make doctor` shows what was searched. Either
`brew install whisper-cpp`, or set `engines.whisper_cpp.binary` to an absolute
path.

**It's slow on the Intel Mac.** Expected — there's no GPU path there. Drop to
`base.en` or `tiny.en`, or switch to the `openai` engine for that machine.

**Nothing at all happens.** `tail -f ~/Library/Logs/Aloud/aloud.log`.

## Layout

```
src/aloud/
  app.py          menu bar shell, state machine, job queue
  hotkey.py       Quartz event tap, push-to-talk edges
  audio.py        microphone capture → WAV
  engines/        whisper.cpp · OpenAI-compatible API · mock
  postprocess.py  fillers, dictionary, spoken commands
  injector.py     paste or type into the focused app
  permissions.py  TCC checks and settings deep links
scripts/          bootstrap · model download · icon · app build
docs/             architecture and decisions
tests/            unit tests + macOS framework stubs
```

## Licence

Not yet chosen.
