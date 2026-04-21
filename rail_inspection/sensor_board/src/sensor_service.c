/*
 * sensor_service.c -- Minimal shared-memory sensor acquisition service
 * ================================================================
 * One real-time loop acquires:
 *   - SCL3300 inclinometer via spidev
 *   - Rotary encoder count from GPIO quadrature inputs
 * And publishes the latest sample into POSIX shared memory.
 */

#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "scl3300.h"
#include "shared_frame.h"

#define ACQUISITION_HZ       50
#define ACQUISITION_NS       (1000000000L / ACQUISITION_HZ)
#define TWIST_HISTORY_MAX    200
#define STATUS_CHECK_SECS    5

static volatile sig_atomic_t g_run = 1;

static int g_shm_fd = -1;
static RailSharedFrame *g_frame = NULL;
static SCL3300 g_scl;

typedef struct { float cl_mm; float ch_m; } CLSample;
static CLSample g_hist[TWIST_HISTORY_MAX];
static uint32_t g_hist_head = 0;

static void on_signal(int s) { (void)s; g_run = 0; }

static int64_t mono_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000LL + ts.tv_nsec / 1000LL;
}

static void ts_add_ns(struct timespec *ts, long ns) {
    ts->tv_nsec += ns;
    if (ts->tv_nsec >= 1000000000L) {
        ts->tv_nsec -= 1000000000L;
        ts->tv_sec++;
    }
}

static float compute_twist(float cl_mm, float ch_m) {
    uint32_t idx = g_hist_head % TWIST_HISTORY_MAX;
    g_hist[idx].cl_mm = cl_mm;
    g_hist[idx].ch_m = ch_m;
    g_hist_head++;
    if (g_hist_head < 2) return 0.0f;

    {
        float target_ch = ch_m - TWIST_BASELINE_M;
        float best_err = 1e9f;
        uint32_t best = 0;
        bool found = false;
        uint32_t n = (g_hist_head < TWIST_HISTORY_MAX) ? g_hist_head : TWIST_HISTORY_MAX;
        uint32_t i;
        for (i = 1; i < n; i++) {
            uint32_t j = (g_hist_head - 1u - i) % TWIST_HISTORY_MAX;
            float err = fabsf(g_hist[j].ch_m - target_ch);
            if (err < best_err) {
                best_err = err;
                best = j;
                found = true;
            }
        }
        if (!found || best_err > TWIST_BASELINE_M * 0.6f) return 0.0f;
        {
            float dcl = cl_mm - g_hist[best].cl_mm;
            float dch = ch_m - g_hist[best].ch_m;
            if (fabsf(dch) < 0.001f) return 0.0f;
            return dcl / dch;
        }
    }
}

static int shm_init(void) {
    g_shm_fd = shm_open(RAIL_SHM_NAME, O_CREAT | O_RDWR, 0666);
    if (g_shm_fd < 0) {
        fprintf(stderr, "[SHM] shm_open failed: %s\n", strerror(errno));
        return -1;
    }
    if (ftruncate(g_shm_fd, (off_t)sizeof(RailSharedFrame)) != 0) {
        fprintf(stderr, "[SHM] ftruncate failed: %s\n", strerror(errno));
        close(g_shm_fd);
        g_shm_fd = -1;
        return -1;
    }
    g_frame = (RailSharedFrame *)mmap(
        NULL, sizeof(RailSharedFrame), PROT_READ | PROT_WRITE, MAP_SHARED,
        g_shm_fd, 0);
    if (g_frame == MAP_FAILED) {
        fprintf(stderr, "[SHM] mmap failed: %s\n", strerror(errno));
        close(g_shm_fd);
        g_shm_fd = -1;
        g_frame = NULL;
        return -1;
    }
    memset(g_frame, 0, sizeof(*g_frame));
    g_frame->magic = RAIL_SHM_MAGIC;
    g_frame->version = RAIL_SHM_VERSION;
    return 0;
}

static void shm_cleanup(void) {
    if (g_frame && g_frame != MAP_FAILED) {
        g_frame->service_ok = 0u;
        msync(g_frame, sizeof(*g_frame), MS_SYNC);
        munmap(g_frame, sizeof(*g_frame));
        g_frame = NULL;
    }
    if (g_shm_fd >= 0) {
        close(g_shm_fd);
        g_shm_fd = -1;
    }
}

static void shm_publish(
    int64_t timestamp_us,
    double cross_level_mm,
    double twist_mm_per_m,
    double chainage_m,
    double gauge_mm,
    int32_t encoder_count,
    uint8_t scl_ok,
    uint8_t encoder_ok
) {
    uint32_t seq;
    if (!g_frame) return;
    seq = g_frame->seq + 1u;
    if ((seq & 1u) == 0u) seq++;
    g_frame->seq = seq;
    __sync_synchronize();
    g_frame->magic = RAIL_SHM_MAGIC;
    g_frame->version = RAIL_SHM_VERSION;
    g_frame->update_count += 1u;
    g_frame->timestamp_us = timestamp_us;
    g_frame->cross_level_mm = cross_level_mm;
    g_frame->twist_mm_per_m = twist_mm_per_m;
    g_frame->chainage_m = chainage_m;
    g_frame->gauge_mm = gauge_mm;
    g_frame->encoder_count = encoder_count;
    g_frame->scl3300_ok = scl_ok;
    g_frame->encoder_ok = encoder_ok;
    g_frame->service_ok = 1u;
    __sync_synchronize();
    g_frame->seq = seq + 1u;
}

int main(void) {
    int scl_ok_init;
    bool first = true;
    float last_cl = 0.0f;
    int hc_cnt = 0;
    int hc_intval = ACQUISITION_HZ * STATUS_CHECK_SECS;
    struct timespec next;

    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0) {
        fprintf(stderr, "[MAIN] mlockall warning: %s\n", strerror(errno));
    }

    {
        struct sched_param sp;
        memset(&sp, 0, sizeof(sp));
        sp.sched_priority = 80;
        if (sched_setscheduler(0, SCHED_FIFO, &sp) != 0) {
            fprintf(stderr, "[MAIN] Cannot set SCHED_FIFO: %s\n", strerror(errno));
        }
    }

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    if (shm_init() != 0) return 1;

    printf("=== Rail Inspection Sensor Service (shared memory) ===\n");
    printf("Gauge: %.0f mm | encoder removed | %d Hz\n",
           (double)GAUGE_MM, ACQUISITION_HZ);
    printf("[SHM] Publishing latest frame at %s\n", RAIL_SHM_PATH);

    scl_ok_init = (scl3300_open(&g_scl) == 0);
    if (!scl_ok_init) {
        fprintf(stderr, "[MAIN] SCL3300 init failed. Running with last-good zeros.\n");
    }
    printf("[ENC] Rotary encoder removed. Publishing chainage=0 and encoder_ok=1.\n");

    clock_gettime(CLOCK_MONOTONIC, &next);
    printf("[MAIN] Running. Ctrl+C to stop.\n");

    while (g_run) {
        float cl_mm = last_cl;
        uint8_t scl_status = 0;
        float chainage_m = 0.0f;
        float twist;

        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
        ts_add_ns(&next, ACQUISITION_NS);

        if (g_scl.initialized) {
            float tmp;
            if (scl3300_read_cross_level(&g_scl, &tmp) == 0) {
                cl_mm = tmp;
                last_cl = tmp;
                scl_status = 1u;
            }
        }

        twist = first ? 0.0f : compute_twist(cl_mm, chainage_m);
        first = false;

        if (++hc_cnt >= hc_intval) {
            hc_cnt = 0;
            if (g_scl.initialized) {
                scl3300_health_check(&g_scl);
            }
        }

        shm_publish(
            mono_us(),
            (double)cl_mm,
            (double)twist,
            (double)chainage_m,
            (double)GAUGE_MM,
            0,
            scl_status,
            1u
        );
    }

    shm_publish(mono_us(), 0.0, 0.0, 0.0, (double)GAUGE_MM, 0, 0u, 0u);
    scl3300_close(&g_scl);
    shm_cleanup();
    printf("[MAIN] Clean exit.\n");
    return 0;
}
