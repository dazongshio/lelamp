#!/usr/bin/env python3
import mmap
import os
import struct

WIDTH = 640
HEIGHT = 360
STRIDE = 2560
FB = "/dev/fb0"


def px(r, g, b):
    return struct.pack("<BBBB", b, g, r, 0)


BLACK = px(0, 0, 0)

# Order follows the Raspberry Pi DPI data pins in the current RGB666 mode:
# GPIO4-9   = framebuffer B2-B7
# GPIO10-15 = framebuffer G2-G7
# GPIO16-21 = framebuffer R2-R7
BARS = []
for bit in range(2, 8):
    BARS.append((f"B{bit}", 0, 0, 1 << bit))
for bit in range(2, 8):
    BARS.append((f"G{bit}", 0, 1 << bit, 0))
for bit in range(2, 8):
    BARS.append((f"R{bit}", 1 << bit, 0, 0))


def main():
    bar_w = WIDTH // len(BARS)
    fd = os.open(FB, os.O_RDWR)
    try:
        fb = mmap.mmap(fd, STRIDE * HEIGHT, mmap.MAP_SHARED, mmap.PROT_WRITE | mmap.PROT_READ)
        for y in range(HEIGHT):
            for x in range(WIDTH):
                idx = min(x // bar_w, len(BARS) - 1)
                local_x = x - idx * bar_w
                if local_x < 2 or local_x >= bar_w - 2:
                    color = BLACK
                else:
                    _, r, g, b = BARS[idx]
                    color = px(r, g, b)
                off = y * STRIDE + x * 4
                fb[off:off + 4] = color
        fb.flush()
    finally:
        os.close(fd)

    print("Bar order left to right:")
    print(" ".join(name for name, *_ in BARS))
    print("Expected projection: 6 blue bars, 6 green bars, 6 red bars.")


if __name__ == "__main__":
    main()
