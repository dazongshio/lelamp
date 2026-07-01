#!/usr/bin/env python3
import mmap
import os
import struct
import sys

WIDTH = 640
HEIGHT = 360
STRIDE = 2560
FB = "/dev/fb0"


def write_color(r, g, b):
    px = struct.pack("<BBBB", b, g, r, 0)
    row = px * WIDTH
    fd = os.open(FB, os.O_RDWR)
    try:
        fb = mmap.mmap(fd, STRIDE * HEIGHT, mmap.MAP_SHARED, mmap.PROT_WRITE | mmap.PROT_READ)
        for y in range(HEIGHT):
            fb[y * STRIDE:y * STRIDE + len(row)] = row
        fb.flush()
    finally:
        os.close(fd)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: ./dpi_bit_test.py <r|g|b> <bit 0..7>")

    channel = sys.argv[1].lower()
    bit = int(sys.argv[2], 0)
    if channel not in ("r", "g", "b") or not 0 <= bit <= 7:
        raise SystemExit("Usage: ./dpi_bit_test.py <r|g|b> <bit 0..7>")

    value = 1 << bit
    r = value if channel == "r" else 0
    g = value if channel == "g" else 0
    b = value if channel == "b" else 0
    write_color(r, g, b)
    print(f"{channel.upper()} bit {bit}: RGB=({r},{g},{b})")


if __name__ == "__main__":
    main()
