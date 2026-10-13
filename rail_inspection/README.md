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

If the inclinometer is healthy but cross-level does not change when the board is
tilted, run the all-axis test:

```bash
cd /home/debian/trolley
sudo pkill -9 sensor_service
sudo python3 tools/sensors/scl3300_axes_test.py --seconds 30 --interval 0.25
```

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

The UI uploads the completed CSV when the session is stopped. Manual upload:

```bash
cd /home/debian/trolley
bash push_latest_csv.sh /root/surveys
```

Frontend dashboard:

```text
https://lwtmt-cloud-frontend.onrender.com
```

Deployment files are included for Render:

- `render.yaml` - Blueprint for the Flask cloud service.
- `cloud/Procfile` - fallback start command for manual web-service setup.
- `cloud/README.md` - setup and upload instructions.
