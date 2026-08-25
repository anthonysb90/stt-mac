"""Replace a bundle's outward symlinks with real files, so it can be signed.

codesign rejects any symlink whose destination lies outside the bundle::

    dist/Aloud.app: invalid destination for symbolic link in bundle

A py2app *alias* build is made of exactly those links -- that is what makes it
an alias build: the bundle points at the virtualenv and at Homebrew rather than
carrying copies. The result runs perfectly and cannot be validly signed, which
would be a fair trade if macOS did not refuse to hold an Accessibility grant
against a signature that fails to verify. It does refuse, so the links have to
go before signing.

Three cases, and they need different answers:

* a link pointing *inside* the bundle -- fine, left alone;
* a link pointing *outside* -- replaced with a copy of what it pointed at;
* a link pointing at nothing -- removed, since it cannot be copied and cannot
  be verified either.

Run it after building and before signing. Reports every change, because a
build step that quietly rearranges a bundle is the kind of thing that costs an
afternoon when it turns out to be wrong.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def symlinks(bundle: Path):
    """Every symlink in the bundle, deepest first.

    Deepest first so that replacing a directory link does not invalidate paths
    already collected beneath it.
    """
    found = []
    for root, dirs, files in os.walk(bundle, followlinks=False):
        for name in dirs + files:
            path = Path(root) / name
            if path.is_symlink():
                found.append(path)
    return sorted(found, key=lambda p: len(p.parts), reverse=True)


def flatten(
    bundle: Path,
    dry_run: bool = False,
    delete: list | None = None,
    copy_outward: bool = False,
) -> int:
    bundle = bundle.resolve()
    if not bundle.is_dir():
        print(f"error: {bundle} is not a directory", file=sys.stderr)
        return 2

    copied = removed = kept = 0
    total_bytes = 0

    delete = [d.strip("/") for d in (delete or [])]

    for link in symlinks(bundle):
        shown = link.relative_to(bundle)
        target_text = os.readlink(link)

        if str(shown) in delete:
            # Some links cannot be copied into a real file and still work. A
            # virtualenv's `python` is one: it locates its standard library by
            # resolving its own symlink, so a copy of it looks inside the
            # bundle, finds no stdlib, and dies with "No module named
            # encodings". Removing is the only other option that leaves a
            # signable bundle.
            print(f"  removing (unsafe to copy)  {shown} -> {target_text}")
            if not dry_run:
                link.unlink()
            removed += 1
            continue

        if not link.exists():  # follows the link; False when it dangles
            print(f"  removing dangling  {shown} -> {target_text}")
            if not dry_run:
                link.unlink()
            removed += 1
            continue

        target = Path(os.path.realpath(link))
        try:
            target.relative_to(bundle)
        except ValueError:
            pass  # outside the bundle: must be copied
        else:
            kept += 1
            continue

        if not copy_outward:
            # Left in place: copying one of these broke the app twice. See the
            # note in install_app.sh.
            kept += 1
            continue

        size = tree_size(target)
        total_bytes += size
        print(f"  copying in         {shown} -> {target_text}  ({human(size)})")
        if dry_run:
            copied += 1
            continue

        link.unlink()
        if target.is_dir():
            shutil.copytree(target, link, symlinks=False)
        else:
            shutil.copy2(target, link)
        copied += 1

    verb = "would copy" if dry_run else "copied"
    print(
        f"  {verb} {copied} outward link(s) ({human(total_bytes)}), "
        f"removed {removed} dangling, kept {kept} internal"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--copy-outward", action="store_true",
        help="replace links leaving the bundle with real copies (breaks alias "
             "builds; see install_app.sh)",
    )
    parser.add_argument(
        "--delete", action="append", default=[],
        metavar="REL/PATH",
        help="bundle-relative symlink to remove rather than copy; repeatable",
    )
    args = parser.parse_args()
    return flatten(args.bundle, args.dry_run, args.delete, args.copy_outward)


if __name__ == "__main__":
    raise SystemExit(main())
