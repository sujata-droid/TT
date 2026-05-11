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
#include <limits.h>
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

#define ACQUISITION_HZ       100
#define ACQUISITION_NS       (1000000000L / ACQUISITION_HZ)
#define SCL3300_READ_HZ      10
#define SCL3300_READ_DIV     (ACQUISITION_HZ / SCL3300_READ_HZ)
#define TWIST_HISTORY_MAX    200
#define STATUS_CHECK_SECS    5
#define CL_FILTER_TAPS       8
#define PRU_DMEM_PHYS        0x4A300000u
#define PRU_MAP_SIZE         4096u
#define ENCODER_DEFAULT_PPR  400.0
#define ENCODER_DEFAULT_WHEEL_DIAMETER_MM 250.0
#define ENCODER_COUNT_COOKIE 0xA5A5A5A5u
#define ENCODER_MAX_DELTA_DEFAULT 0
#define ADC_PATH_DEFAULT "/sys/bus/iio/devices/iio:device0/in_voltage0_raw"
#define GAUGE_ZERO_DEFAULT 2048.0
#define GAUGE_MPC_DEFAULT  0.0684
#define GAUGE_FACTOR_DEFAULT 1.0
#define GAUGE_MIN_MM_DEFAULT (GAUGE_MM - 25.0)
#define GAUGE_MAX_MM_DEFAULT (GAUGE_MM + 50.0)
#define TWIST_SAMPLE_STEP_DEFAULT TWIST_SAMPLE_STEP_M
#define TWIST_BASELINE_DEFAULT    TWIST_BASELINE_M

static volatile sig_atomic_t g_run = 1;

static int g_shm_fd = -1;
static RailSharedFrame *g_frame = NULL;
static SCL3300 g_scl;

typedef struct {
    char path[256];
    double zero_raw;
    double mm_per_count;
    double factor;
    double min_mm;
    double max_mm;
    int healthy;
} GaugeADC;

static GaugeADC g_gauge = {
    .path = ADC_PATH_DEFAULT,
    .zero_raw = GAUGE_ZERO_DEFAULT,
    .mm_per_count = GAUGE_MPC_DEFAULT,
    .factor = GAUGE_FACTOR_DEFAULT,
    .min_mm = GAUGE_MIN_MM_DEFAULT,
    .max_mm = GAUGE_MAX_MM_DEFAULT,
    .healthy = 0,
};

typedef struct {
    int mem_fd;
    volatile uint8_t *map;
    volatile int32_t *count;
    volatile uint32_t *status;
    volatile uint32_t *sample_us;
    volatile uint32_t *count_check;
    double mm_per_count;
    int invert;
    int max_delta;
    int have_last_count;
    int32_t last_count;
    int have_zero_count;
    int32_t zero_count;
} EncoderPRU;

static EncoderPRU g_enc = {
    .mem_fd = -1,
    .map = NULL,
    .count = NULL,
    .status = NULL,
    .sample_us = NULL,
    .mm_per_count = 0.0,
    .invert = 0,
    .max_delta = ENCODER_MAX_DELTA_DEFAULT,
    .have_last_count = 0,
    .last_count = 0,
    .have_zero_count = 0,
    .zero_count = 0,
};

typedef struct { float cl_mm; float ch_m; } CLSample;
static CLSample g_hist[TWIST_HISTORY_MAX];
static uint32_t g_hist_head = 0;
static int32_t g_hist_step_idx[TWIST_HISTORY_MAX];
static float g_cl_hist[CL_FILTER_TAPS];
static uint32_t g_cl_hist_head = 0;
static uint32_t g_cl_hist_count = 0;
static float g_twist_sample_step_m = TWIST_SAMPLE_STEP_DEFAULT;
static float g_twist_baseline_m = TWIST_BASELINE_DEFAULT;
static int32_t g_twist_baseline_steps = 0;
static int g_twist_anchor_valid = 0;
static int32_t g_twist_anchor_step = 0;

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

static void gauge_init(void) {
    const char *raw_path = getenv("RAIL_ADC_PATH");
    if (raw_path && *raw_path) {
        snprintf(g_gauge.path, sizeof(g_gauge.path), "%s", raw_path);
    }
    g_gauge.zero_raw = env_double("RAIL_GAUGE_ZERO_RAW", GAUGE_ZERO_DEFAULT);
    g_gauge.mm_per_count = env_double("RAIL_GAUGE_MPC", GAUGE_MPC_DEFAULT);
    g_gauge.factor = env_double("RAIL_GAUGE_FACTOR", GAUGE_FACTOR_DEFAULT);
    g_gauge.min_mm = env_double("RAIL_GAUGE_MIN_MM", GAUGE_MIN_MM_DEFAULT);
    g_gauge.max_mm = env_double("RAIL_GAUGE_MAX_MM", GAUGE_MAX_MM_DEFAULT);
    if (g_gauge.min_mm > g_gauge.max_mm) {
        double tmp = g_gauge.min_mm;
        g_gauge.min_mm = g_gauge.max_mm;
        g_gauge.max_mm = tmp;
    }
    printf("[GAUGE] path=%s zero=%.2f mpc=%.6f factor=%.3f nominal=%.1f range=[%.1f, %.1f]\n",
           g_gauge.path, g_gauge.zero_raw, g_gauge.mm_per_count, g_gauge.factor,
           (double)GAUGE_MM, g_gauge.min_mm, g_gauge.max_mm);
}

static int gauge_read_raw(const GaugeADC *gauge, int *raw_out) {
    FILE *fp;
    int raw;
    if (!gauge || !raw_out) return -1;
    fp = fopen(gauge->path, "r");
    if (!fp) return -1;
    if (fscanf(fp, "%d", &raw) != 1) {
        fclose(fp);
        return -1;
    }
    fclose(fp);
    *raw_out = raw;
    return 0;
}

static int gauge_read_mm(GaugeADC *gauge, float *gauge_mm_out) {
    int raw;
    double gauge_mm;
    if (!gauge || !gauge_mm_out) return -1;
    if (gauge_read_raw(gauge, &raw) != 0) {
        gauge->healthy = 0;
        return -1;
    }
    gauge->healthy = 1;
    gauge_mm = GAUGE_MM + ((double)raw - gauge->zero_raw) * gauge->mm_per_count * gauge->factor;
    if (gauge_mm < gauge->min_mm) gauge_mm = gauge->min_mm;
    if (gauge_mm > gauge->max_mm) gauge_mm = gauge->max_mm;
    *gauge_mm_out = (float)gauge_mm;
    return 0;
}

static float normalize_sampling_step(float value) {
    float steps;
    float snapped;
    if (!isfinite(value) || value <= 0.0f) return TWIST_SAMPLE_STEP_DEFAULT;
    steps = value / 0.25f;
    snapped = roundf(steps) * 0.25f;
    if (snapped < 0.25f) snapped = 0.25f;
    return snapped;
}

static float normalize_twist_baseline(float value) {
    float steps;
    float snapped;
    if (!isfinite(value) || value <= 0.0f) return TWIST_BASELINE_DEFAULT;
    if (value < 2.0f) value = 2.0f;
    if (value > 4.0f) value = 4.0f;
    steps = value / 0.25f;
    snapped = roundf(steps) * 0.25f;
    if (snapped < 2.0f) snapped = 2.0f;
    if (snapped > 4.0f) snapped = 4.0f;
    return snapped;
}

static void twist_config_init(void) {
    float sample_step = (float)env_double("RAIL_SAMPLING_DISTANCE_M", TWIST_SAMPLE_STEP_DEFAULT);
    float baseline = (float)env_double("RAIL_TWIST_BASE_M", TWIST_BASELINE_DEFAULT);

    g_twist_sample_step_m = normalize_sampling_step(sample_step);
    g_twist_baseline_m = normalize_twist_baseline(baseline);
    g_twist_baseline_steps = (int32_t)lroundf(g_twist_baseline_m / g_twist_sample_step_m);
    if (g_twist_baseline_steps < 1) g_twist_baseline_steps = 1;

    printf("[TWIST] sample_step=%.2f m baseline=%.2f m baseline_steps=%d\n",
           (double)g_twist_sample_step_m, (double)g_twist_baseline_m, g_twist_baseline_steps);
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
    enc->max_delta = env_int("RAIL_ENCODER_MAX_DELTA", ENCODER_MAX_DELTA_DEFAULT);
    if (enc->max_delta < 0) enc->max_delta = ENCODER_MAX_DELTA_DEFAULT;
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
    enc->count_check = (volatile uint32_t *)(enc->map + offset + 0x0Cu);

    printf("[ENC] PRU quadrature input enabled: P9_27=A, P9_30=B\n");
    printf("[ENC] Geometry: ppr=%.0f counts_per_rev=%.0f wheel_diameter_mm=%.2f mm_per_count=%.6f invert=%d max_delta=%d\n",
           ppr, counts_per_rev, wheel_diameter_mm, enc->mm_per_count, enc->invert, enc->max_delta);
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
    enc->count_check = NULL;
    enc->have_last_count = 0;
    enc->last_count = 0;
    enc->have_zero_count = 0;
    enc->zero_count = 0;
}

static int encoder_read(EncoderPRU *enc, int32_t *count_out, float *chainage_m_out, uint32_t *sample_us_out) {
    int32_t count;
    uint32_t status;
    uint32_t sample_us;
    uint32_t count_check;
    int attempt;
    if (!enc || !enc->count || !enc->status || !enc->sample_us || !enc->count_check) return -1;

    for (attempt = 0; attempt < 8; ++attempt) {
        count = *enc->count;
        status = *enc->status;
        sample_us = *enc->sample_us;
        count_check = *enc->count_check;
        if ((((uint32_t)count) ^ ENCODER_COUNT_COOKIE) == count_check) {
            break;
        }
    }

    if ((((uint32_t)count) ^ ENCODER_COUNT_COOKIE) != count_check) return -1;
    if (status != 1u) return -1;

    if (enc->invert) count = -count;
    if (!enc->have_zero_count) {
        enc->zero_count = count;
        enc->have_zero_count = 1;
    }
    count -= enc->zero_count;
    if (enc->have_last_count && enc->max_delta > 0) {
        int32_t delta = count - enc->last_count;
        if (delta > enc->max_delta || delta < -enc->max_delta) {
            count = enc->last_count;
        }
    }
    enc->last_count = count;
    enc->have_last_count = 1;
    if (count_out) *count_out = count;
    if (chainage_m_out) *chainage_m_out = (float)((count * enc->mm_per_count) / 1000.0);
    if (sample_us_out) *sample_us_out = sample_us;
    return 0;
}

static float compute_twist(float cl_mm, float ch_m) {
    uint32_t idx = g_hist_head % TWIST_HISTORY_MAX;
    int32_t current_step_index;
    int32_t relative_step;
    int32_t abs_relative_step;
    int32_t direction;
    int32_t target_step;

    if (g_twist_sample_step_m <= 0.0f || g_twist_baseline_steps < 1) return 0.0f;

    current_step_index = (int32_t)lroundf(ch_m / g_twist_sample_step_m);
    if (!g_twist_anchor_valid) {
        g_twist_anchor_step = current_step_index;
        g_twist_anchor_valid = 1;
    }
    if (g_hist_head > 0) {
        uint32_t last_idx = (g_hist_head - 1u) % TWIST_HISTORY_MAX;
        if (g_hist_step_idx[last_idx] == current_step_index) {
            g_hist[last_idx].cl_mm = cl_mm;
            g_hist[last_idx].ch_m = ch_m;
        } else {
            g_hist[idx].cl_mm = cl_mm;
            g_hist[idx].ch_m = ch_m;
            g_hist_step_idx[idx] = current_step_index;
            g_hist_head++;
        }
    } else {
        g_hist[idx].cl_mm = cl_mm;
        g_hist[idx].ch_m = ch_m;
        g_hist_step_idx[idx] = current_step_index;
        g_hist_head++;
    }
    if (g_hist_head < 2) return 0.0f;
    relative_step = current_step_index - g_twist_anchor_step;
    abs_relative_step = abs(relative_step);
    if (abs_relative_step < g_twist_baseline_steps) return 0.0f;
    if ((abs_relative_step % g_twist_baseline_steps) != 0) return 0.0f;
    direction = (relative_step >= 0) ? 1 : -1;
    target_step = current_step_index - (direction * g_twist_baseline_steps);

    {
        uint32_t best = 0;
        bool found = false;
        uint32_t n = (g_hist_head < TWIST_HISTORY_MAX) ? g_hist_head : TWIST_HISTORY_MAX;
        uint32_t i;

        for (i = 1; i < n; i++) {
            uint32_t j = (g_hist_head - 1u - i) % TWIST_HISTORY_MAX;
            if (g_hist_step_idx[j] == target_step) {
                best = j;
                found = true;
                break;
            }
        }
        if (!found) return 0.0f;
        {
            float dcl = cl_mm - g_hist[best].cl_mm;
            /* Use the fixed project chord length:
             * twist = (C1 - C2) / L
             * where L is the selected twist baseline.
             */
            return dcl / g_twist_baseline_m;
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
    float last_gauge_mm = GAUGE_MM;
    uint8_t last_scl_status = 0u;
    int hc_cnt = 0;
    int hc_intval = ACQUISITION_HZ * STATUS_CHECK_SECS;
    int scl_read_div = SCL3300_READ_DIV > 0 ? SCL3300_READ_DIV : 1;
    int scl_read_cnt = 0;
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
    twist_config_init();
    gauge_init();
    ensure_spi_pinmux();
    ensure_encoder_pinmux();

    printf("=== Rail Inspection Sensor Service (shared memory) ===\n");
    printf("Gauge: live ADC around %.0f mm | encoder via PRU0 | %d Hz\n",
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
        float gauge_mm = last_gauge_mm;
        uint8_t scl_status = last_scl_status;
        float chainage_m = 0.0f;
        int32_t encoder_count = 0;
        uint8_t encoder_status = 0;
        uint32_t encoder_sample_us = 0;
        float twist;

        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
        ts_add_ns(&next, ACQUISITION_NS);

        if (g_scl.initialized && (++scl_read_cnt >= scl_read_div)) {
            float tmp;
            scl_read_cnt = 0;
            if (scl3300_read_cross_level(&g_scl, &tmp) == 0) {
                cl_mm = tmp;
                last_cl = tmp;
                scl_status = 1u;
                last_scl_status = 1u;
            } else {
                scl_status = 0u;
                last_scl_status = 0u;
            }
        }

        if (encoder_read(&g_enc, &encoder_count, &chainage_m, &encoder_sample_us) == 0) {
            encoder_status = 1u;
        }

        if (gauge_read_mm(&g_gauge, &gauge_mm) == 0) {
            last_gauge_mm = gauge_mm;
        }

        cl_mm = filter_cross_level(cl_mm);

        twist = first ? 0.0f : compute_twist(cl_mm, chainage_m);
        first = false;

        if (++hc_cnt >= hc_intval) {
            hc_cnt = 0;
            /*
             * Do not send extra READ_STATUS frames while the acquisition loop is
             * running. The SCL3300 response is pipelined, and interleaving a
             * health-check command stream with ACC_X reads can leave subsequent
             * samples with RS=0x03 even though the sensor initialized correctly.
             * Live health is tracked from the normal ACC_X read result above.
             */
            if (enc_ok_init) {
                printf("[ENC] count=%d chainage_m=%.5f sample_us=%u status=%u\n",
                       encoder_count, (double)chainage_m, encoder_sample_us, encoder_status);
            }
            printf("[GAUGE] mm=%.2f adc_ok=%d\n", (double)gauge_mm, g_gauge.healthy);
        }

        shm_publish(
            mono_us(),
            (double)cl_mm,
            (double)twist,
            (double)chainage_m,
            (double)gauge_mm,
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
