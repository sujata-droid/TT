#!/usr/bin/env python3
"""SCL3300 Datasheet-Compliant Diagnostic & Continuous SPI Engine.

Implements Murata SCL3300-D01 Section 4.2 Startup & Reset Sequence:
1. Power-on / Pre-reset settling (15 ms)
2. Software Reset (SW_RESET: 0xB4002098)
3. Reset recovery delay (25 ms)
4. Mode selection with valid Murata CRC8:
   - Mode 1 (+/-1.2g, 40Hz): 0xB400001F
   - Mode 2 (+/-2.4g, 70Hz): 0xB4000102
   - Mode 3 (+/-1.2g, 10Hz): 0xB4000225
   - Mode 4 (+/-2.4g, 10Hz): 0xB4000338
5. Mode settling delay (100 ms)
6. Status reading & off-frame pipeline clearing (3-stage)
7. WHOAMI verification (0x40000091 -> expected 0x00C1 / 0x00C2)
8. Continuous clock & reading loop so clock signals are constant and visible
"""

import argparse
import os
import struct
import sys
import time
import spidev

# Command Definitions per Murata Datasheet Doc No 4921
CMD_SW_RESET     = 0xB4002098  # SW Reset
CMD_MODE1        = 0xB400001F  # Mode 1: +/-1.2g, 40 Hz
CMD_MODE2        = 0xB4000102  # Mode 2: +/-2.4g, 70 Hz
CMD_MODE3        = 0xB4000225  # Mode 3: +/-1.2g, 10 Hz
CMD_MODE4        = 0xB4000338  # Mode 4: +/-2.4g, 10 Hz (Inclination)
CMD_READ_WHOAMI  = 0x40000091  # Read WHOAMI (reg 0x10)
CMD_READ_STATUS  = 0x180000E5  # Read STATUS (reg 0x06)
CMD_READ_ERR1    = 0x1C0000E3  # Read ERR_FLAG1
CMD_READ_ERR2    = 0x200000C1  # Read ERR_FLAG2
CMD_READ_ACC_X   = 0x040000F7  # Read Acc X
CMD_READ_ACC_Y   = 0x080000FD  # Read Acc Y
CMD_READ_ACC_Z   = 0x0C0000FB  # Read Acc Z
CMD_READ_ANG_X   = 0x240000C7  # Read Angle X
CMD_READ_ANG_Y   = 0x280000CD  # Read Angle Y
CMD_READ_ANG_Z   = 0x2C0000CB  # Read Angle Z
CMD_DUMMY        = 0x000000FF  # Pipeline flush / NOP

def scl3300_crc8(b0, b1, b2):
    crc = 0xFF
    for b in (b0, b1, b2):
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x1D) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc ^ 0xFF

def check_crc(val):
    b0 = (val >> 24) & 0xFF
    b1 = (val >> 16) & 0xFF
    b2 = (val >> 8) & 0xFF
    b3 = val & 0xFF
    return b3 == scl3300_crc8(b0, b1, b2)

def to_bytes(cmd):
    return [(cmd >> 24) & 0xFF, (cmd >> 16) & 0xFF, (cmd >> 8) & 0xFF, cmd & 0xFF]

def xfer(spi, cmd):
    rx = spi.xfer2(to_bytes(cmd))
    # Required t_high inter-command CS delay >= 10 microseconds
    time.sleep(0.000015)
    return int.from_bytes(bytes(rx), "big")

def parse_frame(val):
    rs = (val >> 24) & 0x03
    raw_data = (val >> 8) & 0xFFFF
    crc_ok = check_crc(val)
    # Signed 16-bit
    data_signed = struct.unpack(">h", raw_data.to_bytes(2, "big"))[0]
    return rs, raw_data, data_signed, crc_ok

def run_inclinometer(bus=1, mode_num=4, speed=500000, continuous=False):
    print("=" * 65)
    print(f" SCL3300 DATASHEET INITIALIZATION & VERIFICATION (SPI Bus {bus})")
    print("=" * 65)
    
    # 1. Ensure Pin Multiplexing
    if bus == 1:
        print("[PINMUX] Configuring SPI1: P9_28(CS), P9_29(MISO), P9_30(MOSI), P9_31(SCK)")
        os.system("config-pin P9_28 spi_cs >/dev/null 2>&1")
        os.system("config-pin P9_29 spi >/dev/null 2>&1")
        os.system("config-pin P9_30 spi >/dev/null 2>&1")
        os.system("config-pin P9_31 spi_sclk >/dev/null 2>&1")
    else:
        print("[PINMUX] Configuring SPI0: P9_17(CS), P9_21(MISO), P9_18(MOSI), P9_22(SCK)")
        os.system("config-pin P9_17 spi_cs >/dev/null 2>&1")
        os.system("config-pin P9_21 spi >/dev/null 2>&1")
        os.system("config-pin P9_18 spi >/dev/null 2>&1")
        os.system("config-pin P9_22 spi_sclk >/dev/null 2>&1")
        
    time.sleep(0.05)
    
    spi = spidev.SpiDev()
    spi.open(bus, 0)
    spi.max_speed_hz = speed
    spi.mode = 0  # CPOL=0, CPHA=0 (Mode 0 per datasheet)
    
    try:
        # Step 1: Pre-reset delay (settling)
        print("[1/6] Pre-reset power stabilization delay (20 ms)...")
        time.sleep(0.02)
        
        # Step 2: Software Reset command (0xB4002098)
        print("[2/6] Sending SW_RESET command (0xB4002098)...")
        xfer(spi, CMD_SW_RESET)
        
        # Step 3: Wait for internal reset cycle to complete (Datasheet: > 1ms, wait 30ms)
        print("[3/6] Waiting for sensor reset recovery (30 ms)...")
        time.sleep(0.03)
        
        # Step 4: Write Operation Mode (Datasheet Section 4.2)
        mode_cmd = {1: CMD_MODE1, 2: CMD_MODE2, 3: CMD_MODE3, 4: CMD_MODE4}.get(mode_num, CMD_MODE4)
        print(f"[4/6] Setting Operation Mode {mode_num} (Command: 0x{mode_cmd:08X})...")
        xfer(spi, mode_cmd)
        
        # Step 5: Mode settling time (Mode 1: 25ms, Mode 4: 100ms)
        settling = 0.100 if mode_num in (3, 4) else 0.030
        print(f"[5/6] Waiting for mode settling ({settling*1000:.0f} ms)...")
        time.sleep(settling)
        
        # Step 6: Flush pipeline & read Status to clear startup status
        print("[6/6] Pipeline flush & Reading STATUS (0x180000E5)...")
        xfer(spi, CMD_READ_STATUS)
        xfer(spi, CMD_READ_STATUS)
        rx_status = xfer(spi, CMD_DUMMY)
        rs, raw, sdata, crc_ok = parse_frame(rx_status)
        print(f"      STATUS response: raw=0x{rx_status:08X} (RS={rs}, Payload=0x{raw:04X}, CRC_OK={crc_ok})")
        
        # Step 7: Request WHOAMI
        print("\n--> Requesting WHOAMI (0x40000091)...")
        xfer(spi, CMD_READ_WHOAMI)
        time.sleep(0.005)
        rx_whoami = xfer(spi, CMD_DUMMY)
        rs, raw, sdata, crc_ok = parse_frame(rx_whoami)
        whoami_id = (raw & 0xFF)
        
        print(f"--> WHOAMI Response: 0x{rx_whoami:08X}")
        print(f"    Return Status (RS): 0x{rs:02X} (0x01 = NORMAL)")
        print(f"    WHOAMI ID:         0x{whoami_id:02X} (Expected: 0xC1)")
        print(f"    CRC Valid:         {crc_ok}")
        
        if whoami_id == 0xC1:
            print("\n*** SUCCESS: Murata SCL3300-D01 detected and communication verified! ***\n")
        else:
            print(f"\n[NOTE] Received raw=0x{rx_whoami:08X}. If 0x00000000, MISO is LOW. If 0xFFFFFFFF, MISO is HIGH.\n")
            
        print("Starting continuous live reading loop. (Clock actively pumping on SCK)...")
        print("Press Ctrl+C to exit.\n")
        print("     Time(s)    Acc_X(mm)    Acc_Y(mm)    Acc_Z(mm)   RS   CRC")
        print("-" * 65)
        
        start = time.time()
        while True:
            # SCL3300 Off-frame reading:
            # Send Read Acc X -> returns prev
            xfer(spi, CMD_READ_ACC_X)
            # Send Read Acc Y -> returns Acc X
            rx_x = xfer(spi, CMD_READ_ACC_Y)
            # Send Read Acc Z -> returns Acc Y
            rx_y = xfer(spi, CMD_READ_ACC_Z)
            # Send Dummy -> returns Acc Z
            rx_z = xfer(spi, CMD_DUMMY)
            
            rs_x, _, val_x, crc_x = parse_frame(rx_x)
            rs_y, _, val_y, crc_y = parse_frame(rx_y)
            rs_z, _, val_z, crc_z = parse_frame(rx_z)
            
            # Sensitivity in Mode 4 is 12000 LSB/g; Gauge is 1676 mm
            x_mm = (val_x / 12000.0) * 1676.0 if rs_x == 1 and crc_x else float('nan')
            y_mm = (val_y / 12000.0) * 1676.0 if rs_y == 1 and crc_y else float('nan')
            z_mm = (val_z / 12000.0) * 1676.0 if rs_z == 1 and crc_z else float('nan')
            
            elapsed = time.time() - start
            crc_str = "OK" if (crc_x and crc_y and crc_z) else "FAIL"
            print(f"    {elapsed:7.2f}s   {x_mm:10.2f}   {y_mm:10.2f}   {z_mm:10.2f}   {rs_x}   {crc_str}", flush=True)
            time.sleep(0.1 if not continuous else 0.001)
            
    except KeyboardInterrupt:
        print("\nTest stopped by user.")
    finally:
        spi.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bus", type=int, default=1, choices=[0, 1])
    parser.add_argument("--mode", type=int, default=4, choices=[1, 2, 3, 4])
    parser.add_argument("--speed", type=int, default=500000)
    parser.add_argument("--continuous", action="store_true")
    args = parser.parse_args()
    run_inclinometer(bus=args.bus, mode_num=args.mode, speed=args.speed, continuous=args.continuous)
