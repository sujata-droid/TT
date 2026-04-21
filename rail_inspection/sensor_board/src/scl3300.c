/*
 * scl3300.c -- Murata SCL3300-D01 SPI Driver
 */
#include "scl3300.h"
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <math.h>
#include <time.h>
#include <sys/ioctl.h>
#include <linux/spi/spidev.h>

static void ms_sleep(uint32_t ms) {
    struct timespec ts = { (time_t)(ms/1000u), (long)((ms%1000u)*1000000L) };
    nanosleep(&ts, NULL);
}
static void us_sleep(uint32_t us) {
    struct timespec ts = { 0, (long)(us * 1000L) };
    nanosleep(&ts, NULL);
}

uint8_t scl3300_crc8(uint8_t b0, uint8_t b1, uint8_t b2) {
    static uint8_t tbl[256];
    static int     rdy = 0;
    if (!rdy) {
        for (int i = 0; i < 256; i++) {
            uint8_t c = (uint8_t)i;
            for (int j = 0; j < 8; j++)
                c = (c & 0x80u) ? (uint8_t)((c << 1) ^ 0x1Du) : (uint8_t)(c << 1);
            tbl[i] = c;
        }
        rdy = 1;
    }
    uint8_t crc = 0xFFu;
    crc = tbl[crc ^ b0];
    crc = tbl[crc ^ b1];
    crc = tbl[crc ^ b2];
    return crc ^ 0xFFu;
}

static int spi_xfer32(int fd, uint32_t tx, uint32_t *rx_out) {
    uint8_t tx_buf[4], rx_buf[4];
    tx_buf[0] = (uint8_t)((tx >> 24) & 0xFFu);
    tx_buf[1] = (uint8_t)((tx >> 16) & 0xFFu);
    tx_buf[2] = (uint8_t)((tx >>  8) & 0xFFu);
    tx_buf[3] = (uint8_t)( tx        & 0xFFu);

    struct spi_ioc_transfer xfer;
    memset(&xfer, 0, sizeof(xfer));
    xfer.tx_buf        = (unsigned long)tx_buf;
    xfer.rx_buf        = (unsigned long)rx_buf;
    xfer.len           = 4;
    xfer.speed_hz      = SCL3300_SPI_SPEED_HZ;
    xfer.bits_per_word = 8;
    xfer.cs_change     = 0;
    xfer.delay_usecs   = SCL3300_CS_HIGH_US;

    if (ioctl(fd, SPI_IOC_MESSAGE(1), &xfer) < 0) {
        perror("[SCL3300] SPI_IOC_MESSAGE failed");
        return -1;
    }
    us_sleep(SCL3300_CS_HIGH_US);

    if (rx_out) {
        *rx_out = ((uint32_t)rx_buf[0] << 24) | ((uint32_t)rx_buf[1] << 16)
                | ((uint32_t)rx_buf[2] <<  8) | ((uint32_t)rx_buf[3]);
    }
    return 0;
}

static inline uint8_t frame_rs(uint32_t rx)   { return (uint8_t)((rx >>  8) & 0x03u); }
static inline int16_t frame_data(uint32_t rx)  { return (int16_t)((rx >> 10) & 0xFFFFu); }
static inline int     frame_crc_ok(uint32_t rx) {
    uint8_t b0=(uint8_t)((rx>>24)&0xFF), b1=(uint8_t)((rx>>16)&0xFF),
            b2=(uint8_t)((rx>>8)&0xFF),  b3=(uint8_t)(rx&0xFF);
    return b3 == scl3300_crc8(b0, b1, b2);
}

int scl3300_open(SCL3300 *dev) {
    memset(dev, 0, sizeof(SCL3300));
    dev->spi_fd = -1;

    dev->spi_fd = open(SCL3300_SPI_DEV, O_RDWR);
    if (dev->spi_fd < 0) {
        fprintf(stderr,
            "[SCL3300] Cannot open %s: %s\n"
            "          Check: config-pin P9_17 spi_cs && run as root\n",
            SCL3300_SPI_DEV, strerror(errno));
        return -1;
    }

    uint8_t  mode  = SCL3300_SPI_MODE;
    uint8_t  bits  = SCL3300_SPI_BITS;
    uint32_t speed = SCL3300_SPI_SPEED_HZ;
    if (ioctl(dev->spi_fd, SPI_IOC_WR_MODE,          &mode)  < 0 ||
        ioctl(dev->spi_fd, SPI_IOC_WR_BITS_PER_WORD, &bits)  < 0 ||
        ioctl(dev->spi_fd, SPI_IOC_WR_MAX_SPEED_HZ,  &speed) < 0) {
        fprintf(stderr, "[SCL3300] SPI config failed: %s\n", strerror(errno));
        goto fail;
    }
    printf("[SCL3300] %s @ %u Hz mode %u\n", SCL3300_SPI_DEV, speed, mode);

    uint32_t rx;
    /* Startup sequence per datasheet section 4.2 */
    ms_sleep(SCL3300_STARTUP_MS);
    printf("[SCL3300] SW_RESET...\n");
    if (spi_xfer32(dev->spi_fd, CMD_SW_RESET,      &rx) < 0) goto fail;
    ms_sleep(SCL3300_RESET_DELAY_MS);
    printf("[SCL3300] CHANGE_MODE1...\n");
    if (spi_xfer32(dev->spi_fd, CMD_CHANGE_MODE1,  &rx) < 0) goto fail;
    ms_sleep(SCL3300_MODE_DELAY_MS);

    /* Three-transfer pipeline flush to get actual STATUS */
    printf("[SCL3300] Pipeline flush...\n");
    if (spi_xfer32(dev->spi_fd, CMD_READ_STATUS, &rx) < 0) goto fail;
    if (spi_xfer32(dev->spi_fd, CMD_READ_STATUS, &rx) < 0) goto fail;
    if (spi_xfer32(dev->spi_fd, CMD_DUMMY,        &rx) < 0) goto fail;

    dev->rs_field = frame_rs(rx);
    printf("[SCL3300] RS = 0x%02X (0x01=NORMAL)\n", dev->rs_field);

    if (dev->rs_field == RS_STARTUP) {
        ms_sleep(10);
        if (spi_xfer32(dev->spi_fd, CMD_READ_STATUS, &rx) < 0) goto fail;
        if (spi_xfer32(dev->spi_fd, CMD_DUMMY,        &rx) < 0) goto fail;
        dev->rs_field = frame_rs(rx);
        printf("[SCL3300] RS after extra wait = 0x%02X\n", dev->rs_field);
    }

    if (dev->rs_field == RS_HARD_ERR) {
        fprintf(stderr,
            "[SCL3300] HARD ERROR. Check AVDD=3.3V, AVSS/DVSS=GND, wiring.\n");
        goto fail;
    }

    /* Prime pipeline for ACC_X reads */
    if (spi_xfer32(dev->spi_fd, CMD_READ_ACC_X, &rx) < 0) goto fail;

    dev->initialized = 1;
    dev->healthy     = (dev->rs_field == RS_NORMAL);
    printf("[SCL3300] Ready. Healthy=%s\n", dev->healthy ? "YES" : "WARN");
    return 0;

fail:
    if (dev->spi_fd >= 0) { close(dev->spi_fd); dev->spi_fd = -1; }
    return -1;
}

int scl3300_read_cross_level(SCL3300 *dev, float *out_mm) {
    if (!dev->initialized || dev->spi_fd < 0) return -1;
    uint32_t rx;
    if (spi_xfer32(dev->spi_fd, CMD_READ_ACC_X, &rx) < 0) {
        dev->healthy = 0; return -1;
    }
    if (!frame_crc_ok(rx)) {
        if (++dev->crc_error_count >= SCL3300_MAX_CRC_ERRORS) {
            dev->healthy = 0;
            fprintf(stderr, "[SCL3300] %u consecutive CRC errors\n",
                    dev->crc_error_count);
        }
        *out_mm = dev->last_cl_mm;
        return -1;
    }
    dev->crc_error_count = 0;
    dev->rs_field = frame_rs(rx);
    if (dev->rs_field != RS_NORMAL) {
        dev->healthy = 0; *out_mm = dev->last_cl_mm; return -1;
    }
    dev->healthy = 1;
    int16_t raw = frame_data(rx);
    float acc_g = (float)raw / SCL3300_SENSITIVITY;
    if (acc_g >  1.0f) acc_g =  1.0f;
    if (acc_g < -1.0f) acc_g = -1.0f;
    float cl = asinf(acc_g) * GAUGE_MM;
    dev->last_cl_mm = cl;
    *out_mm = cl;
    return 0;
}

bool scl3300_health_check(SCL3300 *dev) {
    if (!dev->initialized || dev->spi_fd < 0) return false;
    uint32_t rx;
    if (spi_xfer32(dev->spi_fd, CMD_READ_STATUS, &rx) < 0) return false;
    if (spi_xfer32(dev->spi_fd, CMD_READ_STATUS, &rx) < 0) return false;
    if (spi_xfer32(dev->spi_fd, CMD_DUMMY,        &rx) < 0) return false;
    dev->rs_field = frame_rs(rx);
    dev->healthy  = (dev->rs_field == RS_NORMAL);
    spi_xfer32(dev->spi_fd, CMD_READ_ACC_X, &rx);  /* re-prime pipeline */
    return dev->healthy;
}

void scl3300_close(SCL3300 *dev) {
    if (dev->spi_fd >= 0) { close(dev->spi_fd); dev->spi_fd = -1; }
    dev->initialized = 0;
    dev->healthy     = 0;
}
