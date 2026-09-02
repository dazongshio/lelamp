#!/usr/bin/env python3
"""Generate a minimal digital EDID with native CEA 1280x720p60 timing."""

from pathlib import Path


def descriptor(tag: int, text: str) -> bytes:
    payload = text.encode("ascii")[:13].ljust(13, b" ")
    return bytes((0, 0, 0, tag, 0)) + payload


edid = bytearray(bytes.fromhex("00 ff ff ff ff ff ff 00"))
edid.extend(bytes.fromhex("30 ac 20 07 00 00 00 00 01 24"))
# EDID 1.4, digital input, HDMI-a interface, 160x90 mm, gamma 2.20.
edid.extend(bytes.fromhex("01 04 82 10 09 78 0a"))
edid.extend(bytes(10))  # Chromaticity coordinates are unspecified.
edid.extend(bytes(3))   # No legacy established timings.
edid.extend(bytes.fromhex("01 01") * 8)  # All standard timings unused.
# Native CEA-861 1280x720p60: 74.25 MHz, 1650x750, +H/+V.
edid.extend(bytes.fromhex("01 1d 00 72 51 d0 1e 20 6e 28 55 00 a0 5a 00 00 00 1e"))
edid.extend(descriptor(0xFC, "LeLamp 720p"))
edid.extend(descriptor(0xFD, "\x32\x4b\x1e\x50\x0f\x00\x0a"))
edid.extend(descriptor(0xFF, "LELAMP-HDMI0"))
edid.append(1)  # One CTA-861 extension block follows.
edid.append((-sum(edid)) & 0xFF)

assert len(edid) == 128
assert sum(edid) & 0xFF == 0

# CTA-861 revision 3: native VIC 4 (1280x720p60) plus HDMI VSDB.
cta = bytearray(bytes.fromhex("02 03 0c 00 41 84 65 03 0c 00 10 00"))
cta.extend(bytes(127 - len(cta)))
cta.append((-sum(cta)) & 0xFF)
assert len(cta) == 128
assert sum(cta) & 0xFF == 0
edid.extend(cta)

assert len(edid) == 256
Path("/tmp/lelamp-720p.bin").write_bytes(edid)
