/*
 * sensor_service.c -- Minimal shared-memory sensor acquisition service
 * ================================================================
 * One real-time loop acquires:
 *   - SCL3300 inclinometer via spidev
 *   - Rotary encoder count from GPIO quadrature inputs
 * And publishes the latest sample into POSIX shared memory.
 */

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <math.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>
#include <linux/gpio.h>

#include "scl3300.h"
#include "shared_frame.h"

#define ACQUISITION_HZ       100
#define ACQUISITION_NS       (1000000000L / ACQUISITION_HZ)
#define SCL3300_READ_HZ      10
#define SCL3300_READ_DIV     (ACQUISITION_HZ / SCL3300_READ_HZ)
#define GAUGE_READ_HZ        10
#define GAUGE_READ_DIV       (ACQUISITION_HZ / GAUGE_READ_HZ)
#define TWIST_HISTORY_MAX    200
#define STATUS_CHECK_SECS    5
#define CL_FILTER_TAPS       10
/* PCB encoder wiring: P8_15 = gpio1[15] (A), P8_12 = gpio1[12] (B). */
#define ENCODER_GPIO_CHIP    "/dev/gpiochip1"
#define ENCODER_GPIO_A_OFFSET 15u
#define ENCODER_GPIO_B_OFFSET 12u
#define AM335X_GPIO1_BASE     0x4804C000u
#define AM335X_GPIO1_SIZE     0x1000u
#define AM335X_GPIO_DATAIN_OFF (0x138u / 4u)
#define ENCODER_EVENT_BATCH         64
#define ENCODER_MIN_EDGE_US_DEFAULT 25
#define ENCODER_STORM_WINDOW_NS     10000000ULL
#define ENCODER_STORM_MAX_EVENTS    256
#define ENCODER_STORM_YIELD_NS      2000000L
/* Count both edges of B and sample A for direction: 400 PPR -> 800 counts/rev. */
#define ENCODER_DECODE        2.0
#define ENCODER_DEFAULT_PPR  400.0
#define ENCODER_DEFAULT_WHEEL_DIAMETER_MM 50.0
#define ENCODER_MAX_DELTA_DEFAULT 0
#define ADC_PATH_DEFAULT "/sys/bus/iio/devices/iio:device0/in_voltage0_raw"
#define GAUGE_ZERO_DEFAULT 2048.0
#define GAUGE_MPC_DEFAULT  0.0684
#define GAUGE_FACTOR_DEFAULT 1.0
#define GAUGE_MIN_MM_DEFAULT (GAUGE_MM - 25.0)
#define GAUGE_MAX_MM_DEFAULT (GAUGE_MM + 50.0)
#define GAUGE_ADC_MAX_RAW_DEFAULT 4095.0
#define LASER_MIN_MM_DEFAULT 120.0
#define LASER_MAX_MM_DEFAULT 280.0
#define LASER_ZERO_MM_DEFAULT 200.0
#define LASER_GAUGE_CENTER_MM_DEFAULT 200.0
#define LASER_DISPLAY_MIN_MM_DEFAULT 120.0
#define LASER_DISPLAY_MAX_MM_DEFAULT 280.0
#define LASER_DISPLAY_OFFSET_MM_DEFAULT 1523.5
#define LASER_RAW_AT_PLUS80_DEFAULT 2957.5
#define LASER_RAW_AT_ZERO_DEFAULT 1365.0
#define LASER_RAW_AT_MINUS80_DEFAULT 0.0
#define LASER_CAL_RAW_AT_PLUS80 (1.35 / 1.8 * 4095.0)
#define LASER_CAL_RAW_AT_ZERO (LASER_CAL_RAW_AT_PLUS80 / 2.0)
#define LASER_ENDPOINT_TOLERANCE_PCT 1.0
#define LASER_CAL_FACTOR_MIN (1.0 / 1.15)
#define LASER_CAL_FACTOR_MAX (1.0 / 0.85)
/* The 160..240 mm Track Gauge window uses fixed calibration endpoints (+40 / -40). */
#define LASER_AUTO_ZERO_DEFAULT 0
#define TWIST_SAMPLE_STEP_DEFAULT TWIST_SAMPLE_STEP_M
#define TWIST_BASELINE_DEFAULT    TWIST_BASELINE_M

static volatile sig_atomic_t g_run = 1;

static int g_shm_fd = -1;
static RailSharedFrame *g_frame = NULL;
static SCL3300 g_scl;

#define MAX_LASER_CAL_POINTS 16

typedef struct {
    double raw;
    double mm;
} LaserCalPoint;

typedef struct {
    char path[256];
    int source_laser_adc;
    double zero_raw;
    double mm_per_count;
    double factor;
    double min_mm;
    double max_mm;
    double adc_max_raw;
    double laser_min_mm;
    double laser_max_mm;
    double laser_zero_mm;
    double laser_zero_raw;
    double laser_mm_per_count;
    int laser_sign;
    int laser_auto_zero;
    int gauge_output_deviation;
    int laser_output_gauge_scale;
    double laser_gauge_center_mm;
    int laser_output_distance_window;
    double laser_display_min_mm;
    double laser_display_max_mm;
    double laser_display_offset_mm;
    int laser_use_default_120_280;
    int laser_cal_single_point;
    int laser_cal_multi_point;
    int laser_points_count;
    LaserCalPoint laser_points[MAX_LASER_CAL_POINTS];
    int laser_cal_two_point;
    int laser_cal_have_mid;
    double laser_raw_at_min;
    double laser_raw_at_mid;
    double laser_raw_at_max;
    double laser_display_mid_mm;
    double laser_raw_at_plus80;
    double laser_raw_at_zero;
    double laser_raw_at_minus80;
    double laser_raw_default_plus80;
    double laser_raw_default_zero;
    double laser_raw_default_minus80;
    double laser_cal_factor;
    char laser_cal_path[PATH_MAX];
    time_t laser_cal_mtime;
    off_t laser_cal_size;
    ino_t laser_cal_inode;
    int laser_cal_active;
    double last_laser_mm;
    int healthy;
} GaugeADC;

static GaugeADC g_gauge = {
    .path = ADC_PATH_DEFAULT,
    .source_laser_adc = 0,
    .zero_raw = GAUGE_ZERO_DEFAULT,
    .mm_per_count = GAUGE_MPC_DEFAULT,
    .factor = GAUGE_FACTOR_DEFAULT,
    .min_mm = GAUGE_MIN_MM_DEFAULT,
    .max_mm = GAUGE_MAX_MM_DEFAULT,
    .adc_max_raw = GAUGE_ADC_MAX_RAW_DEFAULT,
    .laser_min_mm = LASER_MIN_MM_DEFAULT,
    .laser_max_mm = LASER_MAX_MM_DEFAULT,
    .laser_zero_mm = LASER_ZERO_MM_DEFAULT,
    .laser_zero_raw = -1.0,
    .laser_mm_per_count = (LASER_MAX_MM_DEFAULT - LASER_MIN_MM_DEFAULT) / GAUGE_ADC_MAX_RAW_DEFAULT,
    .laser_sign = -1,
    .laser_auto_zero = LASER_AUTO_ZERO_DEFAULT,
    .gauge_output_deviation = 0,
    .laser_output_gauge_scale = 0,
    .laser_gauge_center_mm = LASER_GAUGE_CENTER_MM_DEFAULT,
    .laser_output_distance_window = 0,
    .laser_display_min_mm = LASER_DISPLAY_MIN_MM_DEFAULT,
    .laser_display_mid_mm = 162.4,
    .laser_display_max_mm = LASER_DISPLAY_MAX_MM_DEFAULT,
    .laser_display_offset_mm = LASER_DISPLAY_OFFSET_MM_DEFAULT,
    .laser_cal_multi_point = 0,
    .laser_points_count = 0,
    .laser_raw_at_mid = 0.0,
    .laser_cal_have_mid = 0,
    .laser_raw_at_plus80 = LASER_RAW_AT_PLUS80_DEFAULT,
    .laser_raw_at_zero = LASER_RAW_AT_ZERO_DEFAULT,
    .laser_raw_at_minus80 = LASER_RAW_AT_MINUS80_DEFAULT,
    .laser_cal_factor = 1.0,
    .laser_cal_mtime = (time_t)-1,
    .laser_cal_size = (off_t)-1,
    .laser_cal_inode = 0,
    .laser_cal_active = 0,
    .last_laser_mm = LASER_ZERO_MM_DEFAULT,
    .healthy = 0,
};

#define PRU_SHARED_MEM_BASE  0x4A310000u
#define PRU_SHARED_MEM_SIZE  0x3000u
#define PRU_COUNT_COOKIE     0xA5A5A5A5u

typedef struct {
    int is_pru;
    int pru_mem_fd;
    void *pru_map;
    volatile int32_t *pru_count;
    volatile uint32_t *pru_status;
    volatile uint32_t *pru_sample_us;
    volatile uint32_t *pru_count_check;

    int a_fd;
    int b_event_fd;
    int mem_fd;
    volatile uint32_t *gpio1_base;
    pthread_t thread;
    volatile int worker_running;
    volatile int32_t raw_count;
    volatile uint32_t sample_us;
    int forward_only;

    double mm_per_count;
    double factor;
    int invert;
    int max_delta;
    int have_last_count;
    int32_t last_count;
    int have_zero_count;
    int32_t zero_count;
} EncoderPRU;

static EncoderPRU g_enc = {
    .is_pru = 0,
    .pru_mem_fd = -1,
    .pru_map = NULL,
    .pru_count = NULL,
    .pru_status = NULL,
    .pru_sample_us = NULL,
    .pru_count_check = NULL,
    .a_fd = -1,
    .b_event_fd = -1,
    .mem_fd = -1,
    .gpio1_base = NULL,
    .worker_running = 0,
    .raw_count = 0,
    .sample_us = 0,
    .forward_only = 1,
    .mm_per_count = 0.0,
    .factor = 1.0,
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

static double env_double_any(const char *name, double fallback) {
    const char *raw = getenv(name);
    char *end = NULL;
    double value;
    if (!raw || !*raw) return fallback;
    value = strtod(raw, &end);
    if (end == raw || (end && *end != '\0') || !isfinite(value)) {
        fprintf(stderr, "[CFG] Warning: invalid %s='%s', using %.3f\n", name, raw, fallback);
        return fallback;
    }
    return value;
}

static int gauge_read_raw(const GaugeADC *gauge, int *raw_out);

static int json_number_field(const char *json, const char *name, double *value_out) {
    char key[64];
    const char *field;
    const char *colon;
    char *end = NULL;
    char *tail;
    double value;

    if (!json || !name || !value_out) return 0;
    if (snprintf(key, sizeof(key), "\"%s\"", name) >= (int)sizeof(key)) return 0;
    field = strstr(json, key);
    if (!field) return 0;
    colon = strchr(field + strlen(key), ':');
    if (!colon) return -1;
    value = strtod(colon + 1, &end);
    if (end == colon + 1 || !isfinite(value)) return -1;
    tail = end;
    while (*tail == ' ' || *tail == '\t' || *tail == '\r' || *tail == '\n') tail++;
    if (*tail != ',' && *tail != '}' && *tail != ']' && *tail != '\0') return -1;
    *value_out = value;
    return 1;
}

static void gauge_reload_laser_calibration(GaugeADC *gauge) {
    const char *configured_path;
    struct stat st;
    FILE *fp;
    char data[4096];
    char *factor_key;
    char *colon;
    char *end = NULL;
    char *tail;
    double factor;
    double raw_plus80;
    double raw_minus80;
    int plus_status;
    int minus_status;
    size_t bytes_read;

    if (!gauge) return;
    configured_path = getenv("RAIL_LASER_CAL_FILE");
    if (configured_path && *configured_path &&
        strncmp(gauge->laser_cal_path, configured_path, sizeof(gauge->laser_cal_path)) != 0) {
        snprintf(gauge->laser_cal_path, sizeof(gauge->laser_cal_path), "%s", configured_path);
        gauge->laser_cal_mtime = (time_t)-1;
        gauge->laser_cal_size = (off_t)-1;
        gauge->laser_cal_inode = 0;
    }
    if (stat(gauge->laser_cal_path, &st) != 0) {
        if (errno == ENOENT && gauge->laser_cal_active) {
            gauge->laser_cal_factor = 1.0;
            gauge->laser_cal_active = 0;
            gauge->laser_raw_at_plus80 = gauge->laser_raw_default_plus80;
            gauge->laser_raw_at_zero = gauge->laser_raw_default_zero;
            gauge->laser_raw_at_minus80 = gauge->laser_raw_default_minus80;
            gauge->laser_cal_mtime = (time_t)-1;
            gauge->laser_cal_size = (off_t)-1;
            gauge->laser_cal_inode = 0;
            printf("[GAUGE] laser calibration file removed; restored launcher references\n");
        }
        return;
    }
    if (gauge->laser_cal_mtime == st.st_mtime &&
        gauge->laser_cal_size == st.st_size &&
        gauge->laser_cal_inode == st.st_ino) {
        return;
    }
    gauge->laser_cal_mtime = st.st_mtime;
    gauge->laser_cal_size = st.st_size;
    gauge->laser_cal_inode = st.st_ino;

    fp = fopen(gauge->laser_cal_path, "r");
    if (!fp) {
        fprintf(stderr, "[GAUGE] cannot open laser calibration file %s: %s\n",
                gauge->laser_cal_path, strerror(errno));
        return;
    }
    bytes_read = fread(data, 1, sizeof(data) - 1, fp);
    if (ferror(fp)) {
        fprintf(stderr, "[GAUGE] cannot read laser calibration file %s\n",
                gauge->laser_cal_path);
        fclose(fp);
        return;
    }
    fclose(fp);
    data[bytes_read] = '\0';

    const char *pts = strstr(data, "\"points\"");
    if (pts) {
        char *p = (char *)strchr(pts, '[');
        int count = 0;
        while (p && count < MAX_LASER_CAL_POINTS) {
            char *obj = strchr(p, '{');
            if (!obj) break;
            char *obj_end = strchr(obj, '}');
            if (!obj_end) break;
            char saved = *obj_end;
            *obj_end = '\0';
            double r = 0.0, m = 0.0;
            if (json_number_field(obj, "raw", &r) == 1 &&
                json_number_field(obj, "mm", &m) == 1) {
                gauge->laser_points[count].raw = r;
                gauge->laser_points[count].mm = m;
                count++;
            }
            *obj_end = saved;
            p = obj_end + 1;
        }
        if (count >= 2) {
            int i, j;
            for (i = 0; i < count - 1; i++) {
                for (j = i + 1; j < count; j++) {
                    if (gauge->laser_points[i].mm > gauge->laser_points[j].mm) {
                        LaserCalPoint tmp = gauge->laser_points[i];
                        gauge->laser_points[i] = gauge->laser_points[j];
                        gauge->laser_points[j] = tmp;
                    }
                }
            }
            gauge->laser_points_count = count;
            gauge->laser_cal_active = 1;
            gauge->laser_cal_multi_point = 1;
            gauge->laser_cal_factor = 1.0;
            gauge->laser_display_min_mm = gauge->laser_points[0].mm;
            gauge->laser_display_max_mm = gauge->laser_points[count - 1].mm;
            printf("[GAUGE] loaded %d-point laser calibration from %s (MIN=%.1f mm, MAX=%.1f mm)\n",
                   count, gauge->laser_cal_path, gauge->laser_display_min_mm, gauge->laser_display_max_mm);
            for (i = 0; i < count; i++) {
                printf("[GAUGE]   point[%d]: raw=%.1f -> mm=%.2f\n",
                       i, gauge->laser_points[i].raw, gauge->laser_points[i].mm);
            }
            return;
        }
    }

    gauge->laser_cal_multi_point = 0;
    gauge->laser_points_count = 0;
    gauge->laser_cal_two_point = 0;
    gauge->laser_cal_have_mid = 0;
    gauge->laser_cal_single_point = 0;
    gauge->laser_use_default_120_280 = 0;

    /* Check for explicit default 120 to 280 mm mode */
    if (strstr(data, "\"default_120_to_280\"") || strstr(data, "\"default\"")) {
        double mm_per_count_val = 0.03907204;
        double offset_val = LASER_DISPLAY_OFFSET_MM_DEFAULT;
        json_number_field(data, "mm_per_count", &mm_per_count_val);
        if (json_number_field(data, "track_gauge_offset_mm", &offset_val) != 1 &&
            json_number_field(data, "display_offset_mm", &offset_val) != 1) {
            json_number_field(data, "offset_mm", &offset_val);
        }
        gauge->laser_cal_factor = 1.0;
        gauge->laser_cal_active = 1;
        gauge->laser_use_default_120_280 = 1;
        gauge->laser_display_min_mm = 120.0;
        gauge->laser_display_max_mm = 280.0;
        gauge->laser_display_offset_mm = offset_val;
        gauge->laser_mm_per_count = (mm_per_count_val > 0.0) ? mm_per_count_val : (160.0 / 4095.0);
        printf("[GAUGE] loaded default laser range: 120.0 mm to 280.0 mm (mm_per_count=%.8f, offset=%.1f mm) from %s\n",
               gauge->laser_mm_per_count, gauge->laser_display_offset_mm, gauge->laser_cal_path);
        return;
    }

    double raw_max_val = 0.0;
    double max_mm_val = 237.4;
    double mm_per_count_val = 0.03907204;
    double offset_val = LASER_DISPLAY_OFFSET_MM_DEFAULT;
    int max_status = json_number_field(data, "raw_at_max", &raw_max_val);
    json_number_field(data, "max_mm", &max_mm_val);
    json_number_field(data, "mm_per_count", &mm_per_count_val);
    if (json_number_field(data, "track_gauge_offset_mm", &offset_val) != 1 &&
        json_number_field(data, "display_offset_mm", &offset_val) != 1) {
        json_number_field(data, "offset_mm", &offset_val);
    }

    if (max_status == 1 && raw_max_val > 1.0) {
        gauge->laser_cal_factor = 1.0;
        gauge->laser_cal_active = 1;
        gauge->laser_cal_single_point = 1;
        gauge->laser_raw_at_max = raw_max_val;
        gauge->laser_display_max_mm = max_mm_val;
        gauge->laser_display_offset_mm = offset_val;
        gauge->laser_mm_per_count = (mm_per_count_val > 0.0) ? mm_per_count_val : 0.03907204;
        printf("[GAUGE] loaded single-point laser calibration: MAX=%.1f mm @ %.1f raw, mm_per_count=%.8f, offset=%.1f mm from %s\n",
               max_mm_val, raw_max_val, gauge->laser_mm_per_count, gauge->laser_display_offset_mm, gauge->laser_cal_path);
        return;
    }
    /* If the file only has legacy references (+40 / -40 or +80 / -80), migrate to 2-point */
    plus_status = json_number_field(data, "raw_at_plus40", &raw_plus80);
    if (plus_status != 1) {
        plus_status = json_number_field(data, "raw_at_plus80", &raw_plus80);
    }
    minus_status = json_number_field(data, "raw_at_minus40", &raw_minus80);
    if (minus_status != 1) {
        minus_status = json_number_field(data, "raw_at_minus80", &raw_minus80);
    }
    if (plus_status == 1 && minus_status == 1 && fabs(raw_plus80 - raw_minus80) >= 1.0) {
        gauge->laser_cal_factor = 1.0;
        gauge->laser_cal_active = 1;
        gauge->laser_cal_two_point = 1;
        gauge->laser_raw_at_min = raw_plus80;
        gauge->laser_raw_at_max = raw_minus80;
        gauge->laser_display_min_mm = env_double_any("RAIL_LASER_DISPLAY_MIN_MM", 0.0);
        gauge->laser_display_max_mm = env_double_any("RAIL_LASER_DISPLAY_MAX_MM", 237.4);
        printf("[GAUGE] migrated legacy laser references from %s to 2-point; "
               "MIN(%.1f mm)=%.2f, MAX(%.1f mm)=%.2f\n",
               gauge->laser_cal_path, gauge->laser_display_min_mm, raw_plus80,
               gauge->laser_display_max_mm, raw_minus80);
        return;
    }
    gauge->laser_cal_two_point = 1;
    gauge->laser_display_min_mm = env_double_any("RAIL_LASER_DISPLAY_MIN_MM", 0.0);
    gauge->laser_display_max_mm = env_double_any("RAIL_LASER_DISPLAY_MAX_MM", 237.4);
    factor_key = strstr(data, "\"cal_factor\"");
    if (!factor_key) factor_key = strstr(data, "\"factor\"");
    colon = factor_key ? strchr(factor_key, ':') : NULL;
    factor = colon ? strtod(colon + 1, &end) : 0.0;
    tail = end;
    while (tail && *tail && (*tail == ' ' || *tail == '\t' || *tail == '\r' || *tail == '\n'))
        tail++;
    if (!colon || end == colon + 1 || !tail || (*tail != ',' && *tail != '}') ||
        !isfinite(factor) ||
        factor < LASER_CAL_FACTOR_MIN || factor > LASER_CAL_FACTOR_MAX) {
        fprintf(stderr, "[GAUGE] invalid laser calibration factor in %s\n",
                gauge->laser_cal_path);
        return;
    }

    gauge->laser_cal_factor = factor;
    gauge->laser_cal_active = 1;
    gauge->laser_raw_at_plus80 = LASER_CAL_RAW_AT_PLUS80;
    gauge->laser_raw_at_zero = LASER_CAL_RAW_AT_ZERO;
    gauge->laser_raw_at_minus80 = 0.0;
    printf("[GAUGE] loaded laser calibration factor=%.6f from %s; "
           "references=%.2f (+80), %.2f (0), 0 (-80)\n",
           factor, gauge->laser_cal_path, gauge->laser_raw_at_plus80,
           gauge->laser_raw_at_zero);
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
    const char *source = getenv("RAIL_GAUGE_SOURCE");
    const char *out_mode = getenv("RAIL_GAUGE_OUTPUT_MODE");
    const char *cal_path = getenv("RAIL_LASER_CAL_FILE");
    if (raw_path && *raw_path) {
        snprintf(g_gauge.path, sizeof(g_gauge.path), "%s", raw_path);
    }
    /* The Track Gauge hardware is the Panasonic laser; retain an explicit
     * RAIL_GAUGE_SOURCE=adc override only for legacy potentiometer testing. */
    g_gauge.source_laser_adc = (!source || !*source ||
        strcmp(source, "laser") == 0 ||
        strcmp(source, "laser_adc") == 0 ||
        strcmp(source, "hg-c1200") == 0 ||
        strcmp(source, "hgc1200") == 0
    );
    g_gauge.zero_raw = env_double("RAIL_GAUGE_ZERO_RAW", GAUGE_ZERO_DEFAULT);
    g_gauge.mm_per_count = env_double("RAIL_GAUGE_MPC", GAUGE_MPC_DEFAULT);
    g_gauge.factor = env_double("RAIL_GAUGE_FACTOR", GAUGE_FACTOR_DEFAULT);
    g_gauge.min_mm = env_double("RAIL_GAUGE_MIN_MM", GAUGE_MIN_MM_DEFAULT);
    g_gauge.max_mm = env_double("RAIL_GAUGE_MAX_MM", GAUGE_MAX_MM_DEFAULT);
    g_gauge.adc_max_raw = env_double("RAIL_ADC_MAX_RAW", GAUGE_ADC_MAX_RAW_DEFAULT);
    g_gauge.laser_min_mm = env_double("RAIL_LASER_MIN_MM", LASER_MIN_MM_DEFAULT);
    g_gauge.laser_max_mm = env_double_any("RAIL_LASER_MAX_MM", LASER_MAX_MM_DEFAULT);
    g_gauge.laser_zero_mm = env_double_any("RAIL_LASER_ZERO_MM", LASER_ZERO_MM_DEFAULT);
    g_gauge.laser_zero_raw = env_double_any("RAIL_LASER_ZERO_RAW", -1.0);
    g_gauge.laser_mm_per_count = env_double_any("RAIL_LASER_MPC",
                                            (LASER_MAX_MM_DEFAULT - LASER_MIN_MM_DEFAULT) / GAUGE_ADC_MAX_RAW_DEFAULT);
    g_gauge.laser_sign = env_int("RAIL_LASER_SIGN", -1) < 0 ? -1 : 1;
    g_gauge.laser_auto_zero = env_int("RAIL_LASER_AUTO_ZERO", LASER_AUTO_ZERO_DEFAULT) != 0;
    g_gauge.gauge_output_deviation = (out_mode && (strcmp(out_mode, "deviation") == 0 || strcmp(out_mode, "offset") == 0));
    g_gauge.laser_output_gauge_scale = (out_mode && strcmp(out_mode, "laser_gauge") == 0);
    g_gauge.laser_gauge_center_mm = env_double_any("RAIL_LASER_GAUGE_CENTER_MM",
                                                    LASER_GAUGE_CENTER_MM_DEFAULT);
    /* Default to the Track Gauge window when no launcher environment exists. */
    g_gauge.laser_output_distance_window = (!out_mode || !*out_mode ||
                                            strcmp(out_mode, "laser_window") == 0);
    g_gauge.laser_cal_two_point = env_int("RAIL_LASER_TWO_POINT", 1);
    g_gauge.laser_display_min_mm = env_double_any("RAIL_LASER_DISPLAY_MIN_MM",
                                                   LASER_DISPLAY_MIN_MM_DEFAULT);
    g_gauge.laser_display_max_mm = env_double_any("RAIL_LASER_DISPLAY_MAX_MM",
                                                   LASER_DISPLAY_MAX_MM_DEFAULT);
    g_gauge.laser_display_offset_mm = env_double_any("RAIL_LASER_DISPLAY_OFFSET_MM",
                                                      LASER_DISPLAY_OFFSET_MM_DEFAULT);
    g_gauge.laser_display_mid_mm = env_double_any("RAIL_LASER_DISPLAY_MID_MM", 162.4);
    g_gauge.laser_raw_at_min = env_double_any("RAIL_LASER_RAW_AT_MIN", 3936.0);
    g_gauge.laser_raw_at_mid = env_double_any("RAIL_LASER_RAW_AT_MID", 0.0);
    g_gauge.laser_cal_have_mid = (g_gauge.laser_raw_at_mid > 1.0) ? 1 : 0;
    g_gauge.laser_raw_at_max = env_double_any("RAIL_LASER_RAW_AT_MAX", 15.0);
    g_gauge.laser_raw_at_plus80 = env_double_any("RAIL_LASER_RAW_AT_PLUS40",
                                                  env_double_any("RAIL_LASER_RAW_AT_PLUS80",
                                                                 LASER_RAW_AT_PLUS80_DEFAULT));
    g_gauge.laser_raw_at_zero = env_double_any("RAIL_LASER_RAW_AT_ZERO",
                                                LASER_RAW_AT_ZERO_DEFAULT);
    g_gauge.laser_raw_at_minus80 = env_double_any("RAIL_LASER_RAW_AT_MINUS40",
                                                   env_double_any("RAIL_LASER_RAW_AT_MINUS80",
                                                                  LASER_RAW_AT_MINUS80_DEFAULT));
    g_gauge.laser_raw_default_plus80 = g_gauge.laser_raw_at_plus80;
    g_gauge.laser_raw_default_zero = g_gauge.laser_raw_at_zero;
    g_gauge.laser_raw_default_minus80 = g_gauge.laser_raw_at_minus80;
    const char *default_cal = "/home/debian/trolley/laser_cal.json";
    if (access("laser_cal.json", R_OK) == 0) default_cal = "laser_cal.json";
    snprintf(g_gauge.laser_cal_path, sizeof(g_gauge.laser_cal_path), "%s",
             (cal_path && *cal_path) ? cal_path : default_cal);
    gauge_reload_laser_calibration(&g_gauge);
    if (g_gauge.laser_display_min_mm > g_gauge.laser_display_max_mm) {
        double tmp = g_gauge.laser_display_min_mm;
        g_gauge.laser_display_min_mm = g_gauge.laser_display_max_mm;
        g_gauge.laser_display_max_mm = tmp;
    }
    if (g_gauge.min_mm > g_gauge.max_mm) {
        double tmp = g_gauge.min_mm;
        g_gauge.min_mm = g_gauge.max_mm;
        g_gauge.max_mm = tmp;
    }
    printf("[GAUGE] source=%s path=%s nominal=%.1f range=[%.1f, %.1f]\n",
           g_gauge.source_laser_adc ? "laser_adc" : "adc",
           g_gauge.path, (double)GAUGE_MM, g_gauge.min_mm, g_gauge.max_mm);
    printf("[GAUGE] output_mode=%s\n", g_gauge.laser_output_distance_window ?
           "laser_window_120_plus_laser_0_to_160_mm" : (g_gauge.laser_output_gauge_scale ?
           "laser_gauge_120_to_280_mm" :
           (g_gauge.gauge_output_deviation ? "deviation_from_nominal" : "absolute_gauge_mm")));
    if (g_gauge.source_laser_adc) {
        if (g_gauge.laser_auto_zero && g_gauge.laser_zero_raw < 0.0) {
            int raw = 0;
            if (gauge_read_raw(&g_gauge, &raw) == 0) {
                g_gauge.laser_zero_raw = (double)raw;
                printf("[GAUGE] laser auto-zero raw=%d -> nominal %.1f mm\n", raw, (double)GAUGE_MM);
            } else {
                printf("[GAUGE] laser auto-zero pending; ADC read failed at startup\n");
            }
        }
        printf("[GAUGE] laser range=[%.1f, %.1f] zero=%.2f sign=%d factor=%.3f adc_max=%.0f\n",
               g_gauge.laser_min_mm, g_gauge.laser_max_mm, g_gauge.laser_zero_mm,
               g_gauge.laser_sign, g_gauge.factor, g_gauge.adc_max_raw);
        if (g_gauge.laser_output_distance_window) {
            printf("[GAUGE] laser calibration: raw=%.1f (+80) -> %.0f mm, raw=%.1f (0) -> 200 mm, raw=%.1f (-80) -> %.0f mm\n",
                   g_gauge.laser_raw_at_plus80, g_gauge.laser_display_min_mm,
                   g_gauge.laser_raw_at_zero,
                   g_gauge.laser_raw_at_minus80, g_gauge.laser_display_max_mm);
        }
        if (g_gauge.laser_zero_raw >= 0.0) {
            printf("[GAUGE] laser offset mode zero_raw=%.0f mpc=%.6f mm/count\n",
                   g_gauge.laser_zero_raw, g_gauge.laser_mm_per_count);
        }
    } else {
        printf("[GAUGE] adc zero=%.2f mpc=%.6f factor=%.3f\n",
               g_gauge.zero_raw, g_gauge.mm_per_count, g_gauge.factor);
    }
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
    double output_mm;
    double ratio;
    double laser_mm;
    double sample_raw;
    if (!gauge || !gauge_mm_out) return -1;
    if (gauge_read_raw(gauge, &raw) != 0) {
        gauge->healthy = 0;
        return -1;
    }
    gauge->healthy = 1;
    sample_raw = (double)raw;
    if (gauge->source_laser_adc) {
        gauge_reload_laser_calibration(gauge);
        sample_raw *= gauge->laser_cal_factor;
        if (gauge->laser_auto_zero && (gauge->laser_zero_raw < 15.0)) {
            if (raw >= 15) {
                gauge->laser_zero_raw = (double)raw;
                printf("[GAUGE] laser auto-zero locked raw=%d\n", raw);
            }
        }
        /* A fixed 80 mm reference is valid when auto-zero is disabled. */
        if ((gauge->laser_auto_zero && gauge->laser_zero_raw < 0.0) ||
            (!gauge->laser_output_distance_window && (raw < 15 || raw > 3060))) {
            output_mm = 0.0;
        } else {
            ratio = sample_raw / gauge->adc_max_raw;
            if (ratio < 0.0) ratio = 0.0;
            if (ratio > 1.0) ratio = 1.0;
            laser_mm = gauge->laser_min_mm + ratio * (gauge->laser_max_mm - gauge->laser_min_mm);
            if (laser_mm < 0.0) laser_mm = 0.0;
            if (laser_mm > 160.0) laser_mm = 160.0;
            gauge->last_laser_mm = laser_mm;
            
            if (gauge->laser_use_default_120_280) {
                /* Default Panasonic HG-C1200 laser range: 120.0 mm (near) to 280.0 mm (far).
                 * High raw (4095 counts / 1.8V) -> 120.0 mm.
                 * Low raw (0 counts / 0.0V) -> 280.0 mm.
                 * Track Gauge = Laser reading + laser_display_offset_mm (adds 1523.5 mm).
                 * At 249 mm reading: Track Gauge = 249.0 + 1523.5 = 1772.5 mm. */
                double laser_dist = 280.0 - sample_raw * gauge->laser_mm_per_count;
                if (laser_dist < 120.0) laser_dist = 120.0;
                if (laser_dist > 280.0) laser_dist = 280.0;
                output_mm = laser_dist + gauge->laser_display_offset_mm;
            } else if (gauge->laser_cal_single_point) {
                /* Pure single-point calibration at MAX reference (237.4 mm).
                 * Distance increases or decreases proportionally from MAX. */
                double laser_dist = gauge->laser_display_max_mm -
                            (sample_raw - gauge->laser_raw_at_max) * gauge->laser_mm_per_count;
                if (laser_dist < 0.0) laser_dist = 0.0;
                output_mm = laser_dist + gauge->laser_display_offset_mm;
            } else if (gauge->laser_cal_multi_point && gauge->laser_points_count >= 2) {
                int n = gauge->laser_points_count;
                double r_max = gauge->laser_points[0].raw;
                double r_min = gauge->laser_points[n - 1].raw;
                if (r_max > r_min) {
                    /* Raw count decreases as physical distance increases (standard) */
                    if (sample_raw >= r_max - 0.5) {
                        output_mm = gauge->laser_points[0].mm;
                    } else if (sample_raw <= r_min + 0.5) {
                        output_mm = gauge->laser_points[n - 1].mm;
                    } else {
                        int i;
                        output_mm = gauge->laser_points[0].mm;
                        for (i = 0; i < n - 1; i++) {
                            double r0 = gauge->laser_points[i].raw;
                            double r1 = gauge->laser_points[i + 1].raw;
                            if (sample_raw <= r0 && sample_raw >= r1) {
                                double d_r = r1 - r0;
                                if (fabs(d_r) > 1e-6) {
                                    double frac = (sample_raw - r0) / d_r;
                                    output_mm = gauge->laser_points[i].mm +
                                        frac * (gauge->laser_points[i + 1].mm - gauge->laser_points[i].mm);
                                } else {
                                    output_mm = gauge->laser_points[i].mm;
                                }
                                break;
                            }
                        }
                    }
                } else {
                    /* Raw count increases as physical distance increases */
                    if (sample_raw <= r_max + 5.0) {
                        output_mm = gauge->laser_points[0].mm;
                    } else if (sample_raw >= r_min - 5.0) {
                        output_mm = gauge->laser_points[n - 1].mm;
                    } else {
                        int i;
                        output_mm = gauge->laser_points[0].mm;
                        for (i = 0; i < n - 1; i++) {
                            double r0 = gauge->laser_points[i].raw;
                            double r1 = gauge->laser_points[i + 1].raw;
                            if (sample_raw >= r0 && sample_raw <= r1) {
                                double d_r = r1 - r0;
                                if (fabs(d_r) > 1e-6) {
                                    double frac = (sample_raw - r0) / d_r;
                                    output_mm = gauge->laser_points[i].mm +
                                        frac * (gauge->laser_points[i + 1].mm - gauge->laser_points[i].mm);
                                } else {
                                    output_mm = gauge->laser_points[i].mm;
                                }
                                break;
                            }
                        }
                    }
                }
            } else if (gauge->laser_cal_two_point) {
                double raw_min = gauge->laser_raw_at_min;
                double raw_max = gauge->laser_raw_at_max;
                double d_raw = raw_max - raw_min;
                if (fabs(d_raw) < 1.0) {
                    output_mm = gauge->laser_display_min_mm;
                } else {
                    double r_lo = raw_min < raw_max ? raw_min : raw_max;
                    double r_hi = raw_min < raw_max ? raw_max : raw_min;
                    double clamped_raw = sample_raw;
                    if (clamped_raw < r_lo) clamped_raw = r_lo;
                    if (clamped_raw > r_hi) clamped_raw = r_hi;
                    if (gauge->laser_cal_have_mid &&
                        ((raw_min > raw_max && gauge->laser_raw_at_mid < raw_min && gauge->laser_raw_at_mid > raw_max) ||
                         (raw_min < raw_max && gauge->laser_raw_at_mid > raw_min && gauge->laser_raw_at_mid < raw_max))) {
                        double raw_mid = gauge->laser_raw_at_mid;
                        double mid_mm = gauge->laser_display_mid_mm;
                        if ((raw_min >= raw_max && clamped_raw >= raw_mid) ||
                            (raw_min < raw_max && clamped_raw <= raw_mid)) {
                            double d1 = raw_mid - raw_min;
                            output_mm = gauge->laser_display_min_mm +
                                (clamped_raw - raw_min) * (mid_mm - gauge->laser_display_min_mm) / d1;
                        } else {
                            double d2 = raw_max - raw_mid;
                            output_mm = mid_mm +
                                (clamped_raw - raw_mid) * (gauge->laser_display_max_mm - mid_mm) / d2;
                        }
                    } else {
                        output_mm = gauge->laser_display_min_mm +
                            (clamped_raw - raw_min) * (gauge->laser_display_max_mm - gauge->laser_display_min_mm) / d_raw;
                    }
                }
            } else if (gauge->laser_output_distance_window) {
                double span_plus_to_zero = gauge->laser_raw_at_plus80 - gauge->laser_raw_at_zero;
                double span_zero_to_minus = gauge->laser_raw_at_zero - gauge->laser_raw_at_minus80;
                double display_center_mm =
                    (gauge->laser_display_min_mm + gauge->laser_display_max_mm) / 2.0;
                double mapped_raw = sample_raw;
                /* Three-point calibration: +40 -> 160 mm, 0 -> 200 mm, -40 -> 240 mm (or +80/-80 legacy). */
                if (span_plus_to_zero <= 0.0 || span_zero_to_minus <= 0.0 ||
                    sample_raw < 5.0 || sample_raw > gauge->adc_max_raw - 5.0) {
                    output_mm = 0.0;
                } else {
                    if (mapped_raw < gauge->laser_raw_at_minus80)
                        mapped_raw = gauge->laser_raw_at_minus80;
                    if (mapped_raw > gauge->laser_raw_at_plus80)
                        mapped_raw = gauge->laser_raw_at_plus80;
                    if (mapped_raw >= gauge->laser_raw_at_zero) {
                        output_mm = display_center_mm +
                            (gauge->laser_raw_at_zero - mapped_raw) *
                            (display_center_mm - gauge->laser_display_min_mm) / span_plus_to_zero;
                    } else {
                        output_mm = display_center_mm +
                            (gauge->laser_raw_at_zero - mapped_raw) *
                            (gauge->laser_display_max_mm - display_center_mm) / span_zero_to_minus;
                    }
                }
            } else if (gauge->gauge_output_deviation || gauge->laser_output_gauge_scale) {
                double reference_laser;
                double laser_deviation;
                if (gauge->laser_zero_raw >= 0.0) {
                    reference_laser = gauge->laser_min_mm + (gauge->laser_zero_raw / gauge->adc_max_raw) * (gauge->laser_max_mm - gauge->laser_min_mm);
                } else {
                    reference_laser = gauge->laser_zero_mm;
                }
                laser_deviation = (reference_laser - laser_mm) * gauge->factor;
                /* +80 laser deviation -> 120 mm; 0 -> 200 mm; -80 -> 280 mm. */
                output_mm = gauge->laser_output_gauge_scale ?
                            (gauge->laser_gauge_center_mm - laser_deviation) :
                            laser_deviation;
            } else {
                output_mm = laser_mm * gauge->factor;
                if (output_mm < 0.0) output_mm = 0.0;
                if (output_mm > 160.0) output_mm = 160.0;
            }
        }
    } else {
        gauge_mm = GAUGE_MM + ((double)raw - gauge->zero_raw) * gauge->mm_per_count * gauge->factor;
        if (gauge_mm < gauge->min_mm) gauge_mm = gauge->min_mm;
        if (gauge_mm > gauge->max_mm) gauge_mm = gauge->max_mm;
        output_mm = gauge->gauge_output_deviation ? (gauge_mm - GAUGE_MM) : gauge_mm;
    }
    *gauge_mm_out = (float)output_mm;
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

    const char *bus_env = getenv("RAIL_SPI_BUS");
    const char *dev_env = getenv("RAIL_SPI_DEV");
    bool use_spi0 = (bus_env && bus_env[0] == '0') || (dev_env && strstr(dev_env, "spidev0."));

    if (use_spi0) {
        printf("[PINMUX] Ensuring SPI0 pins are configured for SCL3300 (P9_17, P9_21, P9_18, P9_22)...\n");
        if (run_quiet("config-pin P9_17 spi_cs >/dev/null 2>&1") != 0)
            fprintf(stderr, "[PINMUX] Warning: failed to set P9_17 -> spi_cs\n");
        if (run_quiet("config-pin P9_21 spi >/dev/null 2>&1") != 0)
            fprintf(stderr, "[PINMUX] Warning: failed to set P9_21 -> spi (MISO/D0)\n");
        if (run_quiet("config-pin P9_18 spi >/dev/null 2>&1") != 0)
            fprintf(stderr, "[PINMUX] Warning: failed to set P9_18 -> spi (MOSI/D1)\n");
        if (run_quiet("config-pin P9_22 spi_sclk >/dev/null 2>&1") != 0)
            fprintf(stderr, "[PINMUX] Warning: failed to set P9_22 -> spi_sclk\n");
    } else {
        printf("[PINMUX] Ensuring SPI1 pins are configured for SCL3300 (P9_28, P9_29, P9_30, P9_31)...\n");
        if (run_quiet("config-pin P9_28 spi_cs >/dev/null 2>&1") != 0)
            fprintf(stderr, "[PINMUX] Warning: failed to set P9_28 -> spi_cs\n");
        if (run_quiet("config-pin P9_29 spi >/dev/null 2>&1") != 0)
            fprintf(stderr, "[PINMUX] Warning: failed to set P9_29 -> spi (MISO/D0)\n");
        if (run_quiet("config-pin P9_30 spi >/dev/null 2>&1") != 0)
            fprintf(stderr, "[PINMUX] Warning: failed to set P9_30 -> spi (MOSI/D1)\n");
        if (run_quiet("config-pin P9_31 spi_sclk >/dev/null 2>&1") != 0)
            fprintf(stderr, "[PINMUX] Warning: failed to set P9_31 -> spi_sclk\n");
    }
    usleep(50000);
}

static void ensure_encoder_pinmux(void) {
    /* Both PRU1 (GPIO1_DATAIN reader) and Linux GPIO fallback read P8_15/P8_12 in GPIO mode. */
    run_quiet("config-pin P8_15 gpio >/dev/null 2>&1");
    run_quiet("config-pin P8_12 gpio >/dev/null 2>&1");
    usleep(10000);
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

static inline int read_gpio_a_level(EncoderPRU *enc) {
    if (enc->gpio1_base) {
        return (enc->gpio1_base[AM335X_GPIO_DATAIN_OFF] & (1u << ENCODER_GPIO_A_OFFSET)) ? 1 : 0;
    }
    struct gpiohandle_data a_data;
    memset(&a_data, 0, sizeof(a_data));
    if (ioctl(enc->a_fd, GPIOHANDLE_GET_LINE_VALUES_IOCTL, &a_data) == 0) {
        return a_data.values[0] ? 1 : 0;
    }
    return 0;
}

static void *encoder_gpio_worker(void *opaque) {
    EncoderPRU *enc = (EncoderPRU *)opaque;

    /* Stay on normal fair-share scheduling (SCHED_OTHER) so EMI pulse bursts never starve Xorg or the Qt UI. */
    struct sched_param sparam;
    memset(&sparam, 0, sizeof(sparam));
    pthread_setschedparam(pthread_self(), SCHED_OTHER, &sparam);

    const uint64_t min_edge_ns = (uint64_t)env_int("RAIL_ENCODER_MIN_EDGE_US", ENCODER_MIN_EDGE_US_DEFAULT) * 1000ULL;
    uint64_t last_edge_ns = 0;
    uint64_t storm_win_start_ns = 0;
    int storm_events = 0;

    struct pollfd pfd;
    pfd.fd = enc->b_event_fd;
    pfd.events = POLLIN;

    struct gpioevent_data events[ENCODER_EVENT_BATCH];

    while (g_run && enc->worker_running) {
        int rc = poll(&pfd, 1, 200);
        if (rc <= 0) continue;
        if (!(pfd.revents & POLLIN)) continue;

        ssize_t bytes = read(enc->b_event_fd, events, sizeof(events));
        if (bytes < (ssize_t)sizeof(struct gpioevent_data)) continue;

        int num_events = (int)(bytes / sizeof(struct gpioevent_data));
        if (num_events <= 0) continue;

        int accepted = 0;
        int last_accepted_idx = -1;
        for (int i = 0; i < num_events; ++i) {
            uint64_t ts = events[i].timestamp;
            if (last_edge_ns != 0 && ts < last_edge_ns) {
                last_edge_ns = ts;
            }
            if (min_edge_ns > 0 && last_edge_ns != 0 && (ts - last_edge_ns) < min_edge_ns) {
                continue;
            }
            last_edge_ns = ts;
            accepted++;
            last_accepted_idx = i;
        }

        if (accepted > 0) {
            int dir = 1;
            if (!enc->forward_only) {
                /* Determine direction from the most recent accepted edge and instantaneous Channel A level */
                int last_rising = (events[last_accepted_idx].id == GPIOEVENT_EVENT_RISING_EDGE);
                int a_level = read_gpio_a_level(enc);
                dir = (last_rising == a_level) ? 1 : -1;
            }

            __sync_fetch_and_add(&enc->raw_count, accepted * dir);
            enc->sample_us = (uint32_t)(events[last_accepted_idx].timestamp / 1000ULL);
        }

        uint64_t batch_ts = events[num_events - 1].timestamp;
        if (storm_win_start_ns == 0 || batch_ts < storm_win_start_ns ||
            (batch_ts - storm_win_start_ns) > ENCODER_STORM_WINDOW_NS) {
            storm_win_start_ns = batch_ts;
            storm_events = num_events;
        } else {
            storm_events += num_events;
        }

        /* Back off briefly during noise/EMI edge storms so kernel buffers coalesce without hogging the single CPU core. */
        if (num_events >= ENCODER_EVENT_BATCH || storm_events >= ENCODER_STORM_MAX_EVENTS) {
            struct timespec pause_ts = {0, ENCODER_STORM_YIELD_NS};
            nanosleep(&pause_ts, NULL);
            storm_win_start_ns = 0;
            storm_events = 0;
        }
    }
    return NULL;
}

static int pru1_remoteproc_is_running(void) {
    DIR *dir = opendir("/sys/class/remoteproc");
    if (!dir) return 0;

    struct dirent *ent;
    int running = 0;
    while ((ent = readdir(dir)) != NULL) {
        if (strncmp(ent->d_name, "remoteproc", 10) != 0) continue;

        char path[PATH_MAX];
        char buf[64];
        snprintf(path, sizeof(path), "/sys/class/remoteproc/%s/name", ent->d_name);
        FILE *f = fopen(path, "r");
        if (!f) continue;
        size_t n = fread(buf, 1, sizeof(buf) - 1, f);
        fclose(f);
        buf[n] = '\0';
        if (strstr(buf, "4a338000.pru") == NULL) continue;

        snprintf(path, sizeof(path), "/sys/class/remoteproc/%s/state", ent->d_name);
        f = fopen(path, "r");
        if (f) {
            n = fread(buf, 1, sizeof(buf) - 1, f);
            fclose(f);
            buf[n] = '\0';
            if (strncmp(buf, "running", 7) == 0) {
                running = 1;
            }
        }
        break;
    }
    closedir(dir);
    return running;
}

static double load_encoder_factor_from_cfg(void) {
    const char *cfg_path = "/home/debian/trolley/rail_config.json";
    if (access("rail_config.json", R_OK) == 0) cfg_path = "rail_config.json";
    int fd = open(cfg_path, O_RDONLY);
    if (fd < 0) return 1.0;
    char buf[4096];
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0) return 1.0;
    buf[n] = '\0';
    char *enc_sec = strstr(buf, "\"encoder\"");
    if (!enc_sec) return 1.0;
    double factor = 1.0;
    if (json_number_field(enc_sec, "factor", &factor) == 1) {
        if (factor > 0.1 && factor < 10.0) return factor;
    }
    return 1.0;
}

static int encoder_open(EncoderPRU *enc) {
    double ppr;
    double wheel_diameter_mm;
    double counts_per_rev;

    if (!enc) return -1;

    ppr = env_double("RAIL_ENCODER_PPR", ENCODER_DEFAULT_PPR);
    wheel_diameter_mm = env_double("RAIL_WHEEL_DIAMETER_MM", ENCODER_DEFAULT_WHEEL_DIAMETER_MM);
    enc->factor = env_double("RAIL_ENCODER_FACTOR", load_encoder_factor_from_cfg());
    if (enc->factor <= 0.0) enc->factor = 1.0;
    enc->invert = env_int("RAIL_ENCODER_INVERT", 0) ? 1 : 0;
    enc->max_delta = env_int("RAIL_ENCODER_MAX_DELTA", ENCODER_MAX_DELTA_DEFAULT);
    if (enc->max_delta < 0) enc->max_delta = ENCODER_MAX_DELTA_DEFAULT;
    enc->forward_only = env_int("RAIL_ENCODER_FORWARD_ONLY", 1);

    enc->is_pru = 0;
    enc->pru_mem_fd = -1;
    enc->pru_map = NULL;
    enc->pru_count = NULL;
    enc->pru_status = NULL;
    enc->pru_sample_us = NULL;
    enc->pru_count_check = NULL;

    /*
     * [SOLUTION 2] Check for active PRU1 hardware quadrature firmware in PRUSS shared RAM.
     * Require remoteproc2 (4a338000.pru) to actually report state=running so stale SRAM
     * across warm reboots never fools us into thinking PRU1 is active when it is offline.
     */
    int pru_fd = pru1_remoteproc_is_running() ? open("/dev/mem", O_RDWR | O_SYNC) : -1;
    if (pru_fd >= 0) {
        void *map = mmap(NULL, PRU_SHARED_MEM_SIZE, PROT_READ | PROT_WRITE,
                         MAP_SHARED, pru_fd, PRU_SHARED_MEM_BASE);
        if (map != MAP_FAILED) {
            volatile int32_t *p_cnt = (volatile int32_t *)((uint8_t *)map + 0x00u);
            volatile uint32_t *p_st  = (volatile uint32_t *)((uint8_t *)map + 0x04u);
            volatile uint32_t *p_us  = (volatile uint32_t *)((uint8_t *)map + 0x08u);
            volatile uint32_t *p_ck  = (volatile uint32_t *)((uint8_t *)map + 0x0Cu);

            int32_t c = *p_cnt;
            uint32_t ck = *p_ck;
            uint32_t st = *p_st;

            if (st == 1u && ((((uint32_t)c) ^ PRU_COUNT_COOKIE) == ck)) {
                enc->is_pru = 1;
                enc->pru_mem_fd = pru_fd;
                enc->pru_map = map;
                enc->pru_count = p_cnt;
                enc->pru_status = p_st;
                enc->pru_sample_us = p_us;
                enc->pru_count_check = p_ck;

                counts_per_rev = ppr * 4.0; /* 4X hardware decoding in PRU1 */
                enc->mm_per_count = ((M_PI * wheel_diameter_mm) / counts_per_rev) * enc->factor;

                printf("[ENC] PRU1 hardware quadrature active: P8_15=A, P8_12=B [0%% CPU, 4X decode, 0.01%% accuracy]\n");
                printf("[ENC] Geometry: ppr_per_channel=%.0f decode=4X counts_per_rev=%.0f wheel_diameter_mm=%.2f factor=%.6f mm_per_count=%.6f invert=%d\n",
                       ppr, counts_per_rev, wheel_diameter_mm, enc->factor, enc->mm_per_count, enc->invert);
                return 0;
            }
            munmap(map, PRU_SHARED_MEM_SIZE);
        }
        close(pru_fd);
    }

    /*
     * [SOLUTION 1] Fallback: Linux GPIO quadrature mode on P8_15/P8_12 under SCHED_OTHER + EMI filter
     */
    counts_per_rev = ppr * ENCODER_DECODE; /* 2X decoding: 800 counts/rev */
    enc->mm_per_count = ((M_PI * wheel_diameter_mm) / counts_per_rev) * enc->factor;

    {
        struct gpiohandle_request a_req;
        struct gpioevent_request b_req;
        memset(&a_req, 0, sizeof(a_req));
        a_req.lines = 1;
        a_req.lineoffsets[0] = ENCODER_GPIO_A_OFFSET;
        a_req.flags = GPIOHANDLE_REQUEST_INPUT;
        snprintf(a_req.consumer_label, sizeof(a_req.consumer_label), "rail-encoder-a");
        int chip_fd = open(ENCODER_GPIO_CHIP, O_RDONLY | O_CLOEXEC);
        if (chip_fd < 0 || ioctl(chip_fd, GPIO_GET_LINEHANDLE_IOCTL, &a_req) < 0) {
            fprintf(stderr, "[ENC] cannot request P8_15/GPIO1_15 input: %s\n", strerror(errno));
            if (chip_fd >= 0) close(chip_fd);
            return -1;
        }
        enc->a_fd = a_req.fd;
        memset(&b_req, 0, sizeof(b_req));
        b_req.lineoffset = ENCODER_GPIO_B_OFFSET;
        b_req.handleflags = GPIOHANDLE_REQUEST_INPUT;
        b_req.eventflags = GPIOEVENT_REQUEST_RISING_EDGE | GPIOEVENT_REQUEST_FALLING_EDGE;
        snprintf(b_req.consumer_label, sizeof(b_req.consumer_label), "rail-encoder-b");
        if (ioctl(chip_fd, GPIO_GET_LINEEVENT_IOCTL, &b_req) < 0) {
            fprintf(stderr, "[ENC] cannot request P8_12/GPIO1_12 edges: %s\n", strerror(errno));
            close(enc->a_fd); enc->a_fd = -1; close(chip_fd);
            return -1;
        }
        enc->b_event_fd = b_req.fd;
        close(chip_fd);
    }

    /* Open /dev/mem to map GPIO1 registers for zero-latency direct sampling */
    enc->mem_fd = open("/dev/mem", O_RDONLY | O_SYNC);
    if (enc->mem_fd >= 0) {
        void *m = mmap(NULL, AM335X_GPIO1_SIZE, PROT_READ, MAP_SHARED, enc->mem_fd, AM335X_GPIO1_BASE);
        if (m != MAP_FAILED) {
            enc->gpio1_base = (volatile uint32_t *)m;
            printf("[ENC] Direct memory-mapped GPIO1 enabled (base=0x%08X)\n", AM335X_GPIO1_BASE);
        } else {
            enc->gpio1_base = NULL;
        }
    } else {
        enc->gpio1_base = NULL;
    }

    enc->raw_count = 0;
    enc->sample_us = 0;
    enc->worker_running = 1;

    if (pthread_create(&enc->thread, NULL, encoder_gpio_worker, enc) != 0) {
        fprintf(stderr, "[ENC] cannot start GPIO edge worker\n");
        if (enc->gpio1_base) { munmap((void *)enc->gpio1_base, AM335X_GPIO1_SIZE); enc->gpio1_base = NULL; }
        if (enc->mem_fd >= 0) { close(enc->mem_fd); enc->mem_fd = -1; }
        close(enc->b_event_fd); enc->b_event_fd = -1;
        close(enc->a_fd); enc->a_fd = -1;
        enc->worker_running = 0;
        return -1;
    }

    printf("[ENC] Linux GPIO quadrature fallback: P8_15=A (gpio1[15]), P8_12=B (gpio1[12]) [SCHED_OTHER + EMI filter, fwd_only=%d]\n", enc->forward_only);
    printf("[ENC] Geometry: ppr_per_channel=%.0f decode=2X counts_per_rev=%.0f wheel_diameter_mm=%.2f factor=%.6f mm_per_count=%.6f invert=%d max_delta=%d\n",
           ppr, counts_per_rev, wheel_diameter_mm, enc->factor, enc->mm_per_count, enc->invert, enc->max_delta);
    return 0;
}

static void encoder_close(EncoderPRU *enc) {
    if (!enc) return;
    if (enc->is_pru) {
        if (enc->pru_map && enc->pru_map != MAP_FAILED) {
            munmap((void *)enc->pru_map, PRU_SHARED_MEM_SIZE);
            enc->pru_map = NULL;
        }
        if (enc->pru_mem_fd >= 0) {
            close(enc->pru_mem_fd);
            enc->pru_mem_fd = -1;
        }
        enc->is_pru = 0;
    }
    if (enc->worker_running) {
        enc->worker_running = 0;
        pthread_join(enc->thread, NULL);
    }
    if (enc->gpio1_base) {
        munmap((void *)enc->gpio1_base, AM335X_GPIO1_SIZE);
        enc->gpio1_base = NULL;
    }
    if (enc->mem_fd >= 0) {
        close(enc->mem_fd);
        enc->mem_fd = -1;
    }
    if (enc->b_event_fd >= 0) close(enc->b_event_fd);
    if (enc->a_fd >= 0) close(enc->a_fd);
    enc->b_event_fd = -1;
    enc->a_fd = -1;
    enc->raw_count = 0;
    enc->sample_us = 0;
    enc->have_last_count = 0;
    enc->last_count = 0;
    enc->have_zero_count = 0;
    enc->zero_count = 0;
}

static int encoder_read(EncoderPRU *enc, int32_t *count_out, float *chainage_m_out, uint32_t *sample_us_out) {
    int32_t count = 0;
    uint32_t sample_us = 0;

    if (!enc) return -1;

    if (enc->is_pru) {
        int attempt;
        uint32_t status = 0;
        uint32_t count_check = 0;
        if (!enc->pru_count || !enc->pru_status || !enc->pru_sample_us || !enc->pru_count_check) return -1;

        for (attempt = 0; attempt < 8; ++attempt) {
            count = *enc->pru_count;
            status = *enc->pru_status;
            sample_us = *enc->pru_sample_us;
            count_check = *enc->pru_count_check;
            if ((((uint32_t)count) ^ PRU_COUNT_COOKIE) == count_check) {
                break;
            }
        }
        if ((((uint32_t)count) ^ PRU_COUNT_COOKIE) != count_check || status != 1u) {
            return -1;
        }
    } else {
        if (enc->a_fd < 0 || enc->b_event_fd < 0 || !enc->worker_running) return -1;
        count = __sync_fetch_and_add(&enc->raw_count, 0);
        sample_us = enc->sample_us;
    }

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
    if (chainage_m_out) {
        double distance_mm = (double)count * enc->mm_per_count;
        /* Shared frame/API names this field chainage_m, so convert mm to m. */
        *chainage_m_out = (float)(distance_mm / 1000.0);
    }
    if (sample_us_out) *sample_us_out = sample_us;
    return 0;
}

static float compute_twist(float cl_mm, float ch_m) {
    uint32_t idx = g_hist_head % TWIST_HISTORY_MAX;
    int32_t current_step_index;
    int32_t relative_step;
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

    /* Check completed baseline intervals based on 0.1m rounded display distance */
    float dist_from_anchor = fabsf(ch_m - (g_twist_anchor_step * g_twist_sample_step_m));
    float display_dist_m = roundf(dist_from_anchor * 10.0f) / 10.0f;
    int32_t completed_chords = (int32_t)(display_dist_m / g_twist_baseline_m);
    if (completed_chords < 1) return 0.0f;

    direction = (relative_step >= 0) ? 1 : -1;
    target_step = g_twist_anchor_step + direction * ((completed_chords - 1) * g_twist_baseline_steps);
    int32_t chord_step = g_twist_anchor_step + direction * (completed_chords * g_twist_baseline_steps);

    {
        uint32_t best_target = 0;
        bool found_target = false;
        uint32_t best_chord = 0;
        bool found_chord = false;
        uint32_t n = (g_hist_head < TWIST_HISTORY_MAX) ? g_hist_head : TWIST_HISTORY_MAX;
        uint32_t i;

        for (i = 0; i < n; i++) {
            uint32_t j = (g_hist_head - 1u - i) % TWIST_HISTORY_MAX;
            if (!found_target && g_hist_step_idx[j] == target_step) {
                best_target = j;
                found_target = true;
            }
            if (!found_chord && g_hist_step_idx[j] == chord_step) {
                best_chord = j;
                found_chord = true;
            }
            if (found_target && found_chord) break;
        }
        if (!found_target) return 0.0f;
        {
            float cl_chord = found_chord ? g_hist[best_chord].cl_mm : cl_mm;
            float dcl = cl_chord - g_hist[best_target].cl_mm;
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
    int scl_miss_count = 0;
    int hc_cnt = 0;
    int hc_intval = ACQUISITION_HZ * STATUS_CHECK_SECS;
    int scl_read_div = SCL3300_READ_DIV > 0 ? SCL3300_READ_DIV : 1;
    int scl_read_cnt = 0;
    int gauge_read_hz = (int)env_double_any("RAIL_GAUGE_READ_HZ", (double)GAUGE_READ_HZ);
    if (gauge_read_hz < 1) gauge_read_hz = 1;
    if (gauge_read_hz > ACQUISITION_HZ) gauge_read_hz = ACQUISITION_HZ;
    int gauge_read_div = ACQUISITION_HZ / gauge_read_hz;
    int gauge_read_cnt = 0;
    const float cross_offset_mm = (float)env_double_any("RAIL_CROSS_LEVEL_OFFSET_MM", -10.0);
    struct timespec next;

    /* Use fair-share SCHED_OTHER (mild nice -5) instead of SCHED_FIFO so display/touch never starve. */
    if (setpriority(PRIO_PROCESS, 0, -5) != 0) {
        fprintf(stderr, "[MAIN] nice(-5) warning: %s\n", strerror(errno));
    }

    /* Lock memory pages to eliminate page-fault latency during real-time acquisition */
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0) {
        fprintf(stderr, "[MAIN] Warning: mlockall failed: %s\n", strerror(errno));
    }

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    if (shm_init() != 0) return 1;
    twist_config_init();
    gauge_init();
    ensure_spi_pinmux();
    ensure_encoder_pinmux();

    printf("=== Rail Inspection Sensor Service (shared memory) ===\n");
    printf("Gauge: live ADC around %.0f mm | encoder via GPIO edge events | %d Hz\n",
           (double)GAUGE_MM, ACQUISITION_HZ);
    printf("[SCL3300] cross_level_offset_mm=%.2f\n", (double)cross_offset_mm);
    printf("[SHM] Publishing latest frame at %s\n", RAIL_SHM_PATH);

    scl_ok_init = (scl3300_open(&g_scl) == 0);
    if (!scl_ok_init) {
        fprintf(stderr, "[MAIN] SCL3300 init failed. Running with last-good zeros.\n");
    }
    enc_ok_init = (encoder_open(&g_enc) == 0);
    if (!enc_ok_init) {
        fprintf(stderr, "[MAIN] GPIO encoder init failed. Running with chainage frozen at zero.\n");
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
                cl_mm = tmp + cross_offset_mm;
                last_cl = cl_mm;
                scl_status = 1u;
                last_scl_status = 1u;
                scl_miss_count = 0;
            } else {
                /*
                 * A single missed SCL3300 frame should not make the UI drop the
                 * cross-level card to "not ready". Keep the last-good value and
                 * only mark the inclinometer unhealthy after repeated failures.
                 */
                if (++scl_miss_count >= SCL3300_MAX_CRC_ERRORS) {
                    scl_status = 0u;
                    last_scl_status = 0u;
                    fprintf(stderr, "[SCL3300] read stalled; reinitializing SPI link\n");
                    scl3300_close(&g_scl);
                    if (scl3300_open(&g_scl) == 0) {
                        scl_miss_count = 0;
                        last_scl_status = g_scl.healthy ? 1u : 0u;
                    }
                }
            }
        }

        if (encoder_read(&g_enc, &encoder_count, &chainage_m, &encoder_sample_us) == 0) {
            encoder_status = 1u;
        }

        if (++gauge_read_cnt >= gauge_read_div) {
            gauge_read_cnt = 0;
            if (gauge_read_mm(&g_gauge, &gauge_mm) == 0) {
                last_gauge_mm = gauge_mm;
            }
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
            printf("[SCL3300] cross_level_mm=%.2f status=%u\n",
                   (double)cl_mm, scl_status);
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
