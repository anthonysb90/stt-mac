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

**Two architectures.** An Intel Mac and an M1. That rules out the fastest local
stacks — MLX, Parakeet-MLX, CoreML/ANE-only builds — because they are Apple
Silicon only and would leave the Intel machine with no engine at all.
[whisper.cpp](https://github.com/ggml-org/whisper.cpp) is the one local runtime
that runs well on both: Metal and the Neural Engine on arm64, AVX and Accelerate
on x86_64. It is what the engine layer targets first.

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

| Stage | Cost | Notes |
| --- | --- | --- |
| Stop stream, write WAV | ~5 ms | recording is buffered in RAM |
| Process spawn | ~50–150 ms | the price of shelling out to the CLI |
| Model load | 0.1–1.5 s | **the dominant cost**; see below |
| Inference | 0.2–3 s | scales with model size and audio length |
| Post-process + paste | ~40 ms | |

Model load per invocation is the obvious thing to fix next: a long-lived
`whisper-server` process, or Python bindings that keep the model resident,
removes it entirely. The engine interface was drawn so that is a new class in
`engines/`, not a rewrite.

## Engine selection per machine

| | M1 | Intel |
| --- | --- | --- |
| Acceleration | Metal + Neural Engine | CPU (AVX / Accelerate) |
| Default model | `small.en` | `base.en` |
| Expected feel | comfortably faster than real time | usable, noticeably slower |
| Fallback worth having | — | the `openai` cloud engine |

`bootstrap.sh` reads `uname -m` and downloads the matching model. Both machines
share the same config schema, so a config file copied between them works.

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

1. **Kill the per-dictation model load** — persistent `whisper-server` engine.
2. **Branding pass** — replace the procedural placeholder mark, add a template
   menu bar icon set with a proper recording state, and animate the state
   change.
3. **LLM cleanup step** — an optional post-process stage that fixes grammar and
   applies tone, matching what Wispr does server-side.
4. **Real preferences window** — hotkey picker, device picker, dictionary
   editor.
5. **Context awareness** — read the frontmost app via `NSWorkspace` and bias the
   prompt or the post-processing per app.
6. **Learned vocabulary** — mine `history.jsonl` for corrections and feed them
   into the dictionary automatically.
