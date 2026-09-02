#!/bin/sh
set -eu

install -d -m 755 /lib/firmware/edid
install -m 644 /tmp/lelamp-720p.bin /lib/firmware/edid/lelamp-720p.bin
cp -a /boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.backup-before-edid
cp -a /boot/firmware/config.txt /boot/firmware/config.txt.backup-before-edid
install -m 755 -o root -g root /tmp/lelamp-cmdline-with-edid.txt /boot/firmware/cmdline.txt
install -m 755 -o root -g root /tmp/lelamp-config-with-edid.txt /boot/firmware/config.txt
sync
echo "EDID 已永久安装；重启后生效。"
