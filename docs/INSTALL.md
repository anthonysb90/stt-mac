# Installing Aloud

Written for the two machines this was built for — an **Intel Mac** and an **M1
Mac** — and for someone who does not have Xcode and does not want it.

Most of this is identical on both. Where it differs, the difference is called
out and marked **M1 only** or **Intel only**. Do the whole thing once per Mac.

Rough time: 15 minutes on the Intel Mac, 20 on the M1 (the Parakeet model is a
2.4 GB download).

---

## The short version

Once you have the code on the Mac (step 4 below), this does everything else —
picks the right Python, installs the engine and model for your architecture,
builds the app, and puts it in `/Applications`:

```sh
./scripts/install.sh
```

It stops at the two macOS permissions, because nothing can grant those for you.
It opens the right Settings pane and tells you what to click.

The rest of this page is the same thing, step by step, for when you want to see
what is happening or something goes wrong.

---

## Before you start

**You need:**

| | Why | How long |
| --- | --- | --- |
| Xcode **Command Line Tools** | `git`, `clang`, `codesign`, `iconutil`. Not Xcode itself. | ~2 GB, 5–10 min |
| Homebrew | Installs `ffmpeg` and, optionally, a newer Python | 5 min |
| Python 3.10 or newer | **M1**: required by Parakeet. **Intel**: 3.9 is enough | already there, or 2 min |

**You do not need:** Xcode, an Apple Developer account, an internet connection
after setup (unless you choose the Deepgram engine), or any paid service.

> **A note on "no Xcode".** People conflate two things. **Xcode** is the ~10 GB
> IDE from the App Store — you never need it. The **Command Line Tools** are a
> separate free ~2 GB download that ships the compiler and `codesign`. Every
> route to a working macOS app needs those, including this one. The bootstrap
> script triggers the installer for you if they are missing.

### Which Mac am I on?

```sh
uname -m
```

* `arm64` → the **M1**. Aloud will use Parakeet on MLX.
* `x86_64` → the **Intel** Mac. Aloud will use faster-whisper on the CPU.

Nothing asks you to choose; `engine: "auto"` reads this and picks.

---

## Step 1 — Command Line Tools

```sh
xcode-select -p
```

If that prints a path (`/Library/Developer/CommandLineTools`), you already have
them; skip ahead. If it errors:

```sh
xcode-select --install
```

A system dialog appears. Click **Install**, accept the licence, and wait. When
it finishes, re-run `xcode-select -p` to confirm.

---

## Step 2 — Homebrew

```sh
which brew
```

If nothing prints, install it:

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

**Then read what it prints at the end.** On Apple Silicon it asks you to add
Homebrew to your `PATH` with two commands like these — run them, or `brew` will
not be found in new terminals:

```sh
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

On Intel, Homebrew installs to `/usr/local` and is usually already on `PATH`.

---

## Step 3 — Python

```sh
python3 --version
```

**M1 only.** Parakeet needs **3.10 or newer**, and macOS ships 3.9. If you see
3.9.x:

```sh
brew install python@3.12
```

Note the path it gives you — you will pass it to the bootstrap script in step 5:

```sh
$(brew --prefix)/bin/python3.12 --version
```

**Intel.** 3.9 is fine. Nothing to do.

---

## Step 4 — Get the code

```sh
mkdir -p ~/Developer && cd ~/Developer
git clone https://github.com/anthonysb90/stt-mac.git aloud
cd aloud
git checkout claude/whispr-dictation-app-glvd1v
```

Put it somewhere permanent. The app runs against this folder, so moving or
deleting it later breaks the app.

### If git asks you to log in

On a **public** repo it never should. Being asked means GitHub answered "404"
to an anonymous request — which happens both when a repo does not exist and
when it is private, because GitHub deliberately does not distinguish the two.
So: the repo is still private, or the URL has a typo.

Two ways past it, neither of which needs the terminal to authenticate:

**Download the ZIP instead.** Nothing here needs git history.

1. Open the branch in a browser you are already signed into GitHub with:
   `https://github.com/anthonysb90/stt-mac/tree/claude/whispr-dictation-app-glvd1v`
2. Green **Code** button → **Download ZIP**.
3. Then:

```sh
mkdir -p ~/Developer
cd ~/Downloads
unzip stt-mac-claude-whispr-dictation-app-glvd1v.zip
mv stt-mac-claude-whispr-dictation-app-glvd1v ~/Developer/aloud
cd ~/Developer/aloud
```

Everything from step 5 on works identically. The only thing you give up is
`git pull` for updates — download a fresh ZIP instead.

**Or make the repo genuinely public.** Repo → **Settings** → scroll to the
bottom → **Danger Zone** → **Change repository visibility** → **Change to
public**. It asks you to type the repository name and confirm on a second
screen; the change only lands after that second confirmation. Then plain
`git clone` works with no login at all.

> If you ever *do* get a username/password prompt from git, note that GitHub
> stopped accepting account passwords there in 2021. The "password" has to be a
> personal access token. Easier: `brew install gh && gh auth login`, which
> authenticates through the browser.

---

## Step 5 — Bootstrap

This installs everything: the virtualenv, the engine for this architecture, and
the model.

**On the Intel Mac:**

```sh
./scripts/bootstrap.sh
```

**On the M1**, if `python3 --version` said 3.9, point it at the newer one:

```sh
PYTHON_BIN=$(brew --prefix)/bin/python3.12 ./scripts/bootstrap.sh
```

If `python3 --version` already said 3.10+, plain `./scripts/bootstrap.sh` is fine.

What it does, in order:

1. checks the Command Line Tools;
2. checks the Python version and refuses early, with the fix, if it is too old;
3. creates `.venv`;
4. **M1 only** — installs `ffmpeg` via Homebrew (Parakeet decodes audio with it);
5. installs the Python dependencies — environment markers pull `parakeet-mlx`
   on arm64 and `faster-whisper` on x86_64, so the same command is correct on
   both;
6. downloads the model: **~2.4 GB on the M1**, **~150 MB on Intel**;
7. prints a diagnostic report.

The model download is the slow part. Let it finish.

### Check it worked

```sh
make doctor
```

You want to see your architecture, an `ok` beside the engine marked `(active)`,
and your microphone listed. Anything marked `--` on the active engine is a
problem; see [Troubleshooting](#troubleshooting).

**On the M1** the active engine should be `parakeet_mlx`. If it says `--` the
line tells you which of the three requirements is missing — Apple Silicon,
Python 3.10+, `parakeet-mlx` itself, or `ffmpeg`. Fix that one thing and re-run.

One thing worth knowing about the M1: `faster-whisper` is not installed there by
default, because Parakeet is both faster and better on that hardware. That
leaves the automatic fallback chain thin — if Parakeet cannot load, the next
option is whisper.cpp, which only exists if you bootstrapped with
`--with-whisper-cpp`. If you want a local second opinion that needs no Homebrew:

```sh
./.venv/bin/python -m pip install faster-whisper
```

It works on Apple Silicon, it just will not beat Parakeet. Purely optional.

---

## Step 6 — Make a signing certificate (2 minutes, do it before building)

Optional, but do it *now* rather than later. Without one the app is **ad-hoc
signed**, which gives it a new identity on every build: macOS treats each
rebuild as a different app and makes you grant Accessibility again from
scratch. With one, the permission you grant in step 8 survives every rebuild.

1. Open **Keychain Access** (Spotlight it).
2. Menu: **Keychain Access → Certificate Assistant → Create a Certificate…**
3. Name: `Aloud Dev`. Identity Type: **Self Signed Root**. Certificate Type:
   **Code Signing**. Click **Create**, then **Done**.

Then make your shell use it, so you never have to remember:

```sh
echo 'export CODESIGN_IDENTITY="Aloud Dev"' >> ~/.zshrc
source ~/.zshrc
```

Skipping this is fine — everything still works, you just re-grant Accessibility
after each rebuild.

---

## Step 7 — Build the app

```sh
make install
```

It removes any previous copy first, builds, signs, installs, and then **starts
the bundle to prove it works** before saying it succeeded.

> **Never `cp -R` a bundle into `/Applications` by hand.** If `Aloud.app` is
> already there, `cp -R dist/Aloud.app /Applications/` copies the new one
> *inside* the old one, leaving `/Applications/Aloud.app/Aloud.app`. Finder
> opens the outer, stale bundle. `make install` uses `ditto`, which does not
> have that behaviour.

This builds `Aloud.app` and copies it into `/Applications`. It is a normal Mac
app: Dock icon, application menu, a window you can close and reopen.

The build ends by starting the bundle once and failing loudly if it cannot —
so a broken build tells you why here, rather than as a "Launch error" dialog
later.

> **The app runs against `~/Developer/aloud/.venv`.** Keep that folder where it
> is; moving or deleting it breaks the app. This is deliberate: the engines are
> MLX (with its Metal shader libraries) or CTranslate2, and py2app has no recipe
> for bundling either. `make app-standalone` attempts a self-contained bundle if
> you want to try, but expect it to fail.

<details>
<summary>Developing on it instead?</summary>

`make dev-app` builds a bundle that links back to your checkout, so edits take
effect without rebuilding. Use that while changing code, and `make install` when
you want the real thing. Don't keep a dev build in `/Applications` — it breaks
if you move the checkout.
</details>

## Step 8 — Grant the two permissions

Aloud needs both, and macOS will not let an app grant itself either.

### Microphone — asked for automatically

Open Aloud (Launchpad, or Spotlight "Aloud"). Press the **Start Dictation**
button once. macOS asks for microphone access — click **Allow**.

If you miss the prompt: **System Settings → Privacy & Security → Microphone**,
switch **Aloud** on.

### Accessibility — you must add it by hand

This is the one that catches people. Aloud needs it to see the hotkey
system-wide and to paste into other apps. **There is no API for an app to grant
itself this**, so:

1. **System Settings → Privacy & Security → Accessibility**
2. Click the **+** button.
3. Navigate to **Applications**, select **Aloud**, click **Open**.
4. Make sure the switch beside it is **on**.
5. **Quit Aloud completely (⌘Q) and open it again.** It only picks up the new
   permission at launch.

Aloud will prompt you and open that pane for you on first launch, but you still
have to do steps 2–5 yourself.

---

## Step 9 — Your first dictation

1. Open any app you can type in — Notes, Mail, a browser.
2. Click into a text field.
3. **Hold Right Option**, say a sentence, **release**.

The menu bar glyph goes `◌` → `●` while recording → `◍` while transcribing, and
your text appears where the cursor is.

> **Why Right Option and not Fn?** Right Option does nothing on its own. Fn is
> the more Wispr-like choice, but macOS gives it a built-in action — if you want
> it, first set **System Settings → Keyboard → Press 🌐 key to → Do Nothing**, or
> it will open the emoji picker every time you dictate. Change the key in
> **Aloud → Settings** (⌘,).

The first dictation after launch is slower than the rest: that is the model
loading. After that it stays resident.

---

## Sounds

The start and stop cues are the only feedback you get while you are looking at
another app, so they are worth setting once. **Settings → Sounds** picks them
from everything your Mac has, and plays each one as you select it.

Defaults are **Bottle** (soft rising bloop) to open and **Glass** (bright
chime) to close. Basso, Funk and Sosumi are alert sounds — they will read as
something going wrong.

---

## If the bundle will not behave

There is a path with no app bundle in it at all:

```sh
make login-item
```

That installs a LaunchAgent running Aloud straight from this folder at login —
the same command `make run` uses, minus the Terminal window. Nothing to go
wrong at launch, because there is no bundle to go wrong.

The trade is where macOS attaches permissions. With a bundle they attach to
`Aloud.app`; here they attach to the Python binary in `.venv`, so that is what
you add under Accessibility (press ⌘⇧G in the picker and paste the path the
script prints). It works and it is stable, but the entry in the list will say
"Python" — which is why the bundle is the better answer when it works.

`make login-item-remove` undoes it.

---

## Step 10 — Keep it running

Aloud lives in the menu bar as a microphone icon. Closing the window does not
quit it — the hotkey keeps working — so the only thing left is making sure it
starts with the Mac:

**System Settings → General → Login Items → Open at Login → +** → choose
**Aloud**.

After that you never think about it again: hold the key in any app, speak,
release.

### Menu bar only, no Dock icon

If you would rather Aloud were invisible except for the menu bar icon:

**Settings (`⌘,`) → Appearance → uncheck *Show Aloud in the Dock***, or click
the menu bar icon and untick **Show in Dock**. It applies immediately.

What you are turning off is more than the Dock icon. macOS switches the app to
its *accessory* policy, which also removes the application menu — so `⌘,` and
`⌘Q` stop working, and Aloud no longer appears in `⌘Tab`. Everything stays
reachable from the menu bar icon, which carries **Open Aloud**, **Settings…**
and **Quit Aloud**. With the Dock icon off the main window also stops opening
at launch, which is the point: it starts quietly and waits for the hotkey.

To undo it, click the menu bar icon and tick **Show in Dock** again.

---

## Transcribing a file

**File → Transcribe Audio File…** (`⌘O`). Takes mp3, m4a, wav, flac, and the
audio track of a video. The transcript opens in its own window with Copy and
Save buttons, and lands on the clipboard.

It is not typed into the app you had open — an hour of audio has no business
being pasted into whatever document happens to be in front of you.

Files that are not already 16 kHz mono WAV are converted with ffmpeg. On Intel
that is not installed by default; `brew install ffmpeg` if you want anything
other than WAV.

---

## Optional — Deepgram for cleanup

Deepgram does transcription *and* cleanup in one call: punctuation,
capitalisation, filler removal, spoken punctuation ("period", "new line"),
numbers as digits. It also has **keyterm prompting**, which is a stronger way to
push your Dictionary words at the model than a Whisper prompt.

It uploads your audio, so it is never chosen automatically.

1. Get a key from [deepgram.com](https://deepgram.com) (there is a free tier).
2. Open **Aloud → Settings** (`⌘,`), set **Engine** to **Deepgram (cloud)**,
   paste the key into **API key**, and press **Save Key**.

That is the whole thing — no terminal. The field is masked, and the key is
written to `~/Library/Application Support/Aloud/keys/deepgram.key`, readable
only by you.

> **Why not just export it in `.zshrc`?** An app launched from the Dock does not
> inherit your shell environment, so a key set that way works in Terminal and
> silently fails in the app. Settings writes it somewhere the app can actually
> read.

There is a command-line equivalent if you prefer it —
`.venv/bin/aloud key deepgram` — but nothing needs it.

Aloud turns off its own filler-stripping and punctuation when Deepgram is
active, so the two never fight over the same text. Your Dictionary corrections
still run locally, because those are the guaranteed path.

To go back to local transcription, set **Engine** to **Automatic**.

---

## Updating

```sh
cd ~/Developer/aloud
git pull
./scripts/bootstrap.sh     # picks up any new dependencies
make install
```

If you set up the signing certificate, permissions carry over. If not, re-do
step 8's Accessibility part: remove Aloud from the list with **−**, then add it
again.

---

## Uninstalling

```sh
rm -rf /Applications/Aloud.app
rm -rf ~/Library/Application\ Support/Aloud    # config, dictionary, history, keys
rm -rf ~/Library/Logs/Aloud
rm -rf ~/Library/Caches/Aloud
rm -rf ~/Developer/aloud                       # the checkout
```

Also remove Aloud from **Privacy & Security → Accessibility** and **Microphone**.

Models live outside the app: `~/.cache/huggingface` (Parakeet, faster-whisper).
Delete that too if you want the disk space back and use nothing else that needs
it.

---

## Where things live

| | |
| --- | --- |
| App | `/Applications/Aloud.app` |
| Settings | `~/Library/Application Support/Aloud/config.json` |
| Dictionary | `~/Library/Application Support/Aloud/dictionary.json` |
| History | `~/Library/Application Support/Aloud/history.jsonl` |
| API keys | `~/Library/Application Support/Aloud/keys/` |
| Log | `~/Library/Logs/Aloud/aloud.log` |
| Models | `~/.cache/huggingface` |

Every one of those is a plain file you can open, read, and edit.

---

## Troubleshooting

Run this first — it answers most of these:

```sh
cd ~/Developer/aloud && make doctor
```

**The hotkey does nothing.**
Accessibility is not granted, or was granted to a previous build. Menu bar →
Aloud → check Settings, then redo step 8 — including quitting and reopening.
After an ad-hoc-signed rebuild you must remove Aloud from the Accessibility list
with **−** and add it again; toggling the switch is not enough.

**"Launch error — see the py2app website for debugging launch issues."**
That dialog is a Python traceback that got thrown away. Get the real one:

```sh
cd ~/Developer/aloud && make diagnose
```

That runs the app's binary directly, so the traceback prints in your terminal.
Send me that output and the fix is usually immediate.

**Nothing happens at all, no menu bar icon.**
`make diagnose` first, then `tail -f ~/Library/Logs/Aloud/aloud.log`.

**Is it the bundle or the code?**
`make run` runs the same app straight from the checkout. If that works and the
bundle doesn't, it's a packaging problem; if both fail the same way, it's the
code.

**"parakeet-mlx is not installed" on the M1.**
Almost always Python: it needs 3.10+ and macOS ships 3.9. Redo step 3, then
re-run bootstrap with `PYTHON_BIN=` pointing at the newer Python.

**"ffmpeg not found".**
`brew install ffmpeg`. Parakeet decodes audio through it.

**The first dictation after opening the app is slow.**
That is the model loading, once. `make warm` pays it up front. The engine line
in Settings says `loaded` when it is done.

**It is slow on the Intel Mac every time.**
Expected — there is no GPU path there. In **Settings**, set the model to
`tiny.en`, or switch that Mac to Deepgram.

**Text appears in the wrong app.**
Aloud pastes into whatever has focus when transcription finishes. Don't click
away while the glyph is `◍`.

**My clipboard got replaced.**
It is put back about a second after pasting. If something else writes to the
clipboard in that window, Aloud leaves it alone rather than overwriting.

**Deepgram says "no API key" even though I exported it.**
See the warning in the Deepgram section — use `aloud key deepgram`.

**Deepgram returns a 400 about keyterms.**
Keyterm prompting needs a `nova-3` model and a single language. In Settings, set
the model to `nova-3`, or turn off biasing in `config.json`
(`dictionary.bias.enabled`).

**A word keeps coming out wrong.**
That is what the Dictionary is for: **⌘2**, then **Add Word** for a name it
should know, or **Add Correction** for a phrase it always mishears. If the
entry looks like it would rewrite an everyday word, the editor warns you before
you save it.
