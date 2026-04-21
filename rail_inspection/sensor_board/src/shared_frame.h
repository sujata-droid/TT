/*
 * shared_frame.h -- Shared memory layout for the rail inspection backend
 */

#ifndef SHARED_FRAME_H_
#define SHARED_FRAME_H_

#include <stdint.h>

#define RAIL_SHM_NAME       "/rail_sensor_shm"
#define RAIL_SHM_PATH       "/dev/shm/rail_sensor_shm"
#define RAIL_SHM_MAGIC      0x5241494cU /* 'RAIL' */
#define RAIL_SHM_VERSION    1U

#pragma pack(push, 1)
typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t seq;
    uint32_t update_count;
    int64_t  timestamp_us;
    double   cross_level_mm;
    double   twist_mm_per_m;
    double   chainage_m;
    double   gauge_mm;
    int32_t  encoder_count;
    uint8_t  scl3300_ok;
    uint8_t  encoder_ok;
    uint8_t  service_ok;
    uint8_t  reserved0;
} RailSharedFrame;
#pragma pack(pop)

#endif /* SHARED_FRAME_H_ */
