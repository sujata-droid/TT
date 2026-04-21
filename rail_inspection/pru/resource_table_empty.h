/*
 * resource_table_empty.h  –  Minimal remoteproc resource table
 * =============================================================
 *
 * WHY THIS EXISTS
 * ---------------
 * When Linux loads PRU firmware via the remoteproc framework
 * (echo "start" > /sys/class/remoteproc/remoteprocN/state),
 * the kernel looks for an ELF section named ".resource_table"
 * at the very start of the PRU data RAM.
 *
 * This table declares what resources (shared memory, vring buffers,
 * etc.) the firmware needs.  Since our firmware is bare-metal and
 * only uses DRAM offsets we control ourselves, we declare ZERO
 * resources – the minimal valid table.
 *
 * Without this, the remoteproc driver refuses to load the firmware.
 *
 * USAGE: #include this file in encoder_pru0.c (already done).
 */

#ifndef RESOURCE_TABLE_EMPTY_H_
#define RESOURCE_TABLE_EMPTY_H_

#include <stdint.h>

/* Standard remoteproc resource table header */
struct resource_table {
    uint32_t ver;           /* Version: must be 1                 */
    uint32_t num;           /* Number of resource entries         */
    uint32_t reserved[2];   /* Must be zero                       */
    uint32_t offset[1];     /* Offsets to entries (none here)     */
};

/*
 * Place this in the .resource_table section.
 * RETAIN prevents the linker from garbage-collecting it.
 * The section must be first in PAGE 1 (see linker cmd file).
 */
#pragma DATA_SECTION(pru_remoteproc_ResourceTable, ".resource_table")
#pragma RETAIN(pru_remoteproc_ResourceTable)
struct resource_table pru_remoteproc_ResourceTable = {
    .ver        = 1,        /* version 1 – the only valid value   */
    .num        = 0,        /* no resources needed                */
    .reserved   = { 0, 0 },
    .offset     = { 0 },
};

#endif /* RESOURCE_TABLE_EMPTY_H_ */
