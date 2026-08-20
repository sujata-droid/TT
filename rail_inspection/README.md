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

## Sensor Test Tools

- `tools/sensors/inclinometer_monitor.py` - continuously watches the same
  cross-level frame used by the UI. Use this first when the UI display looks
  frozen.
- `tools/sensors/scl3300_axes_test.py` - direct X/Y/Z inclinometer test used
  to identify which mounted axis should drive cross-level.
- `tools/encoder_console_test.py` - PRU encoder console test.
- `tools/encoder_eqep_console_test.py` - eQEP encoder console test path.

## Normal BeagleBone Run

Terminal 1:

```bash
cd /home/debian/trolley
sudo pkill -9 sensor_service
sudo pkill -9 python3
sudo rm -f /dev/shm/rail_sensor_shm
sudo bash setup_encoder_pru.sh
sudo env RAIL_SCL_AXIS=X RAIL_ENCODER_PPR=400 RAIL_WHEEL_DIAMETER_MM=250 RAIL_ENCODER_INVERT=0 RAIL_SAMPLING_DISTANCE_M=0.25 RAIL_TWIST_BASE_M=3.0 ./sensor_board/sensor_service
```

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
https://render-cloud-api.onrender.com/api/survey
```

The UI uploads the completed CSV when the session is stopped. Manual upload:

```bash
cd /home/debian/trolley
bash push_latest_csv.sh /root/surveys
```

Deployment files are included for Render:

- `render.yaml` - Blueprint for the Flask cloud service.
- `cloud/Procfile` - fallback start command for manual web-service setup.
- `cloud/README.md` - setup and upload instructions.
