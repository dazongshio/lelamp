#!/usr/bin/env bash
set -euo pipefail

CONFIG_MARK_START="# --- DLPDLCR2000EVM DPI config: start ---"
CONFIG_MARK_END="# --- DLPDLCR2000EVM DPI config: end ---"

usage() {
  printf 'Usage: sudo %s [--config|--init|--all]\n' "$0"
  printf '  --config  Update Raspberry Pi boot config for DLPDLCR2000EVM DPI\n'
  printf '  --init    Send DLPC2607 I2C setup commands on /dev/i2c-3\n'
  printf '  --all     Run --config, then --init if /dev/i2c-3 exists\n'
}

require_root_for_config() {
  if [[ "${EUID}" -ne 0 ]]; then
    printf 'This step must run as root because it edits /boot config.\n' >&2
    printf 'Run: sudo %s --config\n' "$0" >&2
    exit 1
  fi
}

find_config() {
  if [[ -f /boot/firmware/config.txt ]]; then
    printf '/boot/firmware/config.txt\n'
  elif [[ -f /boot/config.txt ]]; then
    printf '/boot/config.txt\n'
  else
    printf 'Could not find /boot/firmware/config.txt or /boot/config.txt\n' >&2
    exit 1
  fi
}

install_i2c_tools_if_needed() {
  if command -v i2cdetect >/dev/null 2>&1 && command -v i2cset >/dev/null 2>&1; then
    return
  fi

  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y i2c-tools
  else
    printf 'i2c-tools is missing and apt-get is unavailable.\n' >&2
    exit 1
  fi
}

quiet_framebuffer_console() {
  if [[ "${EUID}" -ne 0 ]]; then
    return
  fi

  if [[ -w /sys/class/graphics/fbcon/cursor_blink ]]; then
    printf '0' > /sys/class/graphics/fbcon/cursor_blink || true
  fi

  local bind name
  for bind in /sys/class/vtconsole/vtcon*/bind; do
    [[ -e "$bind" ]] || continue
    name="${bind%/bind}/name"
    if [[ -r "$name" ]] && grep -qi 'frame buffer' "$name"; then
      printf '0' > "$bind" || true
    fi
  done
}

write_config() {
  require_root_for_config
  local config_file
  config_file="$(find_config)"
  local backup_file="${config_file}.bak.$(date +%Y%m%d-%H%M%S)"

  cp -a "$config_file" "$backup_file"
  printf 'Backed up %s to %s\n' "$config_file" "$backup_file"

  local tmp_file
  tmp_file="$(mktemp)"

  awk -v start="$CONFIG_MARK_START" -v end="$CONFIG_MARK_END" '
    $0 == start { skip = 1; next }
    $0 == end { skip = 0; next }
    !skip { print }
  ' "$config_file" > "$tmp_file"

  # GPIO2/3 are used by DPI VSYNC/HSYNC in this wiring, so hardware I2C must stay off.
  sed -i -E \
    -e 's/^([[:space:]]*)dtparam=(i2c_arm|i2c)=on([[:space:]]*)$/\1#dtparam=\2=on\3  # disabled for DLP DPI GPIO2\/3/' \
    "$tmp_file"

  if ! grep -Eq '^[[:space:]]*dtoverlay=vc4-kms-v3d([,[:space:]]|$)' "$tmp_file"; then
    {
      printf '\n'
      printf '# Enable DRM VC4 V3D driver for KMS DPI output\n'
      printf 'dtoverlay=vc4-kms-v3d\n'
    } >> "$tmp_file"
  fi

  {
    printf '\n%s\n' "$CONFIG_MARK_START"
    printf '# DLPDLCR2000EVM on Raspberry Pi DPI, RGB666 data on GPIO4-GPIO21.\n'
    printf '# GPIO0=PCLK, GPIO1=DATAEN, GPIO2=VSYNC, GPIO3=HSYNC.\n'
    printf 'dtoverlay=vc4-kms-dpi-generic,clock-frequency=15000000,bus-format=0x1009\n'
    printf 'dtparam=hactive=640,hfp=14,hsync=4,hbp=12\n'
    printf 'dtparam=vactive=360,vfp=2,vsync=3,vbp=9\n'
    printf '\n'
    printf '# Software I2C for DLP control on GPIO23=SDA and GPIO24=SCL.\n'
    printf '# Do not enable hardware i2c_arm here; it would claim GPIO2/GPIO3.\n'
    printf 'dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=23,i2c_gpio_scl=24,i2c_gpio_delay_us=2\n'
    printf '%s\n' "$CONFIG_MARK_END"
  } >> "$tmp_file"

  install -o root -g root -m "$(stat -c '%a' "$config_file")" "$tmp_file" "$config_file"
  rm -f "$tmp_file"

  printf 'Updated %s\n' "$config_file"
  printf 'Reboot is required before /dev/i2c-3 and the DPI mode appear.\n'
}

init_dlp() {
  if [[ ! -e /dev/i2c-3 ]]; then
    printf '/dev/i2c-3 is not present yet. Reboot after --config, then run: sudo %s --init\n' "$0" >&2
    exit 2
  fi

  install_i2c_tools_if_needed

  printf 'Scanning I2C bus 3...\n'
  i2cdetect -y 3

  printf 'Waiting for DLPC2607 at 0x1b...\n'
  local found=0
  local attempt
  for attempt in $(seq 1 20); do
    if i2cdetect -y 3 0x1b 0x1b 2>/dev/null | grep -q '1b'; then
      found=1
      break
    fi
    sleep 0.5
  done

  if [[ "$found" -ne 1 ]]; then
    printf 'DLPC2607 at 0x1b did not respond on /dev/i2c-3.\n' >&2
    printf 'If 0x57 appears but 0x1b does not, the I2C header path is alive but the projector controller is not ready.\n' >&2
    printf 'Check DLP 5V power, common ground, HOST_PRESENTZ to GND, and PROJ_ON_EXT pulled up to 3.3V.\n' >&2
    exit 3
  fi

  printf 'Writing DLP input configuration...\n'
  i2cset -y 3 0x1b 0x0d 0x00 0x00 0x00 0x02 i
  i2cset -y 3 0x1b 0x0c 0x00 0x00 0x00 0x1b i
  i2cset -y 3 0x1b 0x0b 0x00 0x00 0x00 0x00 i
  i2cset -y 3 0x1b 0xaf 0x00 0x00 0x00 0x16 i
  i2cset -y 3 0x1b 0x1e 0x00 0x00 0x00 0x01 i

  quiet_framebuffer_console

  printf 'DLP set to RGB888, 640x360 landscape, parallel video input, rising-edge PCLK, VSYNC lock.\n'
}

main() {
  local mode="${1:---all}"

  case "$mode" in
    --config)
      write_config
      ;;
    --init)
      init_dlp
      ;;
    --all)
      write_config
      if [[ -e /dev/i2c-3 ]]; then
        init_dlp
      else
        printf 'Next: sudo reboot\n'
        printf 'After reboot: sudo %s --init\n' "$0"
      fi
      ;;
    -h|--help)
      usage
      ;;
    *)
      usage >&2
      exit 64
      ;;
  esac
}

main "$@"
