#!/usr/bin/env python3
"""Encoder console monitor for the BeagleBone.
On the PCB, the rotary encoder connects to P8_15 (Channel A) and P8_12 (Channel B).
This tool reads the live encoder stream from the PCB GPIO subsystem.
"""

import sys
from pathlib import Path

# Load and execute the PCB GPIO encoder test
script_dir = Path(__file__).resolve().parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

from encoder_gpio_console_test import main

if __name__ == "__main__":
    raise SystemExit(main())
