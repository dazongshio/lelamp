#!/usr/bin/env python3
import argparse
import os
import select
import sys
import termios
import time

DEFAULT_DEVICE = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
DEFAULT_BAUD = 9600

COMMANDS = {
    "power-toggle": "ff 07 99 00 00 00 00 a0",
    "restore": "ff 07 77 00 00 00 00 7e",
    "flip-v": "ff 07 37 01 00 00 00 3f",
    "flip-h": "ff 07 37 02 00 00 00 40",
    "flip-both": "ff 07 37 00 00 00 00 3e",
    "flip-none": "ff 07 37 03 00 00 00 41",
}

SHARPNESS = {
    0: "ff 07 31 00 00 00 00 38",
    1: "ff 07 31 01 00 00 00 39",
    2: "ff 07 31 02 00 00 00 3a",
    3: "ff 07 31 03 00 00 00 3b",
    4: "ff 07 31 04 00 00 00 3c",
    5: "ff 07 31 05 00 00 00 3d",
    6: "ff 07 31 06 00 00 00 3e",
}

BRIGHTNESS = {
    -31: "ff 07 29 e1 00 00 00 11",
    0: "ff 07 29 00 00 00 00 30",
    10: "ff 07 29 0a 00 00 00 3a",
}

CONTRAST = {
    -15: "ff 07 2b f1 00 00 00 23",
    0: "ff 07 2b 00 00 00 00 32",
    15: "ff 07 2b 0f 00 00 00 41",
}

KEYSTONE_V = {
    "up-max": "ff 07 35 ec 00 00 00 28",
    "up": "ff 07 35 f1 00 00 00 2d",
    "center": "ff 07 35 00 00 00 00 3c",
    "center-plus": "ff 07 35 01 00 00 00 3d",
    "down-max": "ff 07 35 1e 00 00 00 5a",
}

KEYSTONE_H = {
    "left-max": "ff 07 33 e2 00 00 00 1c",
    "left": "ff 07 33 ff 00 00 00 3b",
    "center": "ff 07 33 00 00 00 00 3a",
    "center-plus": "ff 07 33 01 00 00 00 3b",
    "right-max": "ff 07 33 1e 00 00 00 58",
}

BAUD_CONSTANTS = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


def command_bytes(hex_string):
    return bytes.fromhex(hex_string)


def configure_serial(fd, baud):
    baud_const = BAUD_CONSTANTS.get(baud)
    if baud_const is None:
        raise SystemExit(f"Unsupported baud rate: {baud}")

    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[3] = 0
    attrs[4] = baud_const
    attrs[5] = baud_const
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)


def send(device, baud, payload, read_seconds):
    fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        configure_serial(fd, baud)
        os.write(fd, payload)
        termios.tcdrain(fd)
        print(f"sent {len(payload)} bytes: {payload.hex(' ')}")

        if read_seconds <= 0:
            return
        end = time.time() + read_seconds
        received = bytearray()
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                try:
                    received.extend(os.read(fd, 4096))
                except BlockingIOError:
                    pass
        if received:
            print(f"received {len(received)} bytes: {received.hex(' ')}")
        else:
            print("received 0 bytes")
    finally:
        os.close(fd)


def build_payload(args):
    if args.raw:
        return command_bytes(args.raw)
    if args.command:
        return command_bytes(COMMANDS[args.command])
    if args.sharpness is not None:
        return command_bytes(SHARPNESS[args.sharpness])
    if args.brightness is not None:
        return command_bytes(BRIGHTNESS[args.brightness])
    if args.contrast is not None:
        return command_bytes(CONTRAST[args.contrast])
    if args.keystone_v:
        return command_bytes(KEYSTONE_V[args.keystone_v])
    if args.keystone_h:
        return command_bytes(KEYSTONE_H[args.keystone_h])
    raise SystemExit("Choose a command. Use --help for options.")


def main():
    parser = argparse.ArgumentParser(description="Send 9600 baud commands to the CH340 micro projector serial board.")
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--read-seconds", type=float, default=0.5)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--command", choices=sorted(COMMANDS))
    group.add_argument("--sharpness", type=int, choices=sorted(SHARPNESS))
    group.add_argument("--brightness", type=int, choices=sorted(BRIGHTNESS))
    group.add_argument("--contrast", type=int, choices=sorted(CONTRAST))
    group.add_argument("--keystone-v", choices=sorted(KEYSTONE_V))
    group.add_argument("--keystone-h", choices=sorted(KEYSTONE_H))
    group.add_argument("--raw", help="Hex bytes, for example: 'ff 07 99 00 00 00 00 a0'")

    args = parser.parse_args()
    payload = build_payload(args)
    send(args.device, args.baud, payload, args.read_seconds)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(1)
