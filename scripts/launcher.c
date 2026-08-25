/*
 * scripts/launcher.c -> compiled to Contents/MacOS/Aloud, linked directly
 * against Python and calling Py_BytesMain() in-process.
 *
 * The previous design here exec'd Contents/Resources/venv/bin/python. That
 * broke Accessibility in a way that was only found by direct testing on
 * hardware: NSBundle.mainBundle() requires the *running* executable's own
 * path to sit exactly at Contents/MacOS/<CFBundleExecutable>, not merely
 * somewhere inside the .app. execv() replaces the process image -- the
 * running binary becomes .../Contents/Resources/venv/bin/python, which
 * fails that check, so NSBundle fell back to an unrelated bundle (Homebrew's
 * own internal Python.framework helper app). TCC evaluates whichever binary
 * is actually running when CGEventTapCreate is called, using *that*
 * process's own reported identity -- so no Accessibility grant made for
 * "Aloud" could ever apply to it, no matter what System Settings showed.
 *
 * Py_BytesMain() -- https://docs.python.org/3/c-api/veryhigh.html#c.Py_BytesMain
 * -- is the exact function the standard `python` executable's own main()
 * calls. Linking against it directly and calling it here means this
 * process's own binary never changes: it stays Contents/MacOS/Aloud for the
 * app's entire lifetime, so NSBundle.mainBundle() finds the real bundle
 * (this one) and TCC evaluates the right identity.
 *
 * This also means the .venv no longer needs to be copied into the bundle
 * (the previous design's `cp -RL .venv`, dereferencing every symlink so
 * codesign had nothing bundle-internal to object to). That copy existed to
 * avoid a symlink *inside the bundle*; dynamically linking against an
 * external framework isn't a bundle-internal symlink at all -- codesign's
 * "invalid destination for symbolic link in bundle" rule never applied to
 * it in the first place. ALOUD_PYTHONHOME and ALOUD_PYTHONPATH (in the
 * generated build/aloud_embed_config.h -- see scripts/python_embed_flags.py)
 * point at Homebrew's real install and this checkout's venv/src directly,
 * read as ordinary environment variables at runtime -- invisible to
 * codesign, same as pyvenv.cfg pointing outward already was.
 */

#include <Python.h>
#include <stdio.h>
#include <stdlib.h>

#include "aloud_embed_config.h"

int main(int argc, char *argv[]) {
    if (setenv("PYTHONHOME", ALOUD_PYTHONHOME, 1) != 0
        || setenv("PYTHONPATH", ALOUD_PYTHONPATH, 1) != 0
        || setenv("PYTHONUTF8", "1", 1) != 0) {
        fprintf(stderr, "Aloud: could not set up the Python environment\n");
        return 1;
    }

    /* new argv: argv[0], -m, aloud, <forwarded args from argv[1..]>, NULL --
       identical to `python -m aloud <args>`, which is exactly what `make
       run` already invokes successfully. */
    int new_argc = argc + 2;
    char **new_argv = malloc(sizeof(char *) * (size_t)(new_argc + 1));
    if (new_argv == NULL) {
        fprintf(stderr, "Aloud: out of memory\n");
        return 1;
    }
    new_argv[0] = argv[0];
    new_argv[1] = "-m";
    new_argv[2] = "aloud";
    for (int i = 1; i < argc; i++) {
        new_argv[2 + i] = argv[i];
    }
    new_argv[new_argc] = NULL;

    return Py_BytesMain(new_argc, new_argv);
}
