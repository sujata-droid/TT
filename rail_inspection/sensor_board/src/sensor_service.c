/*
 * sensor_service.c -- Rail Inspection Sensor Acquisition Service
 * ==============================================================
 * Runs as root on BeagleBone Black.
 *
 * ARCHITECTURE (two threads):
 *
 *   Thread 1 [acquisition, SCHED_FIFO prio 80]
 *     -> Opens SCL3300 via spidev (Linux DMA-backed SPI)
 *     -> Maps PRU0 DRAM via /dev/mem to read encoder count
 *     -> Runs a precise 50 Hz clock_nanosleep() loop
 *     -> Computes cross-level, chainage, twist
 *     -> Pushes SensorFrame to lock-free ring buffer
 *
 *   Thread 2 [server, SCHED_FIFO prio 60]
 *     -> Listens on Unix domain socket /tmp/rail_sensor.sock
 *     -> Drains ring buffer, formats JSON, broadcasts to clients
 *     -> Handles multiple clients (up to 8)
 *
 * WHY UNIX SOCKET NOT TCP?
 *   Unix sockets stay in kernel memory -- no network stack overhead,
 *   no TCP header parsing, ~5x lower latency than localhost TCP.
 *   Since the GUI runs on the same BBB, there is zero reason to use TCP.
 *
 * WHY SCHED_FIFO?
 *   With SCHED_OTHER (default), the Linux CFS scheduler can preempt
 *   the acquisition thread for up to 1 ms.  At 50 Hz (20 ms period),
 *   that is a 5% jitter which accumulates in chainage calculation.
 *   SCHED_FIFO prio 80 means this thread ONLY yields to higher-prio
 *   kernel threads (IRQ handlers).  Zero jitter from user-space.
 *
 * WHY mlockall()?
 *   Without it, page faults can pause the RT thread for hundreds of
 *   microseconds while the kernel loads a page from swap.  mlockall()
 *   locks all current and future pages in RAM permanently.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <errno.h>
#include <signal.h>
#include <unistd.h>
#include <fcntl.h>
#include <time.h>
#include <math.h>
#include <pthread.h>
#include <sched.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>

#include "scl3300.h"
#include "ring_buffer.h"

/* ---- Configuration ------------------------------------------------- */
#define SOCKET_PATH          "/tmp/rail_sensor.sock"
#define ACQUISITION_HZ       50
#define ACQUISITION_NS       (1000000000L / ACQUISITION_HZ)  /* 20 ms  */
#define PRU0_DRAM_PHYS       0x4A300000UL
#define PRU0_DRAM_SIZE       0x1000u
#define ENCODER_PPR          1000          /* encoder pulses per rev      */
#define ENCODER_QUADX        4             /* PRU does 4x quadrature      */
#define ENCODER_WHEEL_CIRC   785.398f      /* pi * 250 mm wheel           */
#define ENCODER_MM_PER_COUNT (ENCODER_WHEEL_CIRC / (ENCODER_PPR * ENCODER_QUADX))
#define TWIST_HISTORY_MAX    200           /* samples in CL history       */
#define STATUS_CHECK_SECS    5             /* SCL3300 health check period */
#define MAX_CLIENTS          8

/* ---- Globals ------------------------------------------------------- */
static volatile sig_atomic_t g_run = 1;

static volatile uint32_t *g_pru_dram = NULL;
static int                 g_mem_fd  = -1;

static RingBuffer g_ring;
static SCL3300    g_scl;

/* Twist history */
typedef struct { float cl_mm; float ch_m; } CLSample;
static CLSample  g_hist[TWIST_HISTORY_MAX];
static uint32_t  g_hist_head = 0;

/* ---- Signal handler ----------------------------------------------- */
static void on_signal(int s) { (void)s; g_run = 0; }

/* ---- Timing -------------------------------------------------------- */
static int64_t mono_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000LL + ts.tv_nsec / 1000LL;
}
static void ts_add_ns(struct timespec *ts, long ns) {
    ts->tv_nsec += ns;
    if (ts->tv_nsec >= 1000000000L) { ts->tv_nsec -= 1000000000L; ts->tv_sec++; }
}

/* ---- PRU encoder --------------------------------------------------- */
static int pru_init(void) {
    g_mem_fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (g_mem_fd < 0) {
        fprintf(stderr, "[PRU] /dev/mem open failed: %s (need root)\n",
                strerror(errno));
        return -1;
    }
    g_pru_dram = (volatile uint32_t *)mmap(
        NULL, PRU0_DRAM_SIZE, PROT_READ | PROT_WRITE,
        MAP_SHARED, g_mem_fd, (off_t)PRU0_DRAM_PHYS);
    if (g_pru_dram == MAP_FAILED) {
        fprintf(stderr, "[PRU] mmap failed: %s\n", strerror(errno));
        close(g_mem_fd); g_mem_fd = -1; return -1;
    }
    uint32_t status = g_pru_dram[1];
    if (status != 1u) {
        fprintf(stderr,
            "[PRU] WARNING: PRU0 status=%u (want 1=running)\n"
            "      Did setup.sh load and start the PRU firmware?\n", status);
    } else {
        printf("[PRU] PRU0 encoder running. Initial count=%d\n",
               (int32_t)g_pru_dram[0]);
    }
    return 0;
}
static int32_t pru_count(void)   { return g_pru_dram ? (int32_t)g_pru_dram[0] : 0; }
static bool    pru_running(void) { return g_pru_dram && g_pru_dram[1] == 1u; }
static void    pru_cleanup(void) {
    if (g_pru_dram && g_pru_dram != MAP_FAILED) {
        munmap((void *)g_pru_dram, PRU0_DRAM_SIZE); g_pru_dram = NULL;
    }
    if (g_mem_fd >= 0) { close(g_mem_fd); g_mem_fd = -1; }
}

/* ---- Twist --------------------------------------------------------- */
static float compute_twist(float cl_mm, float ch_m) {
    uint32_t idx  = g_hist_head % TWIST_HISTORY_MAX;
    g_hist[idx].cl_mm = cl_mm;
    g_hist[idx].ch_m  = ch_m;
    g_hist_head++;
    if (g_hist_head < 2) return 0.0f;

    float target_ch = ch_m - TWIST_BASELINE_M;
    float best_err  = 1e9f;
    uint32_t best   = 0;
    bool found      = false;
    uint32_t n      = (g_hist_head < TWIST_HISTORY_MAX) ? g_hist_head : TWIST_HISTORY_MAX;

    for (uint32_t i = 1; i < n; i++) {
        uint32_t j = (g_hist_head - 1u - i) % TWIST_HISTORY_MAX;
        float err = fabsf(g_hist[j].ch_m - target_ch);
        if (err < best_err) { best_err = err; best = j; found = true; }
    }
    if (!found || best_err > TWIST_BASELINE_M * 0.6f) return 0.0f;

    float dcl = cl_mm   - g_hist[best].cl_mm;
    float dch = ch_m    - g_hist[best].ch_m;
    if (fabsf(dch) < 0.001f) return 0.0f;
    return dcl / dch;   /* mm/m */
}

/* ---- Acquisition thread (SCHED_FIFO prio 80) ---------------------- */
static void *acq_thread(void *arg) {
    (void)arg;
    struct sched_param sp = { .sched_priority = 80 };
    if (pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp) != 0)
        fprintf(stderr, "[ACQ] Cannot set SCHED_FIFO (run as root)\n");

    /* Init sensors */
    int scl_ok_init = (scl3300_open(&g_scl) == 0);
    if (!scl_ok_init)
        fprintf(stderr, "[ACQ] SCL3300 init failed. Running with zeros.\n");

    if (pru_init() < 0)
        fprintf(stderr, "[ACQ] PRU init failed. Encoder shows 0.\n");

    int32_t enc_base  = pru_count();
    bool    first     = true;
    float   last_cl   = 0.0f;
    int     hc_cnt    = 0;
    int     hc_intval = ACQUISITION_HZ * STATUS_CHECK_SECS;

    struct timespec next;
    clock_gettime(CLOCK_MONOTONIC, &next);
    printf("[ACQ] Running at %d Hz\n", ACQUISITION_HZ);

    while (g_run) {
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
        ts_add_ns(&next, ACQUISITION_NS);

        /* -- Read inclinometer -- */
        float cl_mm = last_cl;
        uint8_t scl_status = 0;
        if (g_scl.initialized) {
            float tmp;
            if (scl3300_read_cross_level(&g_scl, &tmp) == 0) {
                cl_mm = tmp; last_cl = tmp; scl_status = 1;
            }
        }

        /* -- Read encoder via PRU DRAM -- */
        int32_t enc_now = pru_count();
        int32_t enc_delta = enc_now - enc_base;
        float chainage_m = (float)abs(enc_delta) * ENCODER_MM_PER_COUNT / 1000.0f;

        /* -- Twist -- */
        float twist = first ? 0.0f : compute_twist(cl_mm, chainage_m);
        first = false;

        /* -- Periodic health check -- */
        if (++hc_cnt >= hc_intval) {
            hc_cnt = 0;
            if (g_scl.initialized) scl3300_health_check(&g_scl);
        }

        /* -- Push frame -- */
        SensorFrame f;
        f.timestamp_us    = mono_us();
        f.cross_level_mm  = cl_mm;
        f.twist_mm_per_m  = twist;
        f.chainage_m      = chainage_m;
        f.gauge_mm        = GAUGE_MM;
        f.scl3300_ok      = scl_status;
        f.encoder_ok      = pru_running() ? 1u : 0u;
        f._pad[0] = f._pad[1] = f._pad[2] = f._pad[3] = 0;
        f._pad[4] = f._pad[5] = 0;
        rb_push(&g_ring, &f);
    }

    scl3300_close(&g_scl);
    pru_cleanup();
    printf("[ACQ] Thread stopped.\n");
    return NULL;
}

/* ---- Server thread ------------------------------------------------ */
static int make_unix_socket(void) {
    unlink(SOCKET_PATH);
    int fd = socket(AF_UNIX, SOCK_SEQPACKET, 0);
    if (fd < 0) { perror("[SRV] socket"); return -1; }
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, SOCKET_PATH, sizeof(addr.sun_path) - 1);
    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("[SRV] bind"); close(fd); return -1;
    }
    chmod(SOCKET_PATH, 0666);  /* allow non-root GUI to connect */
    if (listen(fd, 4) < 0) { perror("[SRV] listen"); close(fd); return -1; }
    return fd;
}

static void *srv_thread(void *arg) {
    (void)arg;
    struct sched_param sp = { .sched_priority = 60 };
    pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);

    int srv = make_unix_socket();
    if (srv < 0) { fprintf(stderr, "[SRV] Socket setup failed.\n"); return NULL; }
    fcntl(srv, F_SETFL, O_NONBLOCK);

    int  cli[MAX_CLIENTS];
    int  ncli = 0;
    memset(cli, -1, sizeof(cli));
    char jbuf[256];
    printf("[SRV] Listening on %s\n", SOCKET_PATH);

    while (g_run) {
        /* Accept new connections */
        int c = accept(srv, NULL, NULL);
        if (c >= 0 && ncli < MAX_CLIENTS) {
            fcntl(c, F_SETFL, O_NONBLOCK);
            cli[ncli++] = c;
            printf("[SRV] Client connected (%d total)\n", ncli);
        }

        /* Drain ring buffer and broadcast */
        SensorFrame f;
        while (rb_pop(&g_ring, &f)) {
            int n = snprintf(jbuf, sizeof(jbuf),
                "{\"ts\":%lld,\"cl\":%.3f,\"tw\":%.3f,"
                "\"ch\":%.3f,\"ga\":%.0f,\"s0\":%d,\"s1\":%d}\n",
                (long long)f.timestamp_us,
                (double)f.cross_level_mm, (double)f.twist_mm_per_m,
                (double)f.chainage_m,     (double)f.gauge_mm,
                (int)f.scl3300_ok,        (int)f.encoder_ok);

            for (int i = 0; i < ncli; ) {
                ssize_t s = send(cli[i], jbuf, (size_t)n, MSG_NOSIGNAL);
                if (s < 0 && (errno == EPIPE || errno == ECONNRESET
                              || errno == ENOTCONN)) {
                    printf("[SRV] Client disconnected.\n");
                    close(cli[i]);
                    cli[i] = cli[--ncli];
                    cli[ncli] = -1;
                } else { i++; }
            }
        }

        /* 1 ms rest -- keeps CPU available for acquisition thread */
        struct timespec sl = { 0, 1000000L };
        nanosleep(&sl, NULL);
    }

    for (int i = 0; i < ncli; i++) if (cli[i] >= 0) close(cli[i]);
    close(srv);
    unlink(SOCKET_PATH);
    printf("[SRV] Thread stopped.\n");
    return NULL;
}

/* ---- main ---------------------------------------------------------- */
int main(void) {
    /* Lock all RAM pages -- prevents page-fault stalls in RT loop */
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0)
        fprintf(stderr, "[MAIN] mlockall warning: %s\n", strerror(errno));

    /* Ignore SIGPIPE (broken client connections) */
    signal(SIGPIPE, SIG_IGN);
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_signal;
    sigaction(SIGINT,  &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    rb_init(&g_ring);

    printf("=== Rail Inspection Sensor Service ===\n");
    printf("Gauge: %.0f mm | %.4f mm/count | %d Hz\n",
           (double)GAUGE_MM, (double)ENCODER_MM_PER_COUNT, ACQUISITION_HZ);

    pthread_t t_acq, t_srv;
    if (pthread_create(&t_acq, NULL, acq_thread, NULL) != 0) {
        perror("pthread_create acq"); return 1;
    }
    if (pthread_create(&t_srv, NULL, srv_thread, NULL) != 0) {
        perror("pthread_create srv"); g_run = 0;
        pthread_join(t_acq, NULL); return 1;
    }

    printf("[MAIN] Running. Ctrl+C to stop.\n");
    pthread_join(t_acq, NULL);
    pthread_join(t_srv, NULL);
    printf("[MAIN] Clean exit.\n");
    return 0;
}
