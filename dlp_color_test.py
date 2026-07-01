#!/usr/bin/env python3
import mmap
import os
import struct
import sys
import time

WIDTH = 640
HEIGHT = 360
STRIDE = 2560
FB = "/dev/fb0"

COLORS = {
    "white": (255, 255, 255),
    "gray": (128, 128, 128),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "black": (0, 0, 0),
}


def fill(color):
    r, g, b = COLORS[color]
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
    if len(sys.argv) == 2:
        color = sys.argv[1].lower()
        if color not in COLORS:
            raise SystemExit(f"Unknown color: {color}. Use one of: {', '.join(COLORS)}")
        fill(color)
        return

    sequence = ["white", "red", "green", "blue", "gray", "black"]
    for color in sequence:
        print(color, flush=True)
        fill(color)
        time.sleep(3)


if __name__ == "__main__":
    main()
