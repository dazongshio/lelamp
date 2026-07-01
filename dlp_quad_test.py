#!/usr/bin/env python3
import mmap
import os
import struct

WIDTH = 640
HEIGHT = 360
STRIDE = 2560
FB = "/dev/fb0"


def pixel(r, g, b):
    return struct.pack("<BBBB", b, g, r, 0)


RED = pixel(255, 0, 0)
GREEN = pixel(0, 255, 0)
BLUE = pixel(0, 0, 255)
WHITE = pixel(255, 255, 255)
BLACK = pixel(0, 0, 0)


def main():
    fd = os.open(FB, os.O_RDWR)
    try:
        fb = mmap.mmap(fd, STRIDE * HEIGHT, mmap.MAP_SHARED, mmap.PROT_WRITE | mmap.PROT_READ)
        for y in range(HEIGHT):
            for x in range(WIDTH):
                if abs(x - WIDTH // 2) < 3 or abs(y - HEIGHT // 2) < 3:
                    px = BLACK
                elif x < WIDTH // 2 and y < HEIGHT // 2:
                    px = RED
                elif x >= WIDTH // 2 and y < HEIGHT // 2:
                    px = GREEN
                elif x < WIDTH // 2 and y >= HEIGHT // 2:
                    px = BLUE
                else:
                    px = WHITE
                off = y * STRIDE + x * 4
                fb[off:off + 4] = px
        fb.flush()
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
