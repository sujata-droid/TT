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
#define CL_FILTER_TAPS       8
#define PRU_DMEM_PHYS        0x4A300000u
#define PRU_MAP_SIZE         4096u
#define ENCODER_DEFAULT_PPR  400.0
#define ENCODER_DEFAULT_WHEEL_DIAMETER_MM 250.0

static volatile sig_atomic_t g_run = 1;

static int g_shm_fd = -1;
static RailSharedFrame *g_frame = NULL;
static SCL3300 g_scl;

typedef struct {
    int mem_fd;
    volatile uint8_t *map;
    volatile int32_t *count;
    volatile uint32_t *status;
    volatile uint32_t *sample_us;
    double mm_per_count;
    int invert;
} EncoderPRU;

static EncoderPRU g_enc = {
    .mem_fd = -1,
    .map = NULL,
    .count = NULL,
    .status = NULL,
    .sample_us = NULL,
    .mm_per_count = 0.0,
    .invert = 0,
};

typedef struct { float cl_mm; float ch_m; } CLSample;
static CLSample g_hist[TWIST_HISTORY_MAX];
static uint32_t g_hist_head = 0;
static float g_cl_hist[CL_FILTER_TAPS];
static uint32_t g_cl_hist_head = 0;
static uint32_t g_cl_hist_count = 0;

static void on_signal(int s) { (void)s; g_run = 0; }

static float filter_cross_level(float cl_mm) {
    float sum = 0.0f;
    uint32_t i;

    g_cl_hist[g_cl_hist_head] = cl_mm;
    g_cl_hist_head = (g_cl_hist_head + 1u) % CL_FILTER_TAPS;
    if (g_cl_hist_count < CL_FILTER_TAPS) g_cl_hist_count++;

    for (i = 0; i < g_cl_hist_count; i++) {
        sum += g_cl_hist[i];
    }
    return sum / (float)g_cl_hist_count;
}

static int run_quiet(const char *cmd) {
    int rc = system(cmd);
    if (rc == -1) return -1;
    if (WIFEXITED(rc)) return WEXITSTATUS(rc);
    return -1;
}

static double env_double(const char *name, double fallback) {
    const char *raw = getenv(name);
    char *end = NULL;
    double value;
    if (!raw || !*raw) return fallback;
    value = strtod(raw, &end);
    if (end == raw || (end && *end != '\0') || !isfinite(value) || value <= 0.0) {
        fprintf(stderr, "[ENC] Warning: invalid %s='%s', using %.3f\n", name, raw, fallback);
        return fallback;
    }
    return value;
}

static int env_int(const char *name, int fallback) {
    const char *raw = getenv(name);
    char *end = NULL;
    long value;
    if (!raw || !*raw) return fallback;
    value = strtol(raw, &end, 10);
    if (end == raw || (end && *end != '\0')) {
        fprintf(stderr, "[ENC] Warning: invalid %s='%s', using %d\n", name, raw, fallback);
        return fallback;
    }
    return (int)value;
}

static void ensure_spi_pinmux(void) {
    if (getenv("RAIL_SKIP_PINMUX")) {
        printf("[PINMUX] Skipping SPI pinmux because RAIL_SKIP_PINMUX is set.\n");
        return;
    }

    printf("[PINMUX] Ensuring SPI0 pins are configured for SCL3300...\n");
    if (run_quiet("config-pin P9_17 spi_cs >/dev/null 2>&1") != 0)
        fprintf(stderr, "[PINMUX] Warning: failed to set P9_17 -> spi_cs\n");
    if (run_quiet("config-pin P9_18 spi >/dev/null 2>&1") != 0)
        fprintf(stderr, "[PINMUX] Warning: failed to set P9_18 -> spi\n");
    if (run_quiet("config-pin P9_21 spi >/dev/null 2>&1") != 0)
        fprintf(stderr, "[PINMUX] Warning: failed to set P9_21 -> spi\n");
    if (run_quiet("config-pin P9_22 spi_sclk >/dev/null 2>&1") != 0)
        fprintf(stderr, "[PINMUX] Warning: failed to set P9_22 -> spi_sclk\n");
    usleep(50000);
}

static void ensure_encoder_pinmux(void) {
    if (getenv("RAIL_SKIP_PINMUX")) {
        printf("[PINMUX] Skipping encoder pinmux because RAIL_SKIP_PINMUX is set.\n");
        return;
    }

    printf("[PINMUX] Ensuring PRU encoder pins are configured...\n");
    if (run_quiet("config-pin P9_27 pruin >/dev/null 2>&1") != 0)
        fprintf(stderr, "[PINMUX] Warning: failed to set P9_27 -> pruin\n");
    if (run_quiet("config-pin P9_30 pruin >/dev/null 2>&1") != 0)
        fprintf(stderr, "[PINMUX] Warning: failed to set P9_30 -> pruin\n");
    usleep(50000);
}

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

static int encoder_open(EncoderPRU *enc) {
    size_t page_size = (size_t)sysconf(_SC_PAGESIZE);
    off_t base;
    off_t offset;
    double ppr;
    double wheel_diameter_mm;
    double counts_per_rev;

    if (!enc) return -1;

    ppr = env_double("RAIL_ENCODER_PPR", ENCODER_DEFAULT_PPR);
    wheel_diameter_mm = env_double("RAIL_WHEEL_DIAMETER_MM", ENCODER_DEFAULT_WHEEL_DIAMETER_MM);
    counts_per_rev = ppr * 4.0;
    enc->invert = env_int("RAIL_ENCODER_INVERT", 0) ? 1 : 0;
    enc->mm_per_count = (M_PI * wheel_diameter_mm) / counts_per_rev;

    enc->mem_fd = open("/dev/mem", O_RDONLY | O_SYNC);
    if (enc->mem_fd < 0) {
        fprintf(stderr, "[ENC] open(/dev/mem) failed: %s\n", strerror(errno));
        return -1;
    }

    base = (off_t)(PRU_DMEM_PHYS & ~(uint32_t)(page_size - 1u));
    offset = (off_t)(PRU_DMEM_PHYS - (uint32_t)base);
    enc->map = (volatile uint8_t *)mmap(NULL, PRU_MAP_SIZE, PROT_READ, MAP_SHARED, enc->mem_fd, base);
    if (enc->map == MAP_FAILED) {
        fprintf(stderr, "[ENC] mmap(PRU DMEM) failed: %s\n", strerror(errno));
        close(enc->mem_fd);
        enc->mem_fd = -1;
        enc->map = NULL;
        return -1;
    }

    enc->count = (volatile int32_t *)(enc->map + offset + 0x00u);
    enc->status = (volatile uint32_t *)(enc->map + offset + 0x04u);
    enc->sample_us = (volatile uint32_t *)(enc->map + offset + 0x08u);

    printf("[ENC] PRU quadrature input enabled: P9_27=A, P9_30=B\n");
    printf("[ENC] Geometry: ppr=%.0f counts_per_rev=%.0f wheel_diameter_mm=%.2f mm_per_count=%.6f invert=%d\n",
           ppr, counts_per_rev, wheel_diameter_mm, enc->mm_per_count, enc->invert);
    return 0;
}

static void encoder_close(EncoderPRU *enc) {
    if (!enc) return;
    if (enc->map && enc->map != MAP_FAILED) {
        munmap((void *)enc->map, PRU_MAP_SIZE);
    }
    if (enc->mem_fd >= 0) {
        close(enc->mem_fd);
    }
    enc->mem_fd = -1;
    enc->map = NULL;
    enc->count = NULL;
    enc->status = NULL;
    enc->sample_us = NULL;
}

static int encoder_read(EncoderPRU *enc, int32_t *count_out, float *chainage_m_out, uint32_t *sample_us_out) {
    int32_t count;
    uint32_t status;
    uint32_t sample_us;
    if (!enc || !enc->count || !enc->status || !enc->sample_us) return -1;

    count = *enc->count;
    status = *enc->status;
    sample_us = *enc->sample_us;
    if (status != 1u) return -1;

    if (enc->invert) count = -count;
    if (count_out) *count_out = count;
    if (chainage_m_out) *chainage_m_out = (float)((count * enc->mm_per_count) / 1000.0);
    if (sample_us_out) *sample_us_out = sample_us;
    return 0;
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
    int enc_ok_init;
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
    ensure_spi_pinmux();
    ensure_encoder_pinmux();

    printf("=== Rail Inspection Sensor Service (shared memory) ===\n");
    printf("Gauge: %.0f mm | encoder via PRU0 | %d Hz\n",
           (double)GAUGE_MM, ACQUISITION_HZ);
    printf("[SHM] Publishing latest frame at %s\n", RAIL_SHM_PATH);

    scl_ok_init = (scl3300_open(&g_scl) == 0);
    if (!scl_ok_init) {
        fprintf(stderr, "[MAIN] SCL3300 init failed. Running with last-good zeros.\n");
    }
    enc_ok_init = (encoder_open(&g_enc) == 0);
    if (!enc_ok_init) {
        fprintf(stderr, "[MAIN] PRU encoder init failed. Running with chainage frozen at zero.\n");
    }

    clock_gettime(CLOCK_MONOTONIC, &next);
    printf("[MAIN] Running. Ctrl+C to stop.\n");

    while (g_run) {
        float cl_mm = last_cl;
        uint8_t scl_status = 0;
        float chainage_m = 0.0f;
        int32_t encoder_count = 0;
        uint8_t encoder_status = 0;
        uint32_t encoder_sample_us = 0;
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

        if (encoder_read(&g_enc, &encoder_count, &chainage_m, &encoder_sample_us) == 0) {
            encoder_status = 1u;
        }

        cl_mm = filter_cross_level(cl_mm);

        twist = first ? 0.0f : compute_twist(cl_mm, chainage_m);
        first = false;

        if (++hc_cnt >= hc_intval) {
            hc_cnt = 0;
            if (g_scl.initialized) {
                scl3300_health_check(&g_scl);
            }
            if (enc_ok_init) {
                printf("[ENC] count=%d chainage_m=%.5f sample_us=%u status=%u\n",
                       encoder_count, (double)chainage_m, encoder_sample_us, encoder_status);
            }
        }

        shm_publish(
            mono_us(),
            (double)cl_mm,
            (double)twist,
            (double)chainage_m,
            (double)GAUGE_MM,
            encoder_count,
            scl_status,
            encoder_status
        );
    }

    shm_publish(mono_us(), 0.0, 0.0, 0.0, (double)GAUGE_MM, 0, 0u, 0u);
    encoder_close(&g_enc);
    scl3300_close(&g_scl);
    shm_cleanup();
    printf("[MAIN] Clean exit.\n");
    return 0;
}
