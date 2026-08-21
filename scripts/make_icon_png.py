#!/usr/bin/env python3
"""Draw the placeholder Aloud app mark as a 1024x1024 PNG.

Pure standard library: no Pillow, no design tool, nothing to install. The mark
is a rounded square with a vertical gradient and a five-bar waveform, drawn at
4x and downsampled so the edges are smooth.

This is scaffolding for the real branding pass, not the final artwork -- but it
is a real icon, so the bundle looks like an app from the first build.
"""

from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path

SUPERSAMPLE = 4

# Brand palette. One place to change when the real identity lands.
GRADIENT_TOP = (0x4C, 0x3A, 0xE0)
GRADIENT_BOTTOM = (0x8B, 0x3F, 0xD9)
MARK = (0xFF, 0xFF, 0xFF)

#: Relative bar heights, centred. Reads as a voice waveform even at 16 px.
BAR_HEIGHTS = (0.34, 0.62, 1.00, 0.62, 0.34)


def _rounded_square(x: float, y: float, size: float, radius: float) -> bool:
    """Point-in-squircle test in unit-ish coordinates."""
    inset = size
    cx = min(max(x, radius), inset - radius)
    cy = min(max(y, radius), inset - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius


def _rounded_bar(x: float, y: float, left: float, top: float,
                 width: float, height: float, radius: float) -> bool:
    if not (left <= x <= left + width and top <= y <= top + height):
        return False
    cx = min(max(x, left + radius), left + width - radius)
    cy = min(max(y, top + radius), top + height - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius


def render(size: int) -> bytes:
    """Return raw RGBA rows for an icon of ``size`` pixels."""
    hi = size * SUPERSAMPLE
    radius = hi * 0.225
    bar_width = hi * 0.082
    gap = hi * 0.052
    total_width = len(BAR_HEIGHTS) * bar_width + (len(BAR_HEIGHTS) - 1) * gap
    first_left = (hi - total_width) / 2.0
    max_bar_height = hi * 0.46
    bar_radius = bar_width / 2.0

    bars = []
    for index, factor in enumerate(BAR_HEIGHTS):
        height = max_bar_height * factor
        bars.append(
            (
                first_left + index * (bar_width + gap),
                (hi - height) / 2.0,
                bar_width,
                height,
            )
        )

    rows = []
    for out_y in range(size):
        row = bytearray()
        for out_x in range(size):
            r = g = b = a = 0
            for sub_y in range(SUPERSAMPLE):
                y = out_y * SUPERSAMPLE + sub_y + 0.5
                for sub_x in range(SUPERSAMPLE):
                    x = out_x * SUPERSAMPLE + sub_x + 0.5
                    if not _rounded_square(x, y, hi, radius):
                        continue
                    t = y / hi
                    pr = int(GRADIENT_TOP[0] + (GRADIENT_BOTTOM[0] - GRADIENT_TOP[0]) * t)
                    pg = int(GRADIENT_TOP[1] + (GRADIENT_BOTTOM[1] - GRADIENT_TOP[1]) * t)
                    pb = int(GRADIENT_TOP[2] + (GRADIENT_BOTTOM[2] - GRADIENT_TOP[2]) * t)
                    for left, top, width, height in bars:
                        if _rounded_bar(x, y, left, top, width, height, bar_radius):
                            pr, pg, pb = MARK
                            break
                    r += pr
                    g += pg
                    b += pb
                    a += 255
            samples = SUPERSAMPLE * SUPERSAMPLE
            if a:
                covered = a / 255
                row += bytes((r // int(covered), g // int(covered), b // int(covered),
                              a // samples))
            else:
                row += b"\x00\x00\x00\x00"
        rows.append(bytes(row))
    return b"".join(b"\x00" + row for row in rows)


def write_png(path: Path, size: int) -> None:
    raw = render(size)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("assets/Aloud-1024.png"))
    parser.add_argument("--size", type=int, default=1024)
    args = parser.parse_args()
    write_png(args.out, args.size)
    print(f"wrote {args.out} ({args.size}x{args.size})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
