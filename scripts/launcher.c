/*
 * scripts/launcher.c -> compiled to Contents/MacOS/Aloud
 *
 * The whole job: find this binary's own path, derive the bundled venv's
 * python from it, and execv() straight into `python -m aloud <args>`.
 *
 * Why a trampoline instead of embedding Python (the old py2app approach):
 * py2app's alias build symlinks Contents/Frameworks/Python.framework and
 * Contents/MacOS/python back out to the checkout and to Homebrew. codesign
 * refuses any symlink whose destination leaves the bundle ("invalid
 * destination for symbolic link in bundle"), so that bundle can never
 * verify -- and macOS will not hold an Accessibility grant against a
 * signature that does not verify, confirmed directly on hardware, not just
 * theorized. Neither copying nor deleting those specific symlinks works:
 * CPython finds its standard library by resolving its own executable's
 * symlink at startup, so a copy (no longer a symlink) breaks that lookup,
 * and py2app's own stub reads that path at launch and crashes if it is
 * simply gone.
 *
 * This sidesteps the whole problem: install_app.sh copies the entire
 * .venv into Contents/Resources/venv, dereferencing every symlink along
 * the way (`cp -RL`), so the copy is a complete, ordinary venv -- no
 * symlinks anywhere, nothing for codesign to object to. This launcher's
 * only job is to exec that copy.
 *
 * The running process becomes that copied python (re-signed with this same
 * identity -- see install_app.sh), so the Accessibility grant attaches to
 * it directly. Its path stays inside Aloud.app the whole time, so
 * NSBundle.mainBundle() still finds this bundle's Info.plist and icon --
 * unlike exec-ing an external interpreter, which would leave the running
 * process with no bundle association at all (the same reason a bare
 * `python -m aloud run` shows a generic icon instead of Aloud's).
 */

#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void die(const char *what) {
    fprintf(stderr, "Aloud: %s: %s\n", what, strerror(errno));
    exit(1);
}

int main(int argc, char *argv[]) {
    char self_path[PATH_MAX];
    uint32_t size = sizeof(self_path);
    if (_NSGetExecutablePath(self_path, &size) != 0) {
        fprintf(stderr, "Aloud: could not determine my own path (buffer too small)\n");
        return 1;
    }

    char resolved[PATH_MAX];
    if (realpath(self_path, resolved) == NULL) {
        die("resolving my own path");
    }

    /* resolved == ".../Aloud.app/Contents/MacOS/Aloud" --
       strip "/MacOS/Aloud" off the end, in two steps, to land on Contents/. */
    char *last_slash = strrchr(resolved, '/');
    if (last_slash == NULL) {
        fprintf(stderr, "Aloud: unexpected path: %s\n", resolved);
        return 1;
    }
    *last_slash = '\0'; /* ".../Aloud.app/Contents/MacOS" */

    last_slash = strrchr(resolved, '/');
    if (last_slash == NULL) {
        fprintf(stderr, "Aloud: unexpected path: %s\n", resolved);
        return 1;
    }
    *last_slash = '\0'; /* ".../Aloud.app/Contents" */

    char python_path[PATH_MAX];
    int n = snprintf(python_path, sizeof(python_path), "%s/Resources/venv/bin/python", resolved);
    if (n < 0 || (size_t)n >= sizeof(python_path)) {
        fprintf(stderr, "Aloud: bundle path too long\n");
        return 1;
    }

    if (access(python_path, X_OK) != 0) {
        fprintf(stderr, "Aloud: no interpreter at %s: %s\n", python_path, strerror(errno));
        return 1;
    }

    /* new argv: python, -m, aloud, <forwarded args from argv[1..]>, NULL */
    char **new_argv = malloc(sizeof(char *) * (size_t)(argc + 3));
    if (new_argv == NULL) {
        die("allocating argv");
    }
    new_argv[0] = python_path;
    new_argv[1] = "-m";
    new_argv[2] = "aloud";
    for (int i = 1; i < argc; i++) {
        new_argv[2 + i] = argv[i];
    }
    new_argv[argc + 2] = NULL;

    execv(python_path, new_argv);
    /* execv only returns on failure */
    die("exec failed");
    return 1;
}
