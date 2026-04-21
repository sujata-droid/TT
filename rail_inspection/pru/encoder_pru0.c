/*
 * encoder_pru0.c  –  PRU0 Quadrature Encoder Decoder
 * ====================================================
 * Runs on AM335x PRU-ICSS PRU0 at exactly 200 MHz (5 ns / cycle).
 * No OS, no scheduler, no jitter.  This is bare-metal real-time.
 *
 * WHY PRU FOR THE ENCODER?
 * ------------------------
 * The ARM Cortex-A8 runs Linux.  Linux has a kernel scheduler that
 * can preempt any task for up to ~1 ms.  At 1000 PPR × 4X decoding,
 * a trolley travelling at 1 m/s generates 4000 counts/s = one pulse
 * every 250 µs.  A 1 ms preemption WILL cause missed counts → wrong
 * chainage.  PRU has NO interrupts from Linux and NEVER misses a
 * pulse.  That is the entire reason to use it here.
 *
 * WIRING
 * ------
 *   Encoder Channel A → P9_27  (pr1_pru0_pru_r31 bit 5)
 *   Encoder Channel B → P9_42A (pr1_pru0_pru_r31 bit 0)
 *   Encoder GND       → GND
 *   Encoder VCC       → 3.3 V
 *
 * CONFIG-PIN REQUIRED (run once in setup.sh):
 *   config-pin P9_27 pruin
 *   config-pin P9_42 pruin
 *
 * MEMORY MAP (ARM sees PRU0 Data RAM at 0x4A300000)
 * ------------------------------------------------
 *   Offset 0x00  int32_t   encoder_count    (signed, wraps at ±2 billion)
 *   Offset 0x04  uint32_t  status           (0=init, 1=running)
 *   Offset 0x08  uint32_t  sample_us        (actual sample period µs, debug)
 *   Offset 0x0C  uint32_t  reserved
 *
 * QUADRATURE DECODING  (4X = count every edge on both channels)
 * -------------------------------------------------------------
 *   State transition table indexed by (prev_AB << 2) | curr_AB:
 *
 *     prev\curr  00   01   10   11
 *       00        0   -1   +1    0
 *       01       +1    0    0   -1
 *       10       -1    0    0   +1
 *       11        0   +1   -1    0
 *
 *   (+1 = forward, -1 = backward, 0 = no edge / invalid glitch)
 *
 * SAMPLE RATE
 * -----------
 *   We sample every 2000 PRU cycles = 10 µs.
 *   This safely captures encoders up to 50 kHz edge rate.
 *   A 1000 PPR × 4X encoder on a 250 mm wheel needs only ~2.5 kHz
 *   at 1 m/s, so we have 20× margin.
 */

#include <stdint.h>
#include <pru_cfg.h>      /* CT_CFG register set */

/* ── PRU R31 is the input register (maps directly to PRU input pins) ─── */
volatile register uint32_t __R31;
volatile register uint32_t __R30;   /* output register – unused here     */

/* ── PRU0 Data RAM local address (ARM physical: 0x4A300000) ──────────── */
#define DRAM_COUNT    (*((volatile int32_t  *)(0x00000000u)))
#define DRAM_STATUS   (*((volatile uint32_t *)(0x00000004u)))
#define DRAM_DBG_US   (*((volatile uint32_t *)(0x00000008u)))
#define DRAM_RSVD     (*((volatile uint32_t *)(0x0000000Cu)))

/* ── Encoder pin bit indices in R31 ──────────────────────────────────── */
#define ENC_A_BIT   5u   /* P9_27  =  pr1_pru0_pru_r31_5 */
#define ENC_B_BIT   0u   /* P9_42A =  pr1_pru0_pru_r31_0 */

/*
 * Sample period: 2000 PRU cycles = 10 µs at 200 MHz.
 * __delay_cycles() is a clpru intrinsic that inserts an exact
 * busy-wait loop.  The compiler will NOT optimise it away.
 */
#define SAMPLE_CYCLES   2000u

/*
 * Quadrature Event Matrix – fully unrolled for zero branch cost.
 * Index = (prev_AB << 2) | curr_AB   (0..15)
 * Value = direction delta (+1 / 0 / -1)
 *
 * We use int32_t deliberately so the add to `count` is one PRU ADDS
 * instruction (no sign-extension needed).
 */
static const int32_t QEM[16] = {
/*  curr: 00   01   10   11      prev: */
        0,  -1,   1,   0,   /* 00 */
        1,   0,   0,  -1,   /* 01 */
       -1,   0,   0,   1,   /* 10 */
        0,   1,  -1,   0    /* 11 */
};

void main(void) {
    /*
     * Enable OCP master port so PRU can reach the full L3 bus.
     * Without this the PRU cannot write to DRAM addresses visible
     * to the ARM.
     */
    CT_CFG.SYSCFG_bit.STANDBY_INIT = 0;

    /* Clear DRAM – ARM may read this before we write it */
    DRAM_COUNT  = 0;
    DRAM_STATUS = 0;
    DRAM_DBG_US = SAMPLE_CYCLES / 200u;  /* = 10 µs, static debug info */
    DRAM_RSVD   = 0;

    int32_t  count   = 0;
    uint32_t prev_ab = 0;

    /* Latch initial encoder state to avoid a false edge on first sample */
    {
        uint32_t r = __R31;
        uint32_t a = (r >> ENC_A_BIT) & 1u;
        uint32_t b = (r >> ENC_B_BIT) & 1u;
        prev_ab = (a << 1u) | b;
    }

    /* Signal ARM: firmware is live and counting */
    DRAM_STATUS = 1u;

    /* ─── Main loop – runs forever, never sleeps, never preempted ─────── */
    while (1) {
        __delay_cycles(SAMPLE_CYCLES);          /* precise 10 µs period   */

        uint32_t r       = __R31;
        uint32_t a       = (r >> ENC_A_BIT) & 1u;
        uint32_t b       = (r >> ENC_B_BIT) & 1u;
        uint32_t curr_ab = (a << 1u) | b;

        /* 4X quadrature decode via lookup – one cycle, no branches */
        count += QEM[(prev_ab << 2u) | curr_ab];

        /*
         * Single 32-bit store to DRAM.
         * On ARM Cortex-A8, a 32-bit aligned store is atomic with
         * respect to 32-bit loads, so the ARM side never sees a
         * torn read.
         */
        DRAM_COUNT = count;

        prev_ab = curr_ab;
    }
}
