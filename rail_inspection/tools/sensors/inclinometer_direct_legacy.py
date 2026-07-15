#!/usr/bin/env python3
import time
import spidev
import sys

# Murata SCL3300-D01 Commands
CMD_READ_ANG_X  = [0x04, 0x00, 0x00, 0xF7]
CMD_READ_WHOAMI = [0x40, 0x00, 0x00, 0x91]
CMD_SW_RESET    = [0xB4, 0x00, 0x20, 0x98]
CMD_MODE4       = [0xB4, 0x00, 0x03, 0x38]
CMD_ENA_ANG     = [0xB0, 0x00, 0x1F, 0x6F]

def crc8(data):
    crc = 0xFF
    for byte in data[:3]:
        for i in range(8):
            if (crc ^ (byte << i)) & 0x80:
                crc = (crc << 1) ^ 0x1D
            else:
                crc <<= 1
            crc &= 0xFF
    return crc ^ 0xFF

def xfer(spi, cmd):
    resp = spi.xfer2(cmd)
    time.sleep(0.001)
    return resp

def main():
    print("SCL3300 Inclinometer Direct Test (SPI0)")
    spi = spidev.SpiDev()
    try:
        spi.open(0, 0)
        spi.max_speed_hz = 500000
        spi.mode = 0
        
        print("Resetting sensor...")
        xfer(spi, CMD_SW_RESET)
        time.sleep(0.1)
        
        print("Setting Mode 4 (+/- 10 deg)...")
        xfer(spi, CMD_MODE4)
        xfer(spi, CMD_ENA_ANG)
        time.sleep(0.1)
        
        # Verify WHOAMI
        xfer(spi, CMD_READ_WHOAMI)
        resp = xfer(spi, CMD_READ_WHOAMI)
        whoami = resp[2]
        print(f"WHOAMI: 0x{whoami:02X} (Expected 0xC1)")
        
        print("\nReading angles (Ctrl+C to stop)...")
        while True:
            xfer(spi, CMD_READ_ANG_X)
            resp = xfer(spi, CMD_READ_ANG_X)
            
            # Extract 16-bit signed data
            raw = (resp[1] << 8) | resp[2]
            if raw & 0x8000:
                raw -= 0x10000
            
            angle = raw / 16384.0 * 90.0
            print(f"\rRoll Angle: {angle:8.3f} deg", end="")
            sys.stdout.flush()
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        spi.close()

if __name__ == "__main__":
    main()
