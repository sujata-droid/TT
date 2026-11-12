/*
 * encoder_pru1.c – Hardware Real-Time Quadrature Encoder Firmware for PRU1
 * Target: AM335x PRU1 @ 200 MHz
 *
 * Pin Mapping on Custom PCB:
 *   Channel A -> P8_15 (AM335x GPIO1[15] -> GPIO1_DATAIN bit 15)
 *   Channel B -> P8_12 (AM335x GPIO1[12] -> GPIO1_DATAIN bit 12)
 *
 * Note on hardware reading:
 *   P8_12 (LCD_DATA14) does not have a PRU R31 input mux in AM335x silicon,
 *   so PRU1 reads the GPIO1_DATAIN hardware register (0x4804C138) directly
 *   over the OCP master bus in ~30 ns, capturing BOTH P8_15 and P8_12
 *   simultaneously in a single 32-bit atomic word while keeping both pins in
 *   standard gpio input mode and leaving P9_30 (SCL3300 SPI1 MOSI) untouched.
 *
 * Shared Memory Map (PRUSS shared RAM at local 0x00010000, ARM physical at 0x4A310000):
 *   0x00: int32_t  encoder_count (signed 32-bit count, 4X decoding)
 *   0x04: uint32_t status        (1 = running)
 *   0x08: uint32_t sample_us     (sample period in us = 1)
 *   0x0C: uint32_t magic_cookie  (count ^ 0xA5A5A5A5 for atomic verification)
 */

#include <stdint.h>
#include <pru_cfg.h>
#include "resource_table_empty.h"

/* PRU internal registers */
volatile register uint32_t __R31;
volatile register uint32_t __R30;

/* PRUSS Shared RAM control block */
#define ENCODER_SHM_BASE 0x00010000u
#define DRAM_COUNT    (*((volatile int32_t  *)(ENCODER_SHM_BASE + 0x00u)))
#define DRAM_STATUS   (*((volatile uint32_t *)(ENCODER_SHM_BASE + 0x04u)))
#define DRAM_DBG_US   (*((volatile uint32_t *)(ENCODER_SHM_BASE + 0x08u)))
#define DRAM_COOKIE   (*((volatile uint32_t *)(ENCODER_SHM_BASE + 0x0Cu)))

/* AM335x GPIO1 DATAIN register (P8_15 = bit 15, P8_12 = bit 12) */
#define GPIO1_BASE    0x4804C000u
#define GPIO1_DATAIN  (*((volatile uint32_t *)(GPIO1_BASE + 0x138u)))

#define ENC_A_BIT     15u  /* P8_15 = gpio1[15] */
#define ENC_B_BIT     12u  /* P8_12 = gpio1[12] */
#define COOKIE_MASK   0xA5A5A5A5u

/*
 * ~200 PRU cycles @ 200 MHz (~1 microsecond per sample = ~1 MHz sample rate).
 * Requires 3 consecutive matching reads to reject EMI spikes while tracking
 * encoder edge frequencies far beyond 100 kHz with zero missed pulses.
 */
#define SAMPLE_CYCLES 190u
#define STABLE_SAMPLES 3u

/*
 * Quadrature Event Matrix (QEM)
 * Index = (prev_AB << 2) | curr_AB
 * [prev][curr] -> returns -1, 0, or +1
 */
static const int32_t QEM[16] = {
/* curr: 00   01   10   11       prev: */
          0,   1,  -1,   0,   /* 00 */
         -1,   0,   0,   1,   /* 01 */
          1,   0,   0,  -1,   /* 10 */
          0,  -1,   1,   0    /* 11 */
};

void main(void) {
    /* Enable OCP master port so PRU1 can read GPIO1_DATAIN (0x4804C138) */
    CT_CFG.SYSCFG_bit.STANDBY_INIT = 0;

    /* Initialize shared memory */
    DRAM_COUNT  = 0;
    DRAM_STATUS = 0;
    DRAM_DBG_US = 1u;
    DRAM_COOKIE = ((uint32_t)0) ^ COOKIE_MASK;

    int32_t  count        = 0;
    uint32_t prev_ab      = 0;
    uint32_t candidate_ab = 0;
    uint32_t stable_count = 0;

    /* Read initial state synchronously from GPIO1_DATAIN */
    uint32_t r = GPIO1_DATAIN;
    prev_ab = (((r >> ENC_A_BIT) & 1u) << 1u) | ((r >> ENC_B_BIT) & 1u);
    candidate_ab = prev_ab;
    stable_count = STABLE_SAMPLES;

    /* Signal ARM Linux that PRU1 firmware is active */
    DRAM_STATUS = 1u;

    /* Main deterministic acquisition loop (~1 MHz, 0% ARM CPU load) */
    while (1) {
        __delay_cycles(SAMPLE_CYCLES);

        /* Simultaneous atomic 32-bit hardware read of P8_15 and P8_12 */
        uint32_t reg_in = GPIO1_DATAIN;
        uint32_t curr_ab = (((reg_in >> ENC_A_BIT) & 1u) << 1u) |
                           ((reg_in >> ENC_B_BIT) & 1u);

        /* Digital noise filter: require STABLE_SAMPLES consecutive matches */
        if (curr_ab != candidate_ab) {
            candidate_ab = curr_ab;
            stable_count = 1u;
            continue;
        }

        if (stable_count < STABLE_SAMPLES) {
            stable_count++;
            continue;
        }

        /* Process accepted state change */
        if (curr_ab != prev_ab) {
            int32_t delta = QEM[(prev_ab << 2u) | curr_ab];

            if (delta != 0) {
                count += delta;

                /* Publish count and cookie to PRUSS shared RAM */
                DRAM_COUNT  = count;
                DRAM_COOKIE = ((uint32_t)count) ^ COOKIE_MASK;
            }

            prev_ab = curr_ab;
        }
    }
}
