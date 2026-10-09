/*
 * encoder_pru0.c – Corrected PRU0 Quadrature Encoder Firmware
 * Target: AM335x PRU0 @ 200 MHz
 *
 * Pin Mapping:
 *   Channel A -> P9_27 (pr1_pru0_pru_r31 bit 5)
 *   Channel B -> P9_30 (pr1_pru0_pru_r31 bit 2)
 *
 * Shared Memory Map (PRUSS shared RAM at local 0x00010000,
 * ARM at 0x4A310000):
 *   0x00: int32_t  encoder_count (signed 32-bit count)
 *   0x04: uint32_t status        (1 = running)
 *   0x08: uint32_t sample_us     (sample period in us)
 *   0x0C: uint32_t magic_cookie  (count ^ 0xA5A5A5A5 for atomic verification)
 */

#include <stdint.h>
#include <pru_cfg.h>
#include "resource_table_empty.h"

/* PRU internal R31 register (Hardware inputs) */
volatile register uint32_t __R31;
volatile register uint32_t __R30;

/*
 * Keep the live control block out of PRU0 data RAM.  Offset zero of that RAM
 * is occupied by the remoteproc resource table (and the linker places stack
 * and C runtime data there as well).  Writing encoder data at 0x00 corrupts
 * those sections and makes ARM see arbitrary status/sample values.
 */
#define ENCODER_SHM_BASE 0x00010000u
#define DRAM_COUNT    (*((volatile int32_t  *)(ENCODER_SHM_BASE + 0x00u)))
#define DRAM_STATUS   (*((volatile uint32_t *)(ENCODER_SHM_BASE + 0x04u)))
#define DRAM_DBG_US   (*((volatile uint32_t *)(ENCODER_SHM_BASE + 0x08u)))
#define DRAM_COOKIE   (*((volatile uint32_t *)(ENCODER_SHM_BASE + 0x0Cu)))

#define ENC_A_BIT     5u   /* P9_27 = r31 bit 5 */
#define ENC_B_BIT     2u   /* P9_30 = r31 bit 2 */
#define COOKIE_MASK   0xA5A5A5A5u

/*
 * 200 PRU cycles @ 200 MHz = 1.0 microsecond per sample (1 MHz sample rate).
 * A state must remain unchanged for five samples before it is accepted. This
 * rejects short glitches from the NPN open-collector signal without changing
 * the 4x quadrature-counting algorithm.
 */
#define SAMPLE_CYCLES 200u
#define STABLE_SAMPLES 5u

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
    /* Allow PRU access to external OCP master bus */
    CT_CFG.SYSCFG_bit.STANDBY_INIT = 0;

    /* Initialize shared memory */
    DRAM_COUNT  = 0;
    DRAM_STATUS = 0;
    DRAM_DBG_US = SAMPLE_CYCLES / 200u; /* 200 cycles at 200 MHz = 1 us */
    DRAM_COOKIE = ((uint32_t)0) ^ COOKIE_MASK;

    int32_t  count        = 0;
    uint32_t prev_ab      = 0;
    uint32_t candidate_ab = 0;
    uint32_t stable_count = 0;

    /* Read initial state so we don't count a false step at boot */
    uint32_t r = __R31;
    prev_ab = (((r >> ENC_A_BIT) & 1u) << 1u) | ((r >> ENC_B_BIT) & 1u);
    candidate_ab = prev_ab;
    stable_count = STABLE_SAMPLES;

    /* Signal ARM Linux that firmware is active */
    DRAM_STATUS = 1u;

    /* Main real-time acquisition loop */
    while (1) {
        __delay_cycles(SAMPLE_CYCLES);

        /* Read raw pins */
        uint32_t reg_in = __R31;
        uint32_t curr_ab = (((reg_in >> ENC_A_BIT) & 1u) << 1u) |
                           ((reg_in >> ENC_B_BIT) & 1u);

        /* Reject a state until it has persisted for STABLE_SAMPLES reads. */
        if (curr_ab != candidate_ab) {
            candidate_ab = curr_ab;
            stable_count = 1u;
            continue;
        }

        if (stable_count < STABLE_SAMPLES) {
            stable_count++;
            continue;
        }

        /* Only process an accepted state change. */
        if (curr_ab != prev_ab) {
            int32_t delta = QEM[(prev_ab << 2u) | curr_ab];

            if (delta != 0) {
                count += delta;

                /* Write count to shared memory (atomic 32-bit word) */
                DRAM_COUNT  = count;
                DRAM_COOKIE = ((uint32_t)count) ^ COOKIE_MASK;
            }

            prev_ab = curr_ab;
        }
    }
}
