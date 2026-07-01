#!/usr/bin/env bash
set -euo pipefail

BUS="${BUS:-3}"
ADDR="${ADDR:-0x1b}"

usage() {
  cat <<'EOF'
Usage: ./dlp_led_balance.sh <preset>

Presets:
  mild-red-down     Slightly reduce red LED current
  more-red-down     Further reduce red LED current
  strong-red-down   Stronger red reduction
  green-boost       Reduce red/blue and keep green high
  green-max         Stronger green bias for red-white correction
  warm-from-blue    Add red back and reduce blue
  no-blue-balance   Disable blue current while keeping red/green
  factory-300       Set RGB LED current near the EVM factory brightness
  neutral-1         Fine tune between blue and orange
  neutral-2         Slightly cooler than neutral-1
  neutral-3         Slightly warmer than neutral-1
  green-full        Maximize green and minimize red/blue
  red-off-test      Disable red LED only
  green-off-test    Disable green LED only
  blue-off-test     Disable blue LED only
  leds-on           Enable all RGB LEDs
  reset-default     Restore RGB LED current near the EVM factory brightness

Notes:
  DLPC2607 LED PWM: 0x000 is brightest, 0x400 is dimmest.
  This script disables WPC/Wcor, writes RGB LED PWM registers, then
  sends command 0xD3 to propagate LED currents to the LED driver.
EOF
}

write_rgb_pwm() {
  local red="$1"
  local green="$2"
  local blue="$3"

  # Disable WPC/Wcor so manual LED current registers take effect.
  i2cset -y "$BUS" "$ADDR" 0xb5 0x00 0x00 0x00 0x00 i

  # Register payload is 32-bit big-endian, same convention as existing DLP setup commands.
  i2cset -y "$BUS" "$ADDR" 0x12 0x00 0x00 "$(( (red >> 8) & 0xff ))" "$(( red & 0xff ))" i
  i2cset -y "$BUS" "$ADDR" 0x13 0x00 0x00 "$(( (green >> 8) & 0xff ))" "$(( green & 0xff ))" i
  i2cset -y "$BUS" "$ADDR" 0x14 0x00 0x00 "$(( (blue >> 8) & 0xff ))" "$(( blue & 0xff ))" i

  # Propagate LED current registers to PAD1000 via ICP compound command 0xD3.
  i2cset -y "$BUS" "$ADDR" 0x39 0x00 0x00 0x00 0x00 i
  i2cset -y "$BUS" "$ADDR" 0x3a 0x00 0x00 0x00 0x01 i
  i2cset -y "$BUS" "$ADDR" 0x38 0x00 0x00 0x00 0xd3 i

  local _attempt
  for _attempt in $(seq 1 20); do
    if [[ "$(i2cget -y "$BUS" "$ADDR" 0x3a b 2>/dev/null || printf 0xff)" == "0x00" ]]; then
      return
    fi
    sleep 0.05
  done
}

main() {
  local preset="${1:-}"
  case "$preset" in
    mild-red-down)
      write_rgb_pwm 0x400 0x3ff 0x3ff
      ;;
    more-red-down)
      write_rgb_pwm 0x400 0x3e0 0x3e0
      ;;
    strong-red-down)
      write_rgb_pwm 0x400 0x3c0 0x3c0
      ;;
    green-boost)
      write_rgb_pwm 0x400 0x300 0x3e0
      ;;
    green-max)
      write_rgb_pwm 0x400 0x280 0x3f0
      ;;
    warm-from-blue)
      write_rgb_pwm 0x3c0 0x280 0x400
      ;;
    no-blue-balance)
      write_rgb_pwm 0x3d0 0x200 0x400
      ;;
    factory-300)
      write_rgb_pwm 0x300 0x300 0x300
      ;;
    neutral-1)
      write_rgb_pwm 0x3f0 0x200 0x3f0
      ;;
    neutral-2)
      write_rgb_pwm 0x3f8 0x180 0x3e8
      ;;
    neutral-3)
      write_rgb_pwm 0x3e8 0x180 0x3f8
      ;;
    green-full)
      write_rgb_pwm 0x400 0x000 0x400
      ;;
    red-off-test)
      i2cset -y "$BUS" "$ADDR" 0x16 0x00 0x00 0x00 0x06 i
      ;;
    green-off-test)
      i2cset -y "$BUS" "$ADDR" 0x16 0x00 0x00 0x00 0x05 i
      ;;
    blue-off-test)
      i2cset -y "$BUS" "$ADDR" 0x16 0x00 0x00 0x00 0x03 i
      ;;
    leds-on)
      i2cset -y "$BUS" "$ADDR" 0x16 0x00 0x00 0x00 0x07 i
      ;;
    reset-default)
      write_rgb_pwm 0x300 0x300 0x300
      ;;
    -h|--help|"")
      usage
      ;;
    *)
      usage >&2
      exit 64
      ;;
  esac
}

main "$@"
