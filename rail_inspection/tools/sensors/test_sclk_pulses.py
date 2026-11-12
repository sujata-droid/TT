#!/usr/bin/env python3
"""SCL3300 SPI Clock (SCK) Signal Verification Tool.

Supports both SPI0 and SPI1:
  SPI0: CS=P9_17, MISO=P9_21, MOSI=P9_18, SCK=P9_22 (/dev/spidev0.0)
  SPI1: CS=P9_28, MISO=P9_29, MOSI=P9_30, SCK=P9_31 (/dev/spidev1.0)
"""

import argparse
import os
import sys
import time

def run_fast_spi(bus=1):
    import spidev
    if bus == 0:
        cs_pin, clk_pin, dev_name = "P9_17", "P9_22", "/dev/spidev0.0"
        os.system("config-pin P9_17 spi_cs >/dev/null 2>&1")
        os.system("config-pin P9_21 spi >/dev/null 2>&1")
        os.system("config-pin P9_18 spi >/dev/null 2>&1")
        os.system("config-pin P9_22 spi_sclk >/dev/null 2>&1")
    else:
        cs_pin, clk_pin, dev_name = "P9_28", "P9_31", "/dev/spidev1.0"
        os.system("config-pin P9_28 spi_cs >/dev/null 2>&1")
        os.system("config-pin P9_29 spi >/dev/null 2>&1")
        os.system("config-pin P9_30 spi >/dev/null 2>&1")
        os.system("config-pin P9_31 spi_sclk >/dev/null 2>&1")

    spi = spidev.SpiDev()
    spi.open(bus, 0)
    spi.max_speed_hz = 500000
    spi.mode = 0

    print("============================================================")
    print(f" CONTINUOUS 500 kHz SPI CLOCK GENERATOR ON SPI{bus} ({clk_pin})")
    print("============================================================")
    print(f"Device: {dev_name} | Clock Pin: {clk_pin}")
    print(f"Actively pumping SPI clock pulses on {clk_pin}.")
    print(f"Use an Oscilloscope or Logic Analyzer on {clk_pin} to observe.")
    print("Press Ctrl+C to stop.\n")

    burst = [0x55, 0xAA, 0x55, 0xAA]
    count = 0
    start = time.time()
    try:
        while True:
            spi.xfer2(burst)
            count += 1
            if count % 2000 == 0:
                elapsed = time.time() - start
                print(f"[{elapsed:5.1f}s] SPI{bus} clock ({clk_pin}) active... ({count} frames sent)", flush=True)
            time.sleep(0.0001)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        spi.close()

def run_slow_blink(bus=1):
    os.system("pkill -9 -x sensor_service 2>/dev/null")
    import gpiod
    if bus == 0:
        pin_name, chip_num, line_num = "P9_22", "0", 2 # P9_22 = gpio0_2
        os.system(f"config-pin {pin_name} gpio >/dev/null 2>&1")
    else:
        pin_name, chip_num, line_num = "P9_31", "3", 14 # P9_31 = gpio3_14
        os.system(f"config-pin {pin_name} gpio >/dev/null 2>&1")

    chip = gpiod.Chip(chip_num)
    line = chip.get_line(line_num)
    line.request(consumer="clk_test", type=gpiod.LINE_REQ_DIR_OUT)

    print("============================================================")
    print(f" MULTIMETER VOLTAGE TEST ON SPI{bus} CLOCK PIN ({pin_name})")
    print("============================================================")
    print(f"Toggling {pin_name} between HIGH (3.3V) and LOW (0.0V) every second.")
    print(f"Put your Multimeter red probe on {pin_name} and black on GND.")
    print("Press Ctrl+C to stop.\n")

    try:
        val = 0
        while True:
            val = 1 - val
            line.set_value(val)
            voltage_str = "3.3V (HIGH)" if val == 1 else "0.0V (LOW) "
            print(f"{pin_name} Output -> {voltage_str} [Measure with Multimeter]", flush=True)
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nRestoring pin...")
    finally:
        line.set_value(0)
        line.release()
        os.system(f"config-pin {pin_name} spi_sclk >/dev/null 2>&1")
        print(f"Restored {pin_name} to spi_sclk.")

def main():
    parser = argparse.ArgumentParser(description="SPI SCLK Pulse Generator for SPI0 and SPI1")
    parser.add_argument("--bus", type=int, default=1, choices=[0, 1], help="SPI Bus: 0 (P9_22) or 1 (P9_31)")
    parser.add_argument("--slow", action="store_true", help="Toggle slowly (1 Hz) for Multimeter testing")
    args = parser.parse_args()

    if args.slow:
        run_slow_blink(bus=args.bus)
    else:
        run_fast_spi(bus=args.bus)

if __name__ == "__main__":
    main()
