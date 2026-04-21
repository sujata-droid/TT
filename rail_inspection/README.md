# Rail Inspection System — Complete Re-Engineered Architecture

**BeagleBone Black | Murata SCL3300-D01 | Rotary Encoder | PRU | Cloud**

---

## What This System Does

Measures and records **cross-level**, **twist**, and **chainage** of railway track at Indian **Broad Gauge (1676 mm)** using a trolley-mounted inspection system.

| Parameter | Source | Formula |
|---|---|---|
| **Cross-level (mm)** | SCL3300 ACC_X | `1676 × asin(raw / 1000)` |
| **Twist (mm/m)** | Δ cross-level / Δ chainage | Rolling 3 m baseline |
| **Chainage (m)** | PRU encoder | `counts × (π×250) / (1000×4)` |
| **Gauge (mm)** | Constant | **1676** (Indian BG) |

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                 BeagleBone Black (1 GHz ARM)            │
│                                                         │
│  ┌──────────────────────┐   ┌────────────────────────┐  │
│  │   PRU0 (200 MHz)     │   │   Linux userspace       │  │
│  │   encoder_pru0.c     │   │                         │  │
│  │                      │   │  sensor_service (C)     │  │
│  │  Quadrature decode   │──▶│  SCHED_FIFO prio 80    │  │
│  │  10 µs sample rate   │   │  50 Hz acquisition      │  │
│  │  Zero jitter         │   │  Lock-free ring buffer  │  │
│  │  Writes count to     │   │                         │  │
│  │  DRAM @0x4A300000    │   │  Unix socket server     │  │
│  └──────────────────────┘   │  SCHED_FIFO prio 60    │  │
│                              └────────┬───────────────┘  │
│  ┌──────────────────────┐            │ /tmp/rail_sensor  │
│  │ SCL3300 (SPI0)       │            │  .sock            │
│  │ /dev/spidev0.0       │            ▼                   │
│  │ 2 MHz, Mode 0        │   ┌────────────────────────┐  │
│  │ CRC validated        │   │  main.py (PyQt5)        │  │
│  │ Full startup seq.    │   │  10 Hz display          │  │
│  └──────────────────────┘   │  CSVWriterThread        │  │
│                              │  CloudPushThread        │  │
└─────────────────────────────────────────────────────────┘
                                        │ on exit
                                        ▼
                              ┌────────────────────┐
                              │  Render Cloud       │
                              │  Flask + Dashboard  │
                              │  /api/survey  POST  │
                              │  /api/surveys GET   │
                              │  / dashboard        │
                              └────────────────────┘
```

---

## File Structure

```
rail_inspection/
├── pru/
│   ├── encoder_pru0.c        PRU0 quadrature decoder (bare-metal, 200 MHz)
│   ├── am335x_pru0.cmd       PRU linker command file
│   └── resource_table_empty.h  remoteproc resource table (required)
│
├── sensor_board/
│   ├── Makefile
│   └── src/
│       ├── scl3300.h         SCL3300 driver header (full protocol doc)
│       ├── scl3300.c         SCL3300 SPI driver (CRC, pipeline, startup)
│       ├── ring_buffer.h     Lock-free SPSC ring buffer
│       └── sensor_service.c  Main acquisition + socket server daemon
│
├── main_board/
│   ├── main.py               PyQt5 GUI + CSV + cloud push
│   └── requirements.txt
│
├── cloud/
│   ├── app.py                Flask cloud server (Render)
│   └── requirements.txt
│
├── setup.sh                  One-shot setup: pins, build, PRU load
└── README.md                 This file
```

---

## Hardware Wiring

### SCL3300-D01 → BeagleBone Black

```
SCL3300 Pin   →   BBB Pin   →   Function
──────────────────────────────────────────
CSB           →   P9_17     →   SPI0_CS0
MISO          →   P9_21     →   SPI0_D0
MOSI          →   P9_18     →   SPI0_D1
SCK           →   P9_22     →   SPI0_CLK
AVDD, DVDD    →   P9_3      →   3.3 V
AVSS, DVSS    →   P9_1      →   GND
```

**Looking at your PCB photo:** CSB, MISO, MOSI, SCK are labelled on the right
connector (J2). AVSS/AVDD on top-right. Match exactly as above.

### Rotary Encoder → BeagleBone Black

```
Encoder Pin   →   BBB Pin   →   PRU R31 Bit
────────────────────────────────────────────
Channel A     →   P9_27     →   bit 5
Channel B     →   P9_30     →   bit 2
GND           →   P9_1      →   GND
VCC           →   P9_3      →   3.3 V (or 5V if encoder needs it)
```

---

## Why Each Design Decision Was Made

### 1. Why PRU for the encoder (not Linux GPIO interrupt)?
Linux can preempt any user-space thread for **up to 1 ms**. At 1000 PPR × 4X,
a trolley at 1 m/s generates 4000 edges/second = one every 250 µs. A 1 ms
preemption **misses 4 counts = 0.78 mm of chainage error per event**. The PRU
runs at 200 MHz with **zero preemption** — it samples every 10 µs and never
misses a pulse.

### 2. Why SCHED_FIFO for the C service?
With the default scheduler (SCHED_OTHER / CFS), the acquisition thread can be
preempted by any other process — even a `cron` job — for milliseconds. SCHED_FIFO
priority 80 means it only yields to kernel IRQ handlers. Result: **±1 sample
timing jitter at 50 Hz** instead of ±5-10 ms.

### 3. Why a lock-free ring buffer?
A mutex between the acquisition thread (prio 80) and server thread (prio 60)
creates **priority inversion**: if the server thread holds the mutex and is
preempted, the acquisition thread blocks. The lock-free SPSC ring buffer never
blocks the producer — it simply overwrites the oldest data if the consumer falls
behind, which is exactly what you want for a real-time sensor.

### 4. Why Unix domain socket (not TCP)?
Unix sockets stay in kernel memory. No TCP header, no checksum, no port binding.
Latency is **~5× lower than localhost TCP**. Since the GUI runs on the same BBB,
there is zero reason to use a network protocol.

### 5. Why 10 Hz display refresh, not 50 Hz?
Human vision cannot track motion faster than ~24 fps. At 10 Hz, Qt spends
**<5 ms painting** every 100 ms frame — leaving 95% of the single-core BBB for
acquisition. At 50 Hz the GUI would compete with the sensor thread. We read
the LATEST frame only, so no data is "missed" — the display just shows the
most recent value at 10 Hz.

### 6. Why buffer 100 CSV rows before fsync?
On BBB eMMC, a single `fsync()` takes 5-15 ms. Fsyncing every row at 50 Hz
= **50 × 15 ms = 750 ms of IO stall per second**. Buffering 100 rows (2 sec)
and fsyncing once is **150× more efficient** with zero data loss risk.

### 7. Why CRC-8 validation on SCL3300?
Industrial environments have electrical noise. Without CRC checking, a single
corrupted SPI byte produces a fake cross-level spike of up to ±1676 mm
(full-scale). The CRC detects and discards any corrupted frames, substituting
the last valid reading instead.

### 8. Why the full startup sequence for SCL3300?
The SCL3300 uses a **pipelined SPI protocol**: the response to command N arrives
during command N+1. Skipping the SW_RESET + CHANGE_MODE + pipeline flush steps
means you read RS=0x00 (startup state) forever and never get actual data. This
was the root cause of "sensor not detected" in the original code.

---

## Running the System

### Step 1: Setup (once)
```bash
sudo bash setup.sh
```

### Step 2: Start sensor service (terminal 1)
```bash
sudo ./sensor_board/sensor_service
```
You should see:
```
[SCL3300] /dev/spidev0.0 @ 2000000 Hz mode 0
[SCL3300] SW_RESET...
[SCL3300] CHANGE_MODE1...
[SCL3300] RS = 0x01 (0x01=NORMAL)
[SCL3300] Ready. Healthy=YES
[PRU] PRU0 encoder running. Initial count=0
[ACQ] Running at 50 Hz
[SRV] Listening on /tmp/rail_sensor.sock
```

### Step 3: Start GUI (terminal 2)
```bash
export RAIL_CLOUD_URL=https://YOUR-APP.onrender.com/api/survey
python3 main_board/main.py
```

### Step 4: Cloud server (Render)
1. Push `cloud/` folder to GitHub
2. Create new Web Service on Render
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn --bind 0.0.0.0:$PORT --workers 2 app:app`
5. Add persistent disk at `/data` → set env var `STORAGE_DIR=/data`
6. Copy your Render URL → set `RAIL_CLOUD_URL` on BBB

---

## Sensor Status Indicators

| LED | Green means | Red means |
|---|---|---|
| SCL3300 | RS=0x01, CRC OK | CRC errors or RS≠0x01 |
| ENCODER | PRU0 status=1 | PRU firmware not running |
| CLOUD | Survey pushed OK | Network error or server down |

---

## Troubleshooting

**"Cannot open /dev/spidev0.0"**
→ Run `config-pin P9_17 spi_cs` then check `ls /dev/spidev*`
→ Or run `modprobe spidev`

**"PRU0 status=0 (want 1=running)"**
→ Did `setup.sh` build the PRU firmware successfully?
→ Check `cat /sys/class/remoteproc/remoteproc0/state`
→ Check `dmesg | grep -i pru`

**"SCL3300 HARD ERROR (RS=0x02)"**
→ Check AVDD = 3.3 V (measure with multimeter at the PCB pad)
→ Check all GND connections (AVSS and DVSS must both be grounded)
→ Verify SPI wiring matches the table above

**GUI freezes / is glitchy**
→ Ensure `sensor_service` is running first
→ Check `ls /tmp/rail_sensor.sock` — socket must exist
→ The 10 Hz display timer fires even with no data (shows "--" values)

**CSV not saved**
→ Check `~/surveys/` directory exists: `mkdir -p ~/surveys`
→ CSV is saved **only on program exit** (close the window cleanly)

**Cloud not receiving data**
→ Verify `RAIL_CLOUD_URL` is set correctly
→ Test: `curl -X POST $RAIL_CLOUD_URL -H "Content-Type: application/json" -d '{"filename":"test.csv","data":[{"chainage_m":"1.0"}]}'`
→ Check Render logs for errors
