# Rail Track Geometry Inspection System — System Architecture, RCA, CPU/RAM & Complete Storage Report

**Target Hardware:** BeagleBone Black Industrial (Single-Core ARM Cortex-A8 @ `300–1000 MHz`, `483 MB` DDR3 RAM, `0 MB` Swap, `3.5 GB` eMMC)  
**Runtime Pipeline:** C Sensor Service (`sensor_service`) -> POSIX Shared Memory (`/dev/shm/rail_sensor_shm`) -> Python/PyQt5 Touch UI (`launch_railgui25_backend.py` + `railgui25.py`) -> Xorg (HDMI `1024x768`, Software Rendering) + Daly BMS UART5 Monitor (`tools/daly_bms.py`)  
**Updated:** November 12, 2026  

---

## 1. Executive Summary

This report documents the live CPU, RAM, disk/partition, and code/file storage footprint of the **Rail Track Geometry Inspection System**, together with the root-cause analysis (RCA) of intermittent display freezes and all software, BMS telemetry, UI scaling, and storage-management updates deployed to the BeagleBone Black (`192.168.7.2`).

Key findings from live on-device telemetry:
- **CPU Headroom:** The single-core Cortex-A8 runs at **75%–82% idle** (`vmstat`), with the full rail inspection software stack using **~16.6% CPU** combined.
- **RAM Headroom:** Out of **483 MB** total RAM (`0 MB` swap), only **82 MB** is actively used and **384 MB (79.5%) is available**. The core inspection processes consume **~59.1 MB RSS** (`~99.7 MB` including `Xorg`, `gpsd`, and `openvpn`).
- **eMMC Disk Storage (`/dev/mmcblk1p1`):** Out of **3.5 GB** total root capacity, **2.0 GB (62%) is used** and **1.3 GB is free**, following OS factory-bloat cleanup (`~424 MB` freed) and removal of 593 pre-November-2026 survey CSVs (`89.62 MB` freed).
- **Root Cause of Earlier Display Freezes:** Freezes were **not** caused by RAM exhaustion, CSV growth, or distance traveled—they were caused by five distinct hardware, scheduling (`SCHED_FIFO 98`), PRU1 load delay, software blur, and blocking USB/cloud upload issues documented in Section 4.

---

## 2. Live BeagleBone Black CPU & RAM Resource Footprint

Measured directly on the live BBB (`192.168.7.2`) with all production daemons running (`16:59–17:01 IST`, Nov 12, 2026).

### 2.1 CPU Specification & Load (`lscpu` & `vmstat 1 3`)
| Parameter | Measured Value | Notes |
|---|---|---|
| Architecture / Model | `armv7l` / ARM Cortex-A8 (`r3p2`) | Single core (`1` CPU, `1` thread/core), Little Endian |
| Clock Speed | `300.0 MHz` min / `1000.0 MHz` max | `995.32 BogoMIPS` |
| SIMD / Floating Point | `neon`, `vfp`, `vfpv3`, `vfpd32` | Hardware VFPv3; no 3D GPU enabled for Qt |
| Live CPU Utilization | `us: 13–18%`, `sy: 4–10%`, `wa: 0%` | **75%–82% CPU idle** under active telemetry + GUI |
| Load / Interrupts (`vmstat`) | `~615–630` interrupts/s, `~1,226–1,319` ctx switches/s | PRU1 handles encoder quadrature off the main ARM core |

### 2.2 System RAM & Swap Summary (`free -m`)
| Metric | Before Cleanup | After Cleanup & Fixes | % of Total (`483 MB`) |
|---|---|---|---|
| Total DDR3 RAM | `483 MB` | **`483 MB`** | `100.0%` |
| Used RAM | `104 MB` | **`82 MB`** | `17.0%` |
| Free RAM (unallocated) | — | **`213 MB`** | `44.1%` |
| Buffers / Page Cache | — | **`187 MB`** | `38.7%` |
| Shared Memory (`tmpfs` / `shm`) | — | **`4 MB`** | `0.8%` |
| **Available RAM** | `366 MB` | **`384 MB`** | **`79.5%` available** |
| Swap Space | `0 MB` | **`0 MB`** | No swap configured; well clear of OOM |

*Note: Stopping/disabling factory `bonescript-autorun` (`nodejs autorun.js`, `39.4 MB` RSS) permanently reclaimed ~`39 MB` of RAM and cut background CPU jitter.*

### 2.3 Per-Process CPU & RAM Footprint (`ps`)
| Process / Command | Role in Project | Sched (`CLS` / `NI`) | CPU % | % RAM | RSS (Physical) | VSZ (Virtual) |
|---|---|---|---|---|---|---|
| `launch_railgui25_backend.py` (`python3`) | PyQt5 Touch UI + SHM Reader + CSV Logger + Cloud/USB threads | `TS` (`0`) | `9.9%` | `9.2%` | `44.6 MB` (`45,680 KB`) | `132.6 MB` (`135,748 KB`) |
| `Xorg` (`:0 -nocursor`) | X11 display server (`1024x768` HDMI + touchscreen HID) | `TS` (`-1`) | `0.8%` | `6.8%` | `33.1 MB` (`33,872 KB`) | `97.7 MB` (`100,084 KB`) |
| `daly_bms.py --daemon` (`python3`) | Daly BMS / Pico monitor (`UART5 /dev/ttyS5`, 41-col CSV) | `TS` (`0`) | `0.6%` | `2.1%` | `10.3 MB` (`10,580 KB`) | `17.6 MB` (`17,984 KB`) |
| `openvpn` (`lwtmt.conf`) | VPN tunnel for remote cloud & diagnostics | `TS` (`0`) | `0.4%` | `0.9%` | `4.6 MB` (`4,700 KB`) | `6.7 MB` (`6,876 KB`) |
| `gpsd` (`-n /dev/ttyO4`) | GNSS receiver daemon | `TS` (`-10`) | `0.1%` | `0.5%` | `2.9 MB` (`2,960 KB`) | `13.0 MB` (`13,344 KB`) |
| `bash` (sensor launcher wrapper) | Environment setup launching `sensor_service` | `TS` (`0`) | `0.0%` | `0.4%` | `2.3 MB` (`2,364 KB`) | `6.9 MB` (`7,048 KB`) |
| `sensor_service` (C binary) | High-speed SCL3300 SPI + Laser ADC + PRU1/GPIO Encoder -> SHM | `TS` (`-5`) | `4.8%` | `0.3%` | `1.9 MB` (`1,924 KB`) | `2.1 MB` (`2,180 KB`) |
| **Total Rail Application Daemons** *(excl. Xorg/OS)* | `launch_railgui25_backend` + `sensor_service` + `daly_bms` + wrapper | — | **`15.3%`** | **`12.0%`** | **`59.1 MB`** (`60,548 KB`) | `159.2 MB` |
| **Total Full System Pipeline** *(incl. Xorg, gpsd, openvpn)* | Entire active inspection + display + telemetry stack | — | **`16.6%`** | **`20.2%`** | **`99.7 MB`** (`102,080 KB`) | `281.2 MB` |

*(Non-rail background OS services using the remaining small RAM share include `NetworkManager` `10.9 MB`, `ModemManager` `7.1 MB`, `systemd` `6.6 MB` + `6.0 MB`, `nginx` `5.5 MB`, `systemd-journal` `5.4 MB`, `polkitd`/`sshd`/`connmand`/`wpa_supplicant` ~`4–5 MB` each.)*

---

## 3. Complete Disks, Partitions, Code & Data File Storage Inventory

### 3.1 All Block Devices & Mounted Filesystems on BBB (`lsblk` & `df -h`)
| Device / Filesystem | Type / Mount | Total Size | Used | Available | Use % | Contents |
|---|---|---|---|---|---|---|
| `mmcblk1` (`eMMC` flash) | Physical disk | `3.56 GiB` (`3,827,957,760 B`) | — | — | — | Onboard BeagleBone Black Industrial eMMC |
| **`/dev/mmcblk1p1`** | ext4 mounted at **`/`** | **`3.5 GB`** (`3,823,763,456 B`) | **`2.0 GB`** | **`1.3 GB`** | **`62%`** | Ubuntu OS, libraries, code, survey CSVs, BMS logs |
| `mmcblk1boot0` / `boot1` | Hardware boot partitions | `2.0 MiB` each | — | — | — | U-Boot / SPL bootloader regions |
| `tmpfs` (`/dev/shm`) | RAM-backed shm (`temp`) | `242 MB` | `1.6 MB` | `241 MB` | `1%` | Contains `/dev/shm/rail_sensor_shm` (`64 bytes`) |
| `tmpfs` (`/run`) | RAM-backed runtime | `49 MB` | `2.6 MB` | `46 MB` | `6%` | Runtime PIDs, systemd sockets, `udev` state |
| `tmpfs` (`/run/lock`) | RAM-backed locks | `5.0 MB` | `4.0 KB` | `5.0 MB` | `1%` | Device lock files |
| `udev` (`/dev`) | Device tree devfs | `214 MB` | `0 B` | `214 MB` | `0%` | Device nodes (`/dev/ttyS5`, `/dev/spidev1.0`, etc.) |
| `tmpfs` (`/run/user/1000`) | User session tmpfs | `49 MB` | `0 B` | `49 MB` | `0%` | Session runtime directory |
| **RAM Ephemeral Files** (`/tmp`) | Temp logs / JSON state | `1.2 MB` total | — | — | — | `/tmp/battery_status.json` (`1.1 KB`), deploy stage |

*Note on ext4 5% root reserve:* `df` computes `Use%` as `Used / (Used + Available)` (`~3.35 GB` non-root usable space). Our new **80% BBB Storage Monitor** uses this exact same `df`-aligned formula so the UI warning matches Linux `df` output.

### 3.2 BBB Flash Breakdown by OS Directory (`du -xh --max-depth=1 /`)
| Directory on `/` | Size | Purpose |
|---|---|---|
| `/usr` | `1.4 GB` | Ubuntu OS packages, Python 3, PyQt5, Xorg, GCC, TI PRU (`clpru`) compiler |
| `/var` | `232 MB` | Package metadata & logs (`/var/log` trimmed to `8.7 MB`) |
| `/lib` | `220 MB` | Linux kernel modules, shared libraries, DTBO overlays (`/lib/firmware`) |
| **`/home`** | **`74 MB`** | User home: **`/home/debian/surveys` (`70 MB`)**, **`/home/debian/trolley` (`1.5 MB`)**, **`/home/debian/DALY_BMS_Logs` (`24 KB`)** |
| `/opt` | `27 MB` | Retained `/opt/scripts` (`config-pin`, pinmux helpers); removed unused Node-RED/Cloud9 |
| `/boot` | `27 MB` | Kernel image, initrd, `uEnv.txt` (`BB-NHDMI-NO-UART5-00A0` + `BB-UART5-00A0` DTBOs) |
| `/bin`, `/sbin`, `/root`, `/etc`, `/tmp` | `27.4 MB` | Core binaries (`9.0 + 6.6 MB`), root home (`6.3 MB`), system config (`4.3 MB`), `/tmp` (`1.2 MB`) |
| **Total `/` Used** | **`2.0 GB`** | **1.3 GB free (`38%` usable capacity remaining)** |

### 3.3 Project Code & Data Files on BBB (`/home/debian`)
| Path on BBB | Disk Usage | File Count / Details |
|---|---|---|
| **`/home/debian/trolley`** *(Total Code)* | **`1.5 MB`** | Complete deployed application & compiled C/PRU binaries |
| — `/home/debian/trolley/tools` | `560 KB` | `daly_bms.py`, `export_surveys_to_usb.py`, sensor/encoder console utilities |
| — `/home/debian/trolley/bbb_runtime` | `256 KB` | `launch_railgui25_backend.py`, `backend_bridge.py`, launcher script & runtime logs |
| — `/home/debian/trolley/railgui25.py` | `216 KB` | Main PyQt5 touchscreen GUI (Dashboard, Calibration, Data Entry, Graph, CSV Manager) |
| — `/home/debian/trolley/sensor_board` | `200 KB` | Compiled `sensor_service` binary + `src/sensor_service.c` + `src/scl3300.c` |
| — `/home/debian/trolley/__pycache__` | `164 KB` | Python bytecode cache |
| — `/home/debian/trolley/pru` | `64 KB` | Compiled `encoder_pru1.out` + PRU firmware C source & linker scripts |
| — `/home/debian/trolley/setup.sh` | `12 KB` | Master BBB setup script (dependencies, PRU modules, services) |
| — Root configs & shell scripts | `~56 KB` | `rail_config.json`, `laser_cal.json`, `battery_pct.txt`, `setup_encoder_*.sh`, `run_*.sh` (`4 KB` each) |
| **`/home/debian/surveys`** *(Survey Data)* | **`70 MB`** | **165 CSV files** kept from `2026-11-02` to `2026-11-12` *(593 pre-Nov CSVs / `89.62 MB` removed)* |
| **`/home/debian/DALY_BMS_Logs`** *(Battery Logs)* | **`24 KB`** | 5 daily 41-column Daly BMS telemetry CSV files (`2026-11-12`) |

### 3.4 Development PC Workspace Storage Breakdown (`rail_inspection`)
| File / Folder on Dev PC | Count | Size | Notes |
|---|---|---|---|
| `Hardware Connection source.docx` | 1 file | `3,697.3 KB` (`3.61 MB`) | Reference hardware diagram document *(not deployed to BBB)* |
| `tools/` | 81 files | `819.6 KB` | Deployment (`bbb_deploy.py`), Daly BMS monitor, USB exporter, bench tools |
| `bbb_runtime/` | 8 files | `410.3 KB` | BBB runtime patches, SHM bridge, systemd/autostart scripts |
| `railgui25.py` | 1 file | `212.3 KB` | Main GUI application source (`5,300+` lines) |
| `cloud/` | 6 files | `110.1 KB` | Reference Flask cloud dashboard (`app.py`, templates) |
| `sensor_board/` | 6 files | `88.8 KB` | C real-time daemon sources (`sensor_service.c`, `scl3300.c`, Makefile) |
| Documentation & Specs | 6 files | `220.4 KB` | `Hardware Connection and Interface Manual.docx` (`40.1 KB`), `Sensor Pin Connection Manual.docx` (`36.4 KB`), `uabams_spec_extracted.txt` (`28.1 KB`), `README.md` (`14.0 KB`), this `.md` + `.pdf` |
| `pru/` | 5 files | `14.1 KB` | PRU1 hardware quadrature firmware source & linker command files |
| Root shell scripts & JSON configs | 14 files | `24.0 KB` | `setup.sh` (`10.5 KB`), `setup_encoder_*.sh`, `run_*.sh`, `rail_config.json`, `laser_cal.json`, `render.yaml`, `requirements.txt` |
| Local dev caches *(un-deployed)* | 167 files | `1,563.3 KB` (`1.53 MB`) | `scratch/` (`961.3 KB`), `__pycache__/` (`534.7 KB`), `.kivy/` (`66.6 KB`), `.vscode/` (`0.7 KB`), `tools_test/` (`0.9 KB`) |

*Cleanup applied on dev PC:* Removed unused Python 3.12 `.venv` (`184.21 MB`) and `.tmp` (`53.12 MB`), freeing **`~237 MB`**.  
*Cleanup applied on BBB:* Removed factory Node-RED/Cloud9/cache (`~424 MB`) + 593 pre-Nov-2026 survey CSVs (`89.62 MB`), freeing **`~514 MB`** total on BBB eMMC.

---

## 4. Display Freeze Root Cause Analysis & Fixes (Issues #1–#5)

Live telemetry proved the freezes were caused by **five distinct hardware, scheduling, offload, GUI, and upload issues**:

### Issue 1: Shared USB Hub — Touchscreen + LTE Modem (PENDING HARDWARE FIX)
- **USB Topology (`lsusb -t`):** Both the Touchscreen (`usbhid`, `12M`, Hub Port 1) and Quectel LTE Modem (`cdc_ether`, `480M`, Hub Port 2) share `Dev 2` (4-port hub) on the BBB's single USB host controller (`musb-hdrc/1p`).
- **Root Cause:** Peak LTE cellular TX current bursts or RF EMI on that shared hub cause USB `5V` brownouts or hub resets, dropping touchscreen HID input while the GUI continues running.
- **Planned Hardware Fix:** Separate touchscreen and LTE modem USB/power paths (dedicated regulated `5V` + bulk capacitance for LTE modem).

### Issue 2: High-CPU Software Drop Shadows on Metric Cards (FIXED & DEPLOYED)
- **Root Cause:** `MetricCard.__init__` in `railgui25.py` attached `QGraphicsDropShadowEffect(blurRadius=16)` to each of the 4 metric cards (`Gauge`, `Cross Level`, `Twist`, `Distance`). Under CPU software rasterization (`QT_XCB_GL_INTEGRATION="none"`, `LIBGL_ALWAYS_SOFTWARE="1"`), every update triggered expensive Gaussian blurs.
- **Fix:** Removed `QGraphicsDropShadowEffect` and kept clean CSS borders (`optimized_metric_refresh()`).

### Issue 3: Real-Time CPU Starvation (`SCHED_FIFO 98`) & EMI Edge Storms (FIXED & DEPLOYED)
- **Root Cause (`sensor_board/src/sensor_service.c`):** Running `main()` and `encoder_gpio_worker()` under `SCHED_FIFO 98` / `SCHED_RR 80` on a single-core CPU starved `Xorg` and Qt whenever EMI bursts hit encoder GPIO pins.
- **Fix:** Switched both to normal scheduling (`SCHED_OTHER`, process priority `-5`), added `25 µs` glitch filtering (`ENCODER_MIN_EDGE_US_DEFAULT`), `64`-event batching, and a `2 ms` `nanosleep` storm yield when `>= 256` edges occur in `10 ms`.

### Issue 4: PRU1 ~82–87 s Cold-Boot Delay & GPIO Fallback (FIXED & DEPLOYED)
- **Root Cause:** On cold boot, `pru_rproc` took ~82–87 s to initialize, causing the app to fall back to CPU-heavy Linux GPIO interrupts.
- **Fix:** `setup.sh` configures `/etc/modules-load.d/rail-pru.conf` (`pruss_soc_bus`, `pru_rproc`); `setup_encoder_pru.sh` waits up to `30 s` (`60 x 0.5 s`) for `4a338000.pru` (`state = running`) before starting `sensor_service`; verified `remoteproc2` active in `4X` quadrature mode.

### Issue 5: Synchronous USB Export Blocking & Cloud `413` Queue Jam (BBB FIXED; SERVER PENDING)
- **Part A (USB / Close Blocking — FIXED & DEPLOYED):** Moved all automatic (STOP button) and manual CSV->USB exports onto background threads (`UsbExportThread` in `launch_railgui25_backend.py`, `_UsbExportWorker` + `_run_usb_export_async()` in `railgui25.py`), preventing `180 s` UI freezes, and cut close wait from `45 s` to `5 s` cloud + `3 s` USB.
- **Part B (Cloud `HTTP 413` Queue Jam — BBB FIXED; CLOUD SERVER PENDING):** Compacted upload JSON (`separators=(",", ":")`), added `_is_payload_too_large()` fast-fail and `deferred_oversized` queue rotation in `_flush_cloud_queue()` so one large CSV cannot block subsequent uploads, and updated `cloud/app.py` (`MAX_CONTENT_LENGTH = 50 MB`, `gzip`).
  - **Pending on Cloud Server `20.8.1.15:5173`:** Set Nginx `client_max_body_size 50M;` + `sudo nginx -t && sudo systemctl reload nginx`, and set Express `express.json({ limit: "50mb" })` + `express.urlencoded({ limit: "50mb", extended: true })`.

---

## 5. Additional System Enhancements Implemented (Nov 12, 2026)

1. **Daly BMS + Raspberry Pi Pico WH Battery Monitor & BBB UART5 Integration:**
   - Rewrote `tools/daly_bms.py` to support both **Raspberry Pi Pico WH (`DATA,...` @ `115200` baud)** and **direct Daly Smart BMS V1.2 (`0x90`–`0x98` @ `9600` baud)** over UART5 (`/dev/ttyS5`, BBB pins **`P8_37` TXD / `P8_38` RXD**) without disabling HDMI (custom `/lib/firmware/BB-NHDMI-NO-UART5-00A0.dtbo` + `BB-UART5-00A0.dtbo` in `/boot/uEnv.txt`).
   - Logs all 41 parameters per cycle to `/home/debian/DALY_BMS_Logs/DALY_BMS_*.csv`, atomically publishes `/tmp/battery_status.json`, and displays live `SOC%` / faults / cell voltages in `TopBar` + touchscreen `BatteryStatusDialog`.
2. **Dashboard MetricCard Digit Sizing Fix:**
   - Reduced base value font from `84 pt` to `56 pt` with dynamic length-aware scaling (`48 pt` at `>= 6` chars, `40 pt` at `>= 8` chars) in `MetricCard` (`railgui25.py` & `launch_railgui25_backend.py`) so 4+ digit values (e.g., `1676.00`, `185.00`) never clip on the `1024x768` display.
3. **80% BBB Storage Warning Popup & Touchscreen CSV Deletion:**
   - Added `get_bbb_storage_usage()`, `StorageWarningDialog` (`>= 80%` warning, `>= 90%` critical, anti-spam tier state, screen wake), and a **DELETE SELECTED** workflow inside `CSVViewerPage` (with active-survey protection, size confirmation, and live BBB free-space status bar) so operators can free eMMC space directly from the trolley screen.
   - Cleaned 593 pre-November-2026 survey CSVs from `/home/debian/surveys` (`89.62 MB` freed), preserving all 165 CSVs from November 2026 onward (`70 MB`, `/dev/mmcblk1p1` now **62% used / 1.3 GB free**).

---

## 6. Summary Status Matrix

| # | Item | Area | Status |
|---|---|---|---|
| **1** | **Touchscreen + LTE modem on shared USB hub** | Hardware / Power | **Pending hardware separation / dedicated 5V** |
| 2 | `QGraphicsDropShadowEffect` CPU blur | UI Rendering | **Fixed & Deployed on BBB** |
| 3 | `SCHED_FIFO 98` starvation & EMI storms | OS Scheduling / C Daemon | **Fixed, Rebuilt & Verified (`SCHED_OTHER`)** |
| 4 | PRU1 boot delay -> GPIO CPU fallback | Kernel / PRU1 Firmware | **Fixed & Verified (`remoteproc2` running)** |
| 5a | Blocking `180 s` USB export & `45 s` close | PyQt5 GUI Threading | **Fixed & Deployed on BBB** |
| 5b | `HTTP 413` queue block & cloud limits | BBB Queue / Cloud Proxy | **BBB queue fixed; Cloud Nginx/Express `50M` pending** |
| 6 | Daly BMS UART5 (`P8_37`/`P8_38`) + GUI | Battery Telemetry | **Implemented, Live on BBB (`/dev/ttyS5`)** |
| 7 | `MetricCard` digit clipping (`185.00`, etc.) | Dashboard Display | **Fixed (`56/48/40 pt`) & Deployed** |
| 8 | 80% BBB storage alert & CSV deletion UI | eMMC Disk Management | **Fixed, Tested, Deployed & Pre-Nov CSVs Cleaned (`62%` used)** |
