/*
 * ring_buffer.h  –  Lock-Free Single-Producer Single-Consumer Ring Buffer
 * =========================================================================
 *
 * WHY LOCK-FREE?
 * --------------
 * Our acquisition thread (SCHED_FIFO prio 80) MUST NOT be blocked by a
 * mutex held by the server thread (prio 60).  If the server thread is
 * descheduled while holding the lock, the acquisition loop misses its
 * 20 ms deadline → corrupted chainage, wrong cross-level.
 *
 * Solution: SPSC lock-free ring buffer using C11 atomic_store/load.
 * Rule: ONLY ONE THREAD writes (producer), ONLY ONE THREAD reads (consumer).
 * No mutex needed.  Correct on ARM with relaxed/release/acquire ordering.
 *
 * MEMORY ORDERING EXPLANATION
 * ---------------------------
 *   Producer writes data[]  →  atomic_store head (release)
 *   Consumer  atomic_load head (acquire)  →  reads data[]
 *
 * "release" ensures all writes to data[] are visible before head is updated.
 * "acquire" ensures that once head is read, the data[] writes are visible.
 * This is the minimum ordering required for correctness on ARM.
 *
 * SIZE: must be a power of 2 so we can use (index & MASK) instead of modulo.
 * 256 entries × 32 bytes = 8 kB – fits in L1 data cache.
 */

#ifndef RING_BUFFER_H_
#define RING_BUFFER_H_

#include <stdint.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <string.h>

/* ── Buffer size ─────────────────────────────────────────────────────── */
#define RB_SIZE    256u                /* MUST be power of 2             */
#define RB_MASK    (RB_SIZE - 1u)     /* fast modulo: idx & RB_MASK      */

/* ── Sensor data frame ───────────────────────────────────────────────── */
/*
 * One complete measurement sample.
 * Packed to 32 bytes so it fits in one cache line (typical = 64 B,
 * so two frames per line – minimal cache pressure on the ARM side).
 *
 * Field meanings:
 *   timestamp_us   – monotonic clock, microseconds since service start
 *   cross_level_mm – lateral rail inclination in mm (from SCL3300 X axis)
 *   twist_mm_per_m – rate of cross-level change per metre travelled
 *   chainage_m     – distance from survey start (from PRU encoder)
 *   gauge_mm       – always 1676 for Indian BG rail
 *   scl3300_ok     – 1 = sensor healthy (RS=01), 0 = fault / CRC error
 *   encoder_ok     – 1 = PRU firmware running, 0 = PRU not loaded
 */
typedef struct __attribute__((packed)) {
    int64_t  timestamp_us;      /*  8 bytes */
    float    cross_level_mm;    /*  4 bytes */
    float    twist_mm_per_m;    /*  4 bytes */
    float    chainage_m;        /*  4 bytes */
    float    gauge_mm;          /*  4 bytes */
    uint8_t  scl3300_ok;        /*  1 byte  */
    uint8_t  encoder_ok;        /*  1 byte  */
    uint8_t  _pad[6];           /*  6 bytes → total 32 bytes */
} SensorFrame;                  /* sizeof = 32 bytes, cache-friendly     */

/* ── Ring buffer struct ──────────────────────────────────────────────── */
/*
 * head: written only by producer (acquisition thread)
 * tail: written only by consumer (server thread)
 *
 * Padding between head and tail prevents false sharing:
 * both variables on the same cache line would cause cache bouncing
 * between the two CPU cores running the two threads.
 * (BBB is single-core, but the padding is good practice.)
 */
typedef struct {
    _Atomic(uint32_t) head;          /* next slot to write               */
    uint8_t           _pad0[60];     /* pad to 64-byte cache line        */
    _Atomic(uint32_t) tail;          /* next slot to read                */
    uint8_t           _pad1[60];     /* pad to 64-byte cache line        */
    SensorFrame       buf[RB_SIZE];  /* circular data array              */
} RingBuffer;

/* ── API ─────────────────────────────────────────────────────────────── */

/* Initialise (call once before starting threads) */
static inline void rb_init(RingBuffer *rb) {
    atomic_store_explicit(&rb->head, 0u, memory_order_relaxed);
    atomic_store_explicit(&rb->tail, 0u, memory_order_relaxed);
}

/* Returns number of frames available to read */
static inline uint32_t rb_count(const RingBuffer *rb) {
    uint32_t h = atomic_load_explicit(&rb->head, memory_order_acquire);
    uint32_t t = atomic_load_explicit(&rb->tail, memory_order_relaxed);
    return h - t;   /* wraps correctly for uint32_t arithmetic */
}

/* Returns true if buffer is empty */
static inline bool rb_empty(const RingBuffer *rb) {
    return rb_count(rb) == 0u;
}

/*
 * rb_push() – Producer only.
 * Writes frame to the next slot.
 * If the buffer is FULL, drops the OLDEST frame (overwrites tail) so
 * the acquisition loop NEVER blocks.  The server thread gets the
 * newest data even if it falls behind temporarily.
 *
 * Returns true always (never fails – oldest is silently dropped).
 */
static inline bool rb_push(RingBuffer *rb, const SensorFrame *frame) {
    uint32_t h = atomic_load_explicit(&rb->head, memory_order_relaxed);
    uint32_t t = atomic_load_explicit(&rb->tail, memory_order_acquire);

    /* If full, advance tail to make room (drops oldest) */
    if ((h - t) >= RB_SIZE) {
        atomic_store_explicit(&rb->tail, t + 1u, memory_order_relaxed);
    }

    memcpy(&rb->buf[h & RB_MASK], frame, sizeof(SensorFrame));

    /* Release: ensures data write is visible before head increment */
    atomic_store_explicit(&rb->head, h + 1u, memory_order_release);
    return true;
}

/*
 * rb_pop() – Consumer only.
 * Copies next frame into *out_frame.
 * Returns true on success, false if empty.
 */
static inline bool rb_pop(RingBuffer *rb, SensorFrame *out_frame) {
    uint32_t t = atomic_load_explicit(&rb->tail, memory_order_relaxed);
    uint32_t h = atomic_load_explicit(&rb->head, memory_order_acquire);

    if (t == h) return false;   /* empty */

    memcpy(out_frame, &rb->buf[t & RB_MASK], sizeof(SensorFrame));

    /* Release: ensures copy is done before tail advances */
    atomic_store_explicit(&rb->tail, t + 1u, memory_order_release);
    return true;
}

#endif /* RING_BUFFER_H_ */
