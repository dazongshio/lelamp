# Projector Control Notes

This file records the working projector setup used on this Raspberry Pi.

## DLPDLCR2000EVM DPI Output

The Raspberry Pi drives the DLPDLCR2000EVM over DPI RGB666. The DLP controller is configured for RGB888, with only each color's upper 6 bits connected. The lower 2 bits on the DLP side should be pulled down.

Working boot config in `/boot/firmware/config.txt`:

```txt
dtoverlay=vc4-kms-v3d

dtoverlay=vc4-kms-dpi-generic,clock-frequency=15000000,bus-format=0x1009
dtparam=hactive=640,hfp=14,hsync=4,hbp=12
dtparam=vactive=360,vfp=2,vsync=3,vbp=9

dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=23,i2c_gpio_scl=24,i2c_gpio_delay_us=2
```

Important Raspberry Pi boot setting:

```txt
/boot/firmware/cmdline.txt must not contain console=serial0, console=ttyAMA*, or console=ttyS*.
Keep console=tty1.
```

Reason: GPIO14/GPIO15 are part of the DPI green channel in this wiring. A serial console on these pins can corrupt green output.

Expected runtime checks:

```bash
i2cdetect -y 3
# Expected: 0x1b DLPC2607 and 0x57 EEPROM

pinctrl get 0-21
# Expected: GPIO0-GPIO21 are DPI functions.

vcgencmd get_throttled
# Expected: throttled=0x0
```

Initialize external parallel video:

```bash
cd /home/lemp/lelamp
sudo ./dlp2000_setup.sh --init
```

Useful test patterns:

```bash
./dlp_quad_test.py          # LT red, RT green, LB blue, RB white
./dlp_color_test.py red
./dlp_color_test.py green
./dlp_color_test.py blue
./dlp_color_test.py white
./dlp_color_test.py black
```

## DLPDLCR2000EVM RGB Data Wiring

Use compact RGB666 (`bus-format=0x1009`), not padhi/CFG2.

```txt
DLP blue input Data2-7 <- Pi GPIO4-9
P1-43 Data2  <- GPIO4  physical pin 7
P1-44 Data3  <- GPIO5  physical pin 29
P1-41 Data4  <- GPIO6  physical pin 31
P1-42 Data5  <- GPIO7  physical pin 26
P1-39 Data6  <- GPIO8  physical pin 24
P1-40 Data7  <- GPIO9  physical pin 21

DLP green input Data10-15 <- Pi GPIO10-15
P1-36 Data10 <- GPIO10 physical pin 19
P1-34 Data11 <- GPIO11 physical pin 23
P1-35 Data12 <- GPIO12 physical pin 32
P1-33 Data13 <- GPIO13 physical pin 33
P1-31 Data14 <- GPIO14 physical pin 8
P1-32 Data15 <- GPIO15 physical pin 10

DLP red input Data18-23 <- Pi GPIO16-21
P1-11 Data18 <- GPIO16 physical pin 36
P1-12 Data19 <- GPIO17 physical pin 11
P1-17 Data20 <- GPIO18 physical pin 12
P1-14 Data21 <- GPIO19 physical pin 35
P1-13 Data22 <- GPIO20 physical pin 38
P1-19 Data23 <- GPIO21 physical pin 40
```

DLP low bits should be pulled down with 10k resistors:

```txt
Data0/1/8/9/16/17 = P1-45/46/37/38/15/16 -> GND
```

Control wiring:

```txt
P2-3  VINTF         -> 3.3V
P2-43 HOST_PRESENTZ -> GND
P2-19 EXT_SCL       -> GPIO24 physical pin 18
P2-20 EXT_SDA       -> GPIO23 physical pin 16
P2-15 PROJ_ON_EXT   -> 10k pull-up to 3.3V
GPIO_INIT_DONE      -> leave unconnected
```

## CH340 Micro Projector Serial Control

The micro projector control board appears as:

```txt
/dev/ttyUSB0
/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
```

Protocol:

```txt
baud: 9600
data: 8N1
transport: raw 8-byte commands, usually no reply
```

Use the helper:

```bash
cd /home/lemp/lelamp
./micro_projector_serial.py --help
```

Common commands:

```bash
./micro_projector_serial.py --command power-toggle
./micro_projector_serial.py --command restore
./micro_projector_serial.py --command flip-v
./micro_projector_serial.py --command flip-h
./micro_projector_serial.py --command flip-both
./micro_projector_serial.py --command flip-none

./micro_projector_serial.py --sharpness 0
./micro_projector_serial.py --sharpness 6

./micro_projector_serial.py --brightness -31
./micro_projector_serial.py --brightness 0
./micro_projector_serial.py --brightness 10

./micro_projector_serial.py --contrast -15
./micro_projector_serial.py --contrast 0
./micro_projector_serial.py --contrast 15

./micro_projector_serial.py --keystone-v up-max
./micro_projector_serial.py --keystone-v center
./micro_projector_serial.py --keystone-v down-max

./micro_projector_serial.py --keystone-h left-max
./micro_projector_serial.py --keystone-h center
./micro_projector_serial.py --keystone-h right-max
```

Raw command example:

```bash
./micro_projector_serial.py --raw 'ff 07 29 0a 00 00 00 3a'
```

Recorded command bytes:

```txt
Power toggle:       ff 07 99 00 00 00 00 a0
Restore:            ff 07 77 00 00 00 00 7e

Sharpness 6:        ff 07 31 06 00 00 00 3e
Sharpness 5:        ff 07 31 05 00 00 00 3d
Sharpness 4:        ff 07 31 04 00 00 00 3c
Sharpness 3:        ff 07 31 03 00 00 00 3b
Sharpness 2:        ff 07 31 02 00 00 00 3a
Sharpness 1:        ff 07 31 01 00 00 00 39
Sharpness 0:        ff 07 31 00 00 00 00 38

Brightness +10:     ff 07 29 0a 00 00 00 3a
Brightness 0:       ff 07 29 00 00 00 00 30
Brightness -31:     ff 07 29 e1 00 00 00 11

Contrast +15:       ff 07 2b 0f 00 00 00 41
Contrast 0:         ff 07 2b 00 00 00 00 32
Contrast -15:       ff 07 2b f1 00 00 00 23

Vertical flip:      ff 07 37 01 00 00 00 3f
Horizontal flip:    ff 07 37 02 00 00 00 40
Both flip:          ff 07 37 00 00 00 00 3e
No flip:            ff 07 37 03 00 00 00 41

Vertical up max:    ff 07 35 ec 00 00 00 28
Vertical up:        ff 07 35 f1 00 00 00 2d
Vertical center:    ff 07 35 00 00 00 00 3c
Vertical center +1: ff 07 35 01 00 00 00 3d
Vertical down max:  ff 07 35 1e 00 00 00 5a

Horizontal left max:    ff 07 33 e2 00 00 00 1c
Horizontal left:        ff 07 33 ff 00 00 00 3b
Horizontal center:      ff 07 33 00 00 00 00 3a
Horizontal center +1:   ff 07 33 01 00 00 00 3b
Horizontal right max:   ff 07 33 1e 00 00 00 58
```

## Troubleshooting Notes

If DLP I2C shows `0x57` but not `0x1b`, the I2C path is alive but DLPC2607 is not ready. Check DLP 5V power, common ground, `HOST_PRESENTZ -> GND`, and `PROJ_ON_EXT` pull-up.

If internal DLP green is normal but external white is magenta, the green LED is good; check Pi GPIO10-GPIO15 to DLP Data10-Data15 and ensure serial console is disabled.

If the bottom of a black framebuffer shows a colored line, disable framebuffer console before testing:

```bash
sudo systemctl stop lightdm
sudo sh -c 'echo 0 > /sys/class/graphics/fbcon/cursor_blink; echo 0 > /sys/class/vtconsole/vtcon1/bind'
```
