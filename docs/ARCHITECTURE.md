# Aloud — architecture

## What we're cloning

[Wispr Flow](https://wisprflow.ai/features) is a system-wide dictation app: you
hold a key, talk, release, and cleaned-up text appears in whatever app has
focus. The interesting part is not the speech recognition — it is everything
around it:

| Wispr Flow behaviour | Where it lives here |
| --- | --- |
| Hold a key anywhere to record | `hotkey.py` — a Quartz event tap |
| Works in *any* app, not just a browser | `injector.py` — pasteboard + synthetic Cmd-V |
| Strips fillers, fixes formatting | `postprocess.py` |
| Personal dictionary, voice shortcuts | `postprocess.py` (dictionary, commands) |
| "Under two seconds" end to end | the latency budget below |
| 100+ languages | engine-dependent; Whisper covers 99 |

The pieces Wispr charges for and we do not attempt in the skeleton are the
cloud LLM cleanup pass and the cross-device sync.

## The two constraints that decided everything

**Two architectures.** An Intel Mac and an M1, with no single runtime that is
best on both. Rather than settle for a lowest common denominator, the engine
layer picks per machine and `engine: "auto"` resolves it at startup:

* **Apple Silicon → [Parakeet](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
  on [MLX](https://github.com/senstella/parakeet-mlx).** Roughly an order of
  magnitude faster than Whisper large-v3-turbo for English, and its TDT decoder
  can emit a blank frame instead of being forced to produce a token — so it
  does not hallucinate text over silence the way Whisper does. MLX has no
  x86_64 build, so this path is arm64-only by construction.
* **Intel → [faster-whisper](https://github.com/SYSTRAN/faster-whisper).**
  CTranslate2's int8 CPU kernels are the best x86_64 option available, and it
  ships prebuilt wheels so nothing has to be compiled.
* **Either → [whisper.cpp](https://github.com/ggml-org/whisper.cpp)**, opt-in,
  as the last resort. It needs no Python ML stack at all, which makes it the
  thing that still works on a half-configured machine.

**No Xcode.** Worth separating two things that get conflated:

* **Xcode** (~10 GB, App Store) — not needed, and nothing here asks for it.
* **Xcode Command Line Tools** (~2 GB, `xcode-select --install`, free, no Apple
  Developer account) — needed by *every* path to a macOS app. It provides
  `clang`, `git`, `codesign`, `iconutil`. `bootstrap.sh` checks for it and
  triggers the installer.

Given both constraints, the shell is **Python 3 + PyObjC**, packaged with
py2app. The reasoning:

* PyObjC calls the same AppKit, Quartz, and AVFoundation APIs a Swift app
  would. This is a native macOS app that happens to be scripted — not a web
  view in a wrapper.
* No compile step to fight on two architectures. Every dependency ships
  prebuilt wheels; the only native binary is whisper.cpp, which Homebrew
  provides for both arm64 and x86_64.
* The latency-critical work is already native: the event tap is a C callback,
  audio capture is PortAudio, and inference is a separate C++ process. Python
  only ever moves buffers and strings between them.

### When to move to Swift

The seam is deliberate. `app.py` is the only file that knows about the menu
bar; everything below it is plain Python with no UI dependency. A Swift port
would replace `app.py`, `hotkey.py`, `injector.py`, and `feedback.py` — roughly
600 lines — and could keep the engine and post-processing logic behind a small
IPC boundary, or reimplement them directly.

Do it when one of these becomes true: you want to ship this to other people
under a Developer ID (notarization of Python bundles is genuinely painful), you
want a real preferences window rather than a JSON file, or you want the
in-place streaming overlay Wispr shows while you talk.

## Data flow

```
      ┌──────────────┐   key down/up    ┌──────────────┐
      │  Quartz      │─────────────────▶│  AloudApp   │   main thread
      │  event tap   │                  │  state       │   (AppKit run loop)
      └──────────────┘                  └──────┬───────┘
                                               │ start / stop
                                        ┌──────▼───────┐
                                        │  Recorder    │  PortAudio callback
                                        │  16k mono    │  thread
                                        └──────┬───────┘
                                               │ WAV path on a queue
      ┌──────────────┐                  ┌──────▼───────┐
      │ whisper.cpp  │◀─── subprocess ──│  worker      │  transcription thread
      │  / cloud API │                  │  thread      │
      └──────────────┘                  └──────┬───────┘
                                               │ raw text
                                        ┌──────▼───────┐
                                        │ postprocess  │  fillers, dictionary,
                                        └──────┬───────┘  spoken commands
                                        ┌──────▼───────┐
                                        │  injector    │  pasteboard + Cmd-V
                                        └──────────────┘
```

### Threading rules

1. The event tap callback runs on the main run loop. If it blocks, macOS
   **disables the tap** — so it only flips state and starts/stops the audio
   stream. (`hotkey.py` re-arms a disabled tap, but that costs a dropped
   keypress.)
2. Exactly one worker thread drains the job queue. Serialising means two quick
   dictations can never interleave their pastes into the same text field.
3. AppKit is not thread-safe. Every UI mutation goes through
   `mainthread.run_on_main`.

### Latency budget

Wispr's claim is under two seconds from key-release to text. Where it goes:

| Stage | Resident engine | whisper.cpp CLI |
| --- | --- | --- |
| Stop stream, write WAV | ~5 ms | ~5 ms |
| Process spawn | — | 50–150 ms |
| Model load | — *(paid once at startup)* | 0.1–1.5 s **every time** |
| Inference | 0.05–1 s | 0.2–3 s |
| Post-process + paste | ~40 ms | ~40 ms |

The single biggest design consequence of the Parakeet decision is the first two
rows. `parakeet-mlx` and `faster-whisper` are Python libraries, so the engine
holds the model **in-process and resident**: `TranscriptionEngine.warm_up()`
loads it once on a background thread at startup, and every dictation after that
skips both the process spawn and the model load entirely. whisper.cpp pays both
on every utterance, which is the real reason it is a fallback rather than the
default.

The cost is memory — a few GB held for the life of the app — and a warm-up
window at launch during which the first dictation still blocks on the load.

## Engine selection per machine

| | M1 | Intel |
| --- | --- | --- |
| Engine | `parakeet_mlx` | `faster_whisper` |
| Runtime | MLX (Metal + Neural Engine) | CTranslate2 (CPU, int8/AVX2) |
| Default model | `mlx-community/parakeet-tdt-0.6b-v3` | `base.en` |
| Model source | Hugging Face, ~2.4 GB | Hugging Face, ~150 MB |
| Languages | 25 European | 99 (multilingual variants) |
| Model residency | in-process | in-process |
| Extra requirements | Python 3.10+, ffmpeg | — |
| Escape hatch | `whisper_cpp`, or `openai` | `whisper_cpp`, or `openai` |

`engines.select()` implements this. It is deliberately asymmetric about
fallback: `auto` walks the preference list and skips anything that reports
itself unready, but an **explicitly named** engine is always honoured even when
broken. Silently substituting a different engine than the one someone asked for
would be worse than surfacing the error in the menu bar.

Both machines share one config schema, so a config file copied between them
works — `auto` resolves differently on each.

### Why not one engine everywhere?

It was the original design, and whisper.cpp is genuinely the best *single*
answer: Metal on arm64, AVX on x86_64, one binary. What it gives up is the
in-process model residency above, and on Apple Silicon it leaves most of the
available speed on the table. Since the two machines never have to agree, there
is no reason to make them.

## Permissions (TCC)

Two grants, both attached to an **app bundle identity** rather than to a
script:

* **Microphone** — prompted automatically on first record.
* **Accessibility** — must be added by hand in System Settings; there is no API
  to grant it. `permissions.py` detects the absence and opens the right pane.

This is why `make dev-app` exists. Running `make run` from a terminal attaches
both grants to *the terminal*, which works for development but means the grants
do not follow the app.

**Ad-hoc signing caveat.** `codesign --sign -` produces a fresh identity on
every build, so macOS treats each rebuild as a new app and drops the
Accessibility grant. To keep it: open Keychain Access → Certificate Assistant →
Create a Certificate, type **Code Signing**, name it e.g. `Aloud Dev`, then

```sh
CODESIGN_IDENTITY="Aloud Dev" make dev-app
```

## Packaging across two architectures

py2app bundles the interpreter it is built against, so the output matches the
build machine's architecture unless that interpreter is `universal2`. The
simplest approach — and the one the scripts assume — is to run
`./scripts/bootstrap.sh && make app` on each Mac. Two bundles, each native, no
Rosetta.

A single universal build is possible (python.org's universal2 installer plus
universal2 wheels for `cffi` and `sounddevice`) but the wheel availability is
the fragile part, so it is not the default.

## Deliberate non-goals in the skeleton

* **No streaming/partial results.** Whisper is not a streaming model; showing
  live partials means a different architecture (VAD-chunked rolling inference).
* **No LLM cleanup pass.** `postprocess.process` is the seam where it goes.
* **No notarization.** Needed only to distribute to other people.
* **No preferences window.** The config file is the UI for now.

## Roadmap

1. **Streaming warm-up feedback** — the menu bar should show model-loading
   progress at launch instead of looking idle, and queue a dictation that
   arrives mid-warm-up rather than blocking on it.
2. **A resident whisper.cpp** — a long-lived `whisper-server` engine, so the
   fallback path gets model residency too.
3. **Branding pass** — replace the procedural placeholder mark, add a template
   menu bar icon set with a proper recording state, and animate the state
   change.
4. **LLM cleanup step** — an optional post-process stage that fixes grammar and
   applies tone, matching what Wispr does server-side.
5. **Real preferences window** — hotkey picker, device picker, dictionary
   editor.
6. **Context awareness** — read the frontmost app via `NSWorkspace` and bias the
   prompt or the post-processing per app.
7. **Learned vocabulary** — mine `history.jsonl` for corrections and feed them
   into the dictionary automatically.
