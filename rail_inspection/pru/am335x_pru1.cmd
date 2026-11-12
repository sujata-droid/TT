/*
 * am335x_pru1.cmd  –  Linker command file for PRU1 on AM335x
 * ============================================================
 * PRU1 local view:
 *   PRU1 IMEM (8 kB)   : PRU 0x00000000 -> ARM 0x4A338000  (instruction RAM)
 *   PRU1 DRAM (4 kB)   : PRU 0x00000000 -> ARM 0x4A302000  (data RAM – PAGE 1, C25)
 *   Shared RAM (12 kB) : PRU 0x00010000 -> ARM 0x4A310000  (PAGE 1, C28)
 */

-cr                        /* Link using C calling conventions         */
-heap  0x100               /* 256 B heap                               */
-stack 0x100               /* 256 B stack                              */

MEMORY {
    PAGE 0:
        /* Instruction RAM – 8 kB */
        PRU_IMEM : org = 0x00000000, len = 0x00002000

    PAGE 1:
        /* PRU1 Data RAM – 4 kB  (ARM sees this at 0x4A302000) */
        PRU_DMEM : org = 0x00000000, len = 0x00001000,
                   CREGISTER = 25

        /* PRUSS Shared RAM – 12 kB (ARM sees at 0x4A310000) */
        PRU_SHARED : org = 0x00010000, len = 0x00003000,
                     CREGISTER = 28

    PAGE 2:
        /* PRU-ICSS CFG registers (for CT_CFG / OCP enable) */
        PRU_CFG  : org = 0x00026000, len = 0x00000044,
                   CREGISTER = 4
}

SECTIONS {
    /* Executable code -> instruction RAM */
    .text           >  PRU_IMEM,    PAGE 0

    /* Resource table must be first in PRU1 data RAM */
    .resource_table >  PRU_DMEM,    PAGE 1

    /* All data sections -> PRU1 DRAM */
    .stack          >  PRU_DMEM,    PAGE 1
    .bss            >  PRU_DMEM,    PAGE 1
    .data           >  PRU_DMEM,    PAGE 1
    .rodata         >  PRU_DMEM,    PAGE 1
    .cinit          >  PRU_DMEM,    PAGE 1
    .cio            >  PRU_DMEM,    PAGE 1
    .switch         >  PRU_DMEM,    PAGE 1
    .sysmem         >  PRU_DMEM,    PAGE 1
}

