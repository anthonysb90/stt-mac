<div align="center">

<img src="assets/Aloud-1024.png" width="120" alt="Aloud">

# Aloud

**Say it aloud, see it typed.** Push-to-talk dictation for macOS: hold a key,
talk, release — your words land in whatever app you're looking at.

</div>

A local-first take on [Wispr Flow](https://wisprflow.ai/features). Transcription
runs on your machine — Parakeet on Apple Silicon, faster-whisper on Intel — so
nothing leaves it unless you switch to the cloud engine on purpose.

> **Status: skeleton.** The full pipeline is wired end to end and covered by
> tests, but it has not yet been run on real hardware — see
> [Verification status](#verification-status).

---

## Requirements

* macOS 10.15 or newer, Intel **or** Apple Silicon
* Xcode **Command Line Tools** — *not* Xcode itself. Free, ~2 GB,
  `xcode-select --install`. `bootstrap.sh` prompts you if it's missing.
* Python — **3.10+ on Apple Silicon** (Parakeet needs it), 3.9+ on Intel. If
  your system `python3` is older, `brew install python@3.12` and re-run
  bootstrap with `PYTHON_BIN=$(brew --prefix)/bin/python3.12`.
* **ffmpeg** on Apple Silicon — Parakeet decodes audio with it. Bootstrap
  installs it via Homebrew.

## Setup

```sh
git clone <this repo> && cd stt-mac
./scripts/bootstrap.sh
```

Bootstrap installs the Command Line Tools if needed, creates a virtualenv,
installs the right engine for your architecture, and pre-downloads its model so
your first dictation isn't a multi-gigabyte surprise.

| | Apple Silicon | Intel |
| --- | --- | --- |
| Engine | Parakeet on MLX | faster-whisper (CPU, int8) |
| Model | `parakeet-tdt-0.6b-v3` (~2.4 GB) | `base.en` (~150 MB) |

Add `--with-whisper-cpp` if you also want the offline fallback engine installed.

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
make warm        # load the engine now (downloads the model on first run)
make run         # run in the terminal (permissions attach to the terminal)
make dev-app     # build the alias .app and open it
make app         # build a standalone, distributable .app
make test        # run the test suite
make icon        # regenerate the app icon
```

The CLI also works standalone, which is handy for isolating problems:

```sh
.venv/bin/python -m aloud doctor          # which engine is active, and why
.venv/bin/python -m aloud warm            # pre-load the model
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
| `openai` | Cloud. Set `OPENAI_API_KEY` — note this uploads your audio |
| `mock` | Fixed text, for testing the loop without a model |

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
    → postprocess (fillers, dictionary, commands) → injector (paste into focused app)
```

Each stage is a module with one job, and the engine layer is an interface with
five implementations. The two defaults run **in-process and keep the model
resident**, so after a one-off warm-up at launch there's no process spawn and no
model reload per dictation — the two costs that dominated the latency budget.

[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) covers the reasoning: why a
different engine per architecture instead of one everywhere, why Python + PyObjC
rather than Swift, the threading rules, the latency budget, and what a Swift port
would replace.

## Verification status

Written and tested on Linux, which means:

* **Verified** — 65 unit tests pass, covering the hotkey state machine,
  post-processing, config merging, per-architecture engine selection and its
  fallback chain, both in-process engines' readiness reporting, both injection
  modes, and the full press → transcribe → deliver path against framework stubs.
  Every module compiles; every shell script passes `bash -n`.
* **Not yet verified** — anything that needs real hardware: the Quartz event tap
  against a physical keyboard, PortAudio capture, actually loading Parakeet or
  faster-whisper, the py2app build, code signing, and TCC permission prompts.
  The engine calls follow each library's documented API but have not been run.
  Start with `make doctor`, then `make warm`, then `make dev-app`.

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
  app.py          menu bar shell, state machine, job queue
  hotkey.py       Quartz event tap, push-to-talk edges
  audio.py        microphone capture → WAV
  engines/        Parakeet/MLX · faster-whisper · whisper.cpp · OpenAI API · mock
                  plus the per-architecture `auto` selection logic
  postprocess.py  fillers, dictionary, spoken commands
  injector.py     paste or type into the focused app
  permissions.py  TCC checks and settings deep links
scripts/          bootstrap · model download · icon · app build
docs/             architecture and decisions
tests/            unit tests + macOS framework stubs
```

## Licence

Not yet chosen.
