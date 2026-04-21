/*
 * scl3300.h  --  Murata SCL3300-D01 Inclinometer Driver Header
 * =============================================================
 *
 * SENSOR OVERVIEW
 * ---------------
 * The SCL3300-D01 is a 3-axis MEMS inclinometer from Murata.
 * Designed for industrial applications: IP67, -40 to +85 C.
 * Measures tilt using the component of gravity on each axis.
 *
 * SPI INTERFACE RULES  (most bugs come from violating these)
 * -----------------------------------------------------------
 * 1. Mode 0 (CPOL=0, CPHA=0). Data latched on RISING edge of SCK.
 * 2. 32-bit frames ONLY. CS must go HIGH between every 32-bit frame.
 *    If CS stays low across frames the chip de-syncs permanently.
 * 3. PIPELINED: sending command N returns the RESPONSE to command N-1.
 *    After power-on or mode-change you must send two extra dummy reads.
 * 4. CRC: every frame has an 8-bit CRC. Validate it or silent corruption.
 * 5. CS must be HIGH for at least 10 us between frames.
 *
 * FRAME FORMAT (32 bits, MSB first)
 * ----------------------------------
 *   Bit 31:     RW    (0=Read, 1=Write)
 *   Bits 30-26: ADDR  (5-bit register address)
 *   Bits 25-10: DATA  (16-bit payload)
 *   Bits 9-8:   RS    (return status from sensor, in RX frames)
 *   Bits 7-0:   CRC   (over bytes [3:1])
 *
 * RS FIELD VALUES
 * ---------------
 *   0x01 = Normal operation     <- only state where data is valid
 *   0x00 = Startup in progress  <- wait longer after power-on/reset
 *   0x02 = Hard error           <- device needs reset or is damaged
 *   0x03 = Soft error           <- recoverable, read STATUS to clear
 *
 * CROSS-LEVEL FORMULA
 * -------------------
 *   Sensitivity in Mode 1 = 1000 LSB/g
 *   If tilted by angle theta from horizontal:
 *     ACC_X_raw = 1000 * sin(theta)
 *   Cross-level (mm) = gauge_mm * sin(theta)
 *                    = gauge_mm * (ACC_X_raw / 1000.0f)  [exact for small angles]
 *                    = 1676 * (raw / 1000)               [for BG rail]
 *
 * TWIST FORMULA
 *   twist (mm/m) = delta_cross_level_mm / delta_chainage_m
 *   Computed over a rolling 3-metre baseline.
 *
 * SPI WIRING TO BBB
 * -----------------
 *   SCL3300 CSB  -> P9_17  (SPI0_CS0)
 *   SCL3300 MISO -> P9_21  (SPI0_D0)
 *   SCL3300 MOSI -> P9_18  (SPI0_D1)
 *   SCL3300 SCK  -> P9_22  (SPI0_CLK)
 *   SCL3300 AVDD/DVDD -> 3.3 V
 *   SCL3300 AVSS/DVSS -> GND
 *   Linux device: /dev/spidev0.0
 */

#ifndef SCL3300_H_
#define SCL3300_H_

#include <stdint.h>
#include <stdbool.h>

/* SPI device */
#define SCL3300_SPI_DEV      "/dev/spidev0.0"
#define SCL3300_SPI_SPEED_HZ  2000000   /* 2 MHz: reliable for long cables */
#define SCL3300_SPI_MODE      0
#define SCL3300_SPI_BITS      8

/* Pre-computed 32-bit commands (datasheet Table 9, CRC included) */
#define CMD_READ_ACC_X      0x040000F7UL
#define CMD_READ_ACC_Y      0x080000FDUL
#define CMD_READ_ACC_Z      0x0C0000FBUL
#define CMD_READ_STO        0x100000E9UL
#define CMD_READ_TEMP       0x140000EFUL
#define CMD_READ_STATUS     0x180000E5UL
#define CMD_READ_ERR_FLAG1  0x1C0000E3UL
#define CMD_READ_ERR_FLAG2  0x200000C1UL
#define CMD_READ_CMD        0x340000DFUL
#define CMD_CHANGE_MODE1    0xB4000091UL  /* Mode 1: +/-3g, 40 Hz, normal */
#define CMD_CHANGE_MODE2    0xB4000102UL  /* Mode 2: +/-6g, 70 Hz         */
#define CMD_CHANGE_MODE3    0xB4000213UL  /* Mode 3: +/-1.5g, 10 Hz       */
#define CMD_CHANGE_MODE4    0xB4000319UL  /* Mode 4: +/-0.5g, 10 Hz       */
#define CMD_SW_RESET        0xB4002098UL
#define CMD_DUMMY           0x000000FFUL  /* NOP / pipeline flush         */

/* RS field values */
#define RS_STARTUP   0x00
#define RS_NORMAL    0x01
#define RS_HARD_ERR  0x02
#define RS_SOFT_ERR  0x03

/* Physical constants */
#define SCL3300_SENSITIVITY   1000.0f   /* LSB per g in Mode 1          */
#define GAUGE_MM              1676.0f   /* Indian BG rail gauge (mm)    */
#define TWIST_BASELINE_M      3.0f      /* Twist computation baseline   */

/* Timing (ms / us) */
#define SCL3300_STARTUP_MS    1
#define SCL3300_MODE_DELAY_MS 5
#define SCL3300_RESET_DELAY_MS 2
#define SCL3300_CS_HIGH_US    10

/* Fault threshold */
#define SCL3300_MAX_CRC_ERRORS  10

/* Driver state */
typedef struct {
    int      spi_fd;
    bool     initialized;
    bool     healthy;
    uint8_t  rs_field;
    uint32_t crc_error_count;
    float    last_cl_mm;
} SCL3300;

/* API */
int    scl3300_open(SCL3300 *dev);
int    scl3300_read_cross_level(SCL3300 *dev, float *out_mm);
bool   scl3300_health_check(SCL3300 *dev);
void   scl3300_close(SCL3300 *dev);
uint8_t scl3300_crc8(uint8_t b0, uint8_t b1, uint8_t b2);

#endif /* SCL3300_H_ */
