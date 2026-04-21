/*
 * am335x_pru0.cmd  –  Linker command file for PRU0 on AM335x
 * ============================================================
 *
 * MEMORY MAP EXPLANATION
 * ----------------------
 * The PRU-ICSS subsystem has its own internal address space, separate
 * from the ARM address space.  This file tells the linker WHERE to
 * place code and data inside the PRU's local view.
 *
 * PRU local address → ARM physical address:
 *   PRU0 IMEM (8 kB)   : PRU 0x00000000 → ARM 0x4A334000  (instruction RAM)
 *   PRU0 DRAM (4 kB)   : PRU 0x00000000 → ARM 0x4A300000  (data RAM – PAGE 1)
 *   PRU1 DRAM (4 kB)   : PRU 0x00002000 → ARM 0x4A302000
 *   Shared RAM (12 kB) : PRU 0x00010000 → ARM 0x4A310000
 *
 * CREGISTER values map to the PRU constant table (CT_*).
 * These must match the silicon manual section 4.4.2.
 */

-cr                        /* Link using C calling conventions         */
-heap  0x100               /* 256 B heap  (we use none, but linker req)*/
-stack 0x100               /* 256 B stack                              */

MEMORY {
    PAGE 0:
        /* Instruction RAM – 8 kB, PRU-local code space */
        PRU_IMEM : org = 0x00000000, len = 0x00002000

    PAGE 1:
        /* PRU0 Data RAM – 4 kB  (ARM sees this at 0x4A300000) */
        PRU_DMEM : org = 0x00000000, len = 0x00001000,
                   CREGISTER = 24

        /* PRUSS Shared RAM – 12 kB (ARM sees at 0x4A310000) */
        PRU_SHARED : org = 0x00010000, len = 0x00003000,
                     CREGISTER = 28

    PAGE 2:
        /* PRU-ICSS CFG registers (for CT_CFG / OCP enable) */
        PRU_CFG  : org = 0x00026000, len = 0x00000044,
                   CREGISTER = 4
}

SECTIONS {
    /* Executable code → instruction RAM */
    .text           >  PRU_IMEM,    PAGE 0

    /*
     * Resource table MUST be first in data RAM so remoteproc can find
     * it at a known offset when loading the firmware via /lib/firmware/.
     */
    .resource_table >  PRU_DMEM,    PAGE 1

    /* All data sections → PRU0 DRAM */
    .stack          >  PRU_DMEM,    PAGE 1
    .bss            >  PRU_DMEM,    PAGE 1
    .data           >  PRU_DMEM,    PAGE 1
    .rodata         >  PRU_DMEM,    PAGE 1
    .cinit          >  PRU_DMEM,    PAGE 1
    .cio            >  PRU_DMEM,    PAGE 1
    .switch         >  PRU_DMEM,    PAGE 1
    .sysmem         >  PRU_DMEM,    PAGE 1
}
