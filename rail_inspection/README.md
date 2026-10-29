# Rail Inspection Runtime

This folder contains the BeagleBone Black runtime for the LWTMT rail inspection
GUI, sensor service, PRU encoder firmware, and sensor test utilities.

## Main Runtime

- `railgui25.py` - main PyQt GUI.
- `bbb_runtime/` - BeagleBone wrapper that connects the GUI to shared memory,
  CSV logging, and cloud upload.
- `sensor_board/` - C service that reads SCL3300, track-gauge ADC, and PRU
  encoder, then publishes one shared-memory frame for the UI.
- `pru/` - PRU0 quadrature encoder firmware.
- `cloud/` - Render Flask dashboard and `/api/survey` upload endpoint.

### Calibration password

Calibration is protected by a password-only prompt (there is no username).
Set the password before launching the GUI on the BeagleBone:

```bash
export RAIL_CALIBRATION_PASSWORD='choose-a-strong-password'
```

The password is case-sensitive. The touchscreen keyboard starts in lowercase;
tap **SHIFT** to enter uppercase characters. For persistent configuration on
the BBB, `calibration_password` may instead be set in its local
`rail_config.json` file. Do not commit that file or the password to source
control.

The dialog always provides a **Back** button so an operator without the
password can return to the dashboard.

### Encoder distance calibration

The encoder calibration panel sends raw-count readings and calibration requests
to the shared-memory backend; the panel displays the backend's count, distance,
and result. The backend and `tools/encoder_console_test.py` share the same
encoder distance and factor-calculation helpers. The backend uses the configured
wheel geometry (`RAIL_ENCODER_PPR` and `RAIL_WHEEL_DIAMETER_MM`) to calculate
one calibration factor; the factor is saved in `rail_config.json` and applied
to the complete raw-count distance, not compounded on previously scaled
distance.
Start a survey session before tapping **START RUN**, move over a measured
distance, then tap **END RUN** and enter that distance. Calibration runs must
contain at least 5,000 counts, and the calculated factor must be between 0.5
and 1.5. **ENTER FACTOR MANUALLY** and **RESET FACTOR TO 1.0000** are also
available.

## Sensor Test Tools

- `tools/sensors/inclinometer_monitor.py` - continuously watches the same
  cross-level frame used by the UI. Use this first when the UI display looks
  frozen.
- `tools/sensors/scl3300_axes_test.py` - direct X/Y/Z inclinometer test used
  to identify which mounted axis should drive cross-level.
- `tools/sensors/laser_gauge_adc_test.py` - live laser ADC monitor and resistor
  divider calibration utility. `--calibrate` samples a known Panasonic
  reference point and saves a validated factor to `laser_cal.json`; the live
  console monitor reloads that file while running. The production calibration
  panel uses the same calibration-file format and the sensor service hot-reloads
  the factor while running.
- `tools/encoder_console_test.py` - PRU encoder console test.
- `tools/encoder_eqep_console_test.py` - eQEP encoder console test path.

The PRU console tool can also calculate a calibration factor from raw counts
and a known distance without opening `/dev/mem`:

```bash
python3 tools/encoder_console_test.py --ppr 400 --wheel-diameter-mm 50 \
  --calibrate-counts 6000 --true-distance-m 0.6
```

This prints the factor but does not save it. The UI sends its measured-run or
manual factor to the live shared-memory backend, which validates and persists
it in `rail_config.json`.

To test a factor in the live console monitor without changing the UI's saved
configuration, pass it explicitly. For example, `--factor 1.5` applies a 1.5x
multiplier to the displayed distance; the default is `1.0`.

## Normal BeagleBone Run

Terminal 1:

```bash
cd /home/debian/trolley
sudo pkill -9 sensor_service
sudo pkill -9 python3
sudo rm -f /dev/shm/rail_sensor_shm
sudo bash setup_encoder_pru.sh
sudo env RAIL_SCL_AXIS=X RAIL_ENCODER_PPR=400 RAIL_WHEEL_DIAMETER_MM=50 RAIL_ENCODER_INVERT=0 RAIL_SAMPLING_DISTANCE_M=0.25 RAIL_TWIST_BASE_M=3.0 ./sensor_board/sensor_service
```

`RAIL_ENCODER_PPR=400` is 400 cycles per channel per encoder revolution.
The PRU uses 4X quadrature decoding, so the raw encoder count is exactly
1600 counts per revolution. With a 1:1 encoder-to-wheel drive, that is also
1600 counts per wheel revolution.

Terminal 2:

```bash
cd /home/debian/trolley
export DISPLAY=:0
sudo -E python3 /home/debian/trolley/bbb_runtime/launch_railgui25_backend.py
```

Terminal 3, optional inclinometer verification:

```bash
cd /home/debian/trolley
sudo python3 tools/sensors/inclinometer_monitor.py --interval 0.2
```

If `inclinometer_monitor.py` changes while the UI does not, the issue is in the
UI path. If the monitor is also constant, check the SCL3300 wiring and SPI logs.

### Inclination calibration

The SCL3300 is factory calibrated. The UI calibration compensates for
installation offset and system scale; it cannot establish sensor accuracy
without an independent cross-level reference.

The monitor and UI share `inclinometer_cal.json` for the calibration offset
and factor. The runtime UI loads that calibration at startup and also stores
the values in `bbb_runtime/rail_config.json` under `incl`. The CLI's default
`--config-file` points to this UI config file. Restart the UI after CLI
calibration so it reloads the shared calibration.

Perform calibration only on a secured, stationary test fixture or approved
protected track; never perform the reversal procedure on live track.

**Static known-reference calibration**

1. Keep the trolley stationary on a verified cross-level reference and ensure
   the sensor service is publishing healthy frames.
2. For a zero fixture, run:

   ```bash
   cd /home/debian/trolley
   sudo python3 tools/sensors/inclinometer_monitor.py --calibrate
   ```

   The command asks you to press Enter when the trolley is stationary and
   ready to sample.

   For a verified nonzero reference, specify its signed value, for example:

   ```bash
   sudo python3 tools/sensors/inclinometer_monitor.py --calibrate --target-ref-mm 3.5
   ```

   The CLI collects 100 distinct samples at 20 Hz by default, reports capture
   progress and statistics, and rejects a capture if peak-to-peak spread
   exceeds 1.0 mm. The zero offset is computed using the currently configured
   scale factor, so the calibrated output equals the specified target. Use
   `--cal-samples N` and `--cal-period SECONDS` to change the capture count and
   interval; the compatible alternatives are `--samples N` and `--rate HZ`.

**180-degree reversal**

1. Mark the trolley position and wheel contact reference points on a secured
   fixture with known track cant.
2. Run `sudo python3 tools/sensors/inclinometer_monitor.py --calibrate-reversal`
   from the project directory. Capture Position A when stationary, rotate the
   trolley 180 degrees while keeping the wheel contacts on the same marked
   reference points, then capture Position B.
3. The command estimates mounting zero as `(A + B) / 2` and track cant as
   `(A - B) / 2`. This isolates offset only; it does not independently
   establish scale accuracy. Both captures must pass the same health and
   1.0 mm spread checks.

To monitor raw and calibrated readings, angle, and sensor health:

```bash
sudo python3 tools/sensors/inclinometer_monitor.py
```

The monitor auto-applies `inclinometer_cal.json`; use `--no-cal` to display
uncorrected readings or `--manual-offset-mm VALUE` for a temporary offset.
It reports `FRAME_STALE` if the shared-memory update counter stops advancing
for the configured warning period (`--warn-freeze`, 2 seconds by default).
This checks for a stalled data feed; an unchanged physical reading by itself
is not treated as a fault.
Keep the sensor service running during CLI capture. If the runtime launcher is
providing the service, leave the UI running during capture, then restart the UI
after calibration. The UI's
**Sensor Calibration → Inclination** panel uses the same 100-sample, 20 Hz,
1.0 mm-spread rule: capture zero on a verified 0 mm fixture, capture a known
signed reference of 20–200 mm, then save. Tap the reference value to open its
keypad; use `+/-` to toggle the sign before tapping **DONE**.

Validate the saved calibration on independent zero and known positive and
negative references. Confirm `scl_ok=1`, `service_ok=1`, and stable readings.
The monitor angle is `asin(calibrated_mm / gauge_mm)`. Do not infer accuracy
from communication health or resolution alone.

The specification's ±1 mm static / ±1.5 mm dynamic accuracy must be validated
against an appropriate reference instrument. Software calibration alone does
not guarantee those tolerances.

If the inclinometer is healthy but cross-level does not change when the board is
tilted, run the all-axis test:

```bash
cd /home/debian/trolley
sudo pkill -9 sensor_service
sudo python3 tools/sensors/scl3300_axes_test.py --seconds 30 --interval 0.25
```

### Laser gauge calibration and live test

Run the normal runtime launcher so it exports the shared calibration-file path
and starts the C sensor service. In **Sensor Calibration → Track Gauge**:

1. Position the laser at the verified Panasonic `+80` point, tap **CAPTURE
   +80**, and wait for all 50 samples to finish.
2. Repeat at the verified `0` point using **CAPTURE 0**.
3. Repeat at the verified `-80` point using **CAPTURE -80**.
4. Check that all three buttons show **CAPTURED**, then press **SAVE 3-POINT
   CALIBRATION**. The UI requires the measured ADC means to decrease from +80
   through 0 to -80; it rejects unstable samples and an incorrect ordering.
5. Confirm the live predicted mapping is `+80 → 120 mm`, `0 → 200 mm`, and
   `-80 → 280 mm`.

Each point captures 50 raw ADC samples. The three measured means are stored as
the calibrated raw references in `/home/debian/trolley/laser_cal.json` by
default. The running C sensor service detects the file update and uses
piecewise-linear mapping between the three references without a restart.
Older calibration files containing only `cal_factor` remain supported until
a three-point calibration is saved. Removing the calibration file restores the
launcher-configured raw references.

Readings more than 1% of the +80 reference raw count outside the configured ADC
window return `0.0 mm`; readings within that small endpoint noise allowance are
clamped to 120 mm or 280 mm to avoid ADC jitter dropping the display to zero.
The ADC cannot report a value below zero, so if its output saturates at raw zero,
software cannot distinguish exactly -80 from a laser position farther beyond
that point.

The ADC cannot report values below zero; if it saturates at raw zero at the
`-80` point, software cannot distinguish exactly `-80` from a laser position
farther beyond that point.

Tilt the board in the physical direction that should change cross-level. The
column that changes the most is the axis to use in Terminal 1:

```bash
RAIL_SCL_AXIS=X   # or Y / Z
```

## Cloud

Default cloud endpoint:

```text
https://lwtmt-cloud-backend.onrender.com/api/survey
```

The UI uploads the completed CSV when the session is stopped and displays a
success or failure dialog naming that file. The current session is uploaded
before the app attempts one older queued CSV, so a large old backlog does not
block the current session from being sent first. Older queued files are
retried gradually as later sessions are uploaded. Check
`bbb_runtime/logs/cloud_status.json` for the latest upload state and
`bbb_runtime/logs/cloud_queue.json` for files still awaiting retry.

Manual upload:

```bash
cd /home/debian/trolley
bash push_latest_csv.sh /root/surveys
```

## USB / Pen Drive CSV Export

The **USB EXPORT** button in the CSV File Viewer copies all locally saved CSV
files to an inserted pen drive. It is independent of LTE/cloud connectivity and
never deletes or moves the BBB's original survey files.

1. Insert one FAT32 or exFAT pen drive and wait a few seconds for the BBB to
   mount it.
2. On the display, open the CSV File Viewer and press **USB EXPORT**.
3. Wait for the `SAFE TO REMOVE USB` confirmation before removing the drive.

Files are copied to `RAIL_SURVEYS/YYYY-MM-DD/` on the pen drive. Re-exporting
does not overwrite matching files; unchanged files are reported as already
present. If the display reports no mounted USB drive, remove/reinsert the drive
and ensure it is FAT32/exFAT formatted. Keep only one target pen drive attached
during export.

When an operator stops a survey, the display also asks whether to copy that
just-finished CSV to an inserted USB drive. Choosing **No** skips only the USB
copy; the local BBB copy remains available and the normal cloud upload proceeds.

Frontend dashboard:

```text
https://lwtmt-cloud-frontend.onrender.com
```

Deployment files are included for Render:

- `render.yaml` - Blueprint for the Flask cloud service.
- `cloud/Procfile` - fallback start command for manual web-service setup.
- `cloud/README.md` - setup and upload instructions.
