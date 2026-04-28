#!/usr/bin/env python3
"""Small SSH/SFTP helper for deploying and checking the BeagleBone target."""

import argparse
import os
import posixpath
import stat
import sys
from pathlib import Path

import paramiko


HOST = os.environ.get("BBB_HOST", "192.168.7.2")
USER = os.environ.get("BBB_USER", "debian")
PASSWORD = os.environ.get("BBB_PASSWORD")
REMOTE_ROOT = os.environ.get(
    "BBB_REMOTE_ROOT", "/home/debian/trolley"
)
REMOTE_STAGE = os.environ.get("BBB_REMOTE_STAGE", "/tmp/rail_inspection_stage")
DEPLOY_ITEMS = [
    "setup.sh",
    "configure_autologin_startx.sh",
    "push_latest_csv.sh",
    "railgui25.py",
    "run_encoder_console.sh",
    "run_railgui25.sh",
    "run_railgui25_diag.sh",
    "setup_encoder_eqep.sh",
    "setup_encoder_pru.sh",
    "start_gui_session.sh",
    "bbb_runtime",
    "pru",
    "sensor_board",
    "tools/encoder_console_test.py",
    "tools/encoder_eqep_console_test.py",
]


def safe_print(text, stream=sys.stdout):
    stream.write(text.encode("ascii", "replace").decode("ascii"))
    if not text.endswith("\n"):
        stream.write("\n")


def connect():
    if not PASSWORD:
        raise SystemExit("Set BBB_PASSWORD before running this helper.")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        username=USER,
        password=PASSWORD,
        timeout=10,
        banner_timeout=10,
        auth_timeout=10,
    )
    return client


def run(client, command, timeout=120):
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    code = stdout.channel.recv_exit_status()
    print(f"### {command}")
    if out:
        safe_print(out)
    if err:
        safe_print(err, stream=sys.stderr)
    print(f"### exit={code}")
    return code


def sudo_run(client, command, timeout=120):
    stdin, stdout, stderr = client.exec_command(
        f"sudo -S -p '' sh -c {shell_quote(command)}", timeout=timeout
    )
    stdin.write(PASSWORD + "\n")
    stdin.flush()
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    code = stdout.channel.recv_exit_status()
    print(f"### sudo {command}")
    if out:
        safe_print(out)
    if err:
        safe_print(err, stream=sys.stderr)
    print(f"### exit={code}")
    return code


def shell_quote(value):
    return "'" + value.replace("'", "'\"'\"'") + "'"


def sftp_mkdirs(sftp, remote_dir):
    parts = Path(remote_dir).parts
    current = ""
    for part in parts:
        if part == "/":
            current = "/"
            continue
        current = os.path.join(current, part).replace("\\", "/")
        try:
            sftp.stat(current)
        except IOError:
            sftp.mkdir(current)


def upload_tree(client, local_root, remote_root):
    sftp = client.open_sftp()
    local_root = Path(local_root).resolve()
    for path in local_root.rglob("*"):
        rel = path.relative_to(local_root).as_posix()
        remote = f"{remote_root}/{rel}"
        if path.is_dir():
            sftp_mkdirs(sftp, remote)
            continue
        sftp_mkdirs(sftp, os.path.dirname(remote))
        sftp.put(str(path), remote)
        mode = path.stat().st_mode
        if mode & stat.S_IXUSR:
            sftp.chmod(remote, 0o755)
    sftp.close()


def upload_project(client, local_root, remote_root):
    sftp = client.open_sftp()
    local_root = Path(local_root).resolve()
    for item in DEPLOY_ITEMS:
        path = local_root / item
        if path.is_dir():
            for child in path.rglob("*"):
                if "__pycache__" in child.parts:
                    continue
                rel = child.relative_to(local_root).as_posix()
                remote = f"{remote_root}/{rel}"
                if child.is_dir():
                    sftp_mkdirs(sftp, remote)
                    continue
                sftp_mkdirs(sftp, os.path.dirname(remote))
                sftp.put(str(child), remote)
                if child.stat().st_mode & stat.S_IXUSR:
                    sftp.chmod(remote, 0o755)
        elif path.is_file():
            remote = f"{remote_root}/{item}"
            sftp_mkdirs(sftp, os.path.dirname(remote))
            sftp.put(str(path), remote)
            if path.stat().st_mode & stat.S_IXUSR:
                sftp.chmod(remote, 0o755)
    sftp.close()


def inspect(args):
    client = connect()
    commands = [
        "pwd",
        f"ls -la {REMOTE_ROOT} || true",
        f"find {REMOTE_ROOT} -maxdepth 2 -type f -printf '%p\\n' 2>/dev/null | sort | head -100",
        "ps -ef | grep -E 'sensor_service|main_ui.py|main.py|pppd' | grep -v grep || true",
        "ls -l /dev/spidev* 2>/dev/null || true",
        "ls -l /tmp/rail_sensor.sock 2>/dev/null || true",
    ]
    for command in commands:
        run(client, command, timeout=30)
    client.close()


def deploy(args):
    client = connect()
    run(client, f"rm -rf {shell_quote(REMOTE_STAGE)} && mkdir -p {shell_quote(REMOTE_STAGE)}", timeout=30)
    upload_project(client, Path(__file__).resolve().parents[1], REMOTE_STAGE)
    sudo_run(client, "pkill -x sensor_service || true; pkill -f '[p]ython3 .*main.py' || true; pkill -f '[p]ython3 .*main_ui.py' || true", timeout=30)
    sudo_run(client, f"rm -rf {shell_quote(REMOTE_ROOT)}", timeout=30)
    sudo_run(client, f"mkdir -p {shell_quote(posixpath.dirname(REMOTE_ROOT))}", timeout=30)
    sudo_run(client, f"cp -a {shell_quote(REMOTE_STAGE)} {shell_quote(REMOTE_ROOT)}", timeout=60)
    sudo_run(client, f"chown -R {USER}:{USER} {shell_quote(REMOTE_ROOT)}", timeout=60)
    run(client, f"find {shell_quote(REMOTE_ROOT)} -type f -name '*.sh' -exec sed -i 's/\\r$//' {{}} +", timeout=60)
    run(client, f"chmod +x {shell_quote(REMOTE_ROOT + '/setup.sh')}", timeout=30)
    run(client, f"chmod +x {shell_quote(REMOTE_ROOT + '/run_railgui25.sh')}", timeout=30)
    run(client, f"chmod +x {shell_quote(REMOTE_ROOT + '/run_railgui25_diag.sh')}", timeout=30)
    run(client, f"chmod +x {shell_quote(REMOTE_ROOT + '/run_encoder_console.sh')}", timeout=30)
    run(client, f"chmod +x {shell_quote(REMOTE_ROOT + '/push_latest_csv.sh')}", timeout=30)
    run(client, f"chmod +x {shell_quote(REMOTE_ROOT + '/bbb_runtime/run_railgui25_backend.sh')}", timeout=30)
    run(client, f"chmod +x {shell_quote(REMOTE_ROOT + '/configure_autologin_startx.sh')}", timeout=30)
    run(client, f"chmod +x {shell_quote(REMOTE_ROOT + '/start_gui_session.sh')}", timeout=30)
    run(client, f"chmod +x {shell_quote(REMOTE_ROOT + '/setup_encoder_eqep.sh')}", timeout=30)
    run(client, f"chmod +x {shell_quote(REMOTE_ROOT + '/setup_encoder_pru.sh')}", timeout=30)
    run(client, f"python3 -m py_compile {shell_quote(REMOTE_ROOT + '/bbb_runtime/backend_bridge.py')}", timeout=30)
    run(client, f"python3 -m py_compile {shell_quote(REMOTE_ROOT + '/bbb_runtime/launch_railgui25_backend.py')}", timeout=30)
    run(client, f"python3 -m py_compile {shell_quote(REMOTE_ROOT + '/railgui25.py')}", timeout=30)
    run(client, f"python3 -m py_compile {shell_quote(REMOTE_ROOT + '/tools/encoder_console_test.py')}", timeout=30)
    run(client, f"python3 -m py_compile {shell_quote(REMOTE_ROOT + '/tools/encoder_eqep_console_test.py')}", timeout=30)
    run(client, f"cd {shell_quote(REMOTE_ROOT + '/sensor_board')} && make clean && make", timeout=120)
    run(client, f"find {shell_quote(REMOTE_ROOT)} -type d -name __pycache__ -prune -exec rm -rf {{}} +", timeout=60)
    client.close()


def check(args):
    client = connect()
    commands = [
        f"python3 -m py_compile {REMOTE_ROOT}/railgui25.py",
        f"python3 -m py_compile {REMOTE_ROOT}/bbb_runtime/launch_railgui25_backend.py",
        f"python3 -m py_compile {REMOTE_ROOT}/tools/encoder_console_test.py",
        f"cd {REMOTE_ROOT}/sensor_board && make",
        f"test -x {REMOTE_ROOT}/sensor_board/sensor_service && echo sensor_service_ok",
        "python3 - <<'PY'\nimport PyQt5\nprint('pyqt5_ok')\nPY",
    ]
    for command in commands:
        run(client, command, timeout=120)
    client.close()


def sensor_test(args):
    client = connect()
    sudo_run(
        client,
        f"cd {shell_quote(REMOTE_ROOT)} && timeout {int(args.seconds)}s ./sensor_board/sensor_service",
        timeout=int(args.seconds) + 20,
    )
    client.close()


def frame_test(args):
    client = connect()
    command = f"""cd {shell_quote(REMOTE_ROOT)}
./sensor_board/sensor_service >/tmp/rail_sensor_service.log 2>&1 &
pid=$!
sleep 3
python3 - <<'PY'
import mmap
import struct
import time
s = struct.Struct('<IIIIqddddiBBBB')
with open('/dev/shm/rail_sensor_shm', 'rb') as f:
    m = mmap.mmap(f.fileno(), s.size, access=mmap.ACCESS_READ)
    for i in range(5):
        v = s.unpack(m[:s.size])
        print('frame%d magic=%08x updates=%d cl=%.3f tw=%.3f ch=%.3f enc=%d svc=%d' %
              (i, v[0], v[3], v[5], v[6], v[7], v[11], v[12]))
        time.sleep(0.3)
PY
kill $pid 2>/dev/null || true
wait $pid 2>/dev/null || true
cat /tmp/rail_sensor_service.log
"""
    sudo_run(client, command, timeout=40)
    client.close()


def csv_test(args):
    client = connect()
    command = f"""cd {shell_quote(REMOTE_ROOT)}
rm -rf /tmp/rail_csv_test
python3 - <<'PY'
import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'bbb_runtime')
import railgui25 as app
logger = app.CSVLogger()
logger.set_reference('TEST', 'DIAG')
logger.start('/tmp/rail_csv_test')
for i in range(5):
    logger.write({{'lat': 0, 'lon': 0, 'cross': 1.25 + i, 'twist': 0.5, 'dist': 0.0}})
path = logger.stop()
print(path)
with open(path) as f:
    print(f.read())
PY
"""
    run(client, command, timeout=40)
    client.close()


def configure_lte(args):
    client = connect()
    sudo_run(
        client,
        f"cd {shell_quote(REMOTE_ROOT)} && APN={shell_quote(args.apn)} sh tools/configure_airtel_lte.sh",
        timeout=120,
    )
    client.close()


def configure_console(args):
    client = connect()
    sudo_run(
        client,
        f"cd {shell_quote(REMOTE_ROOT)} && bash configure_autologin_startx.sh {shell_quote(args.user)} {shell_quote(args.mode)}",
        timeout=120,
    )
    client.close()


def remote_cmd(args):
    client = connect()
    code = sudo_run(client, args.command, timeout=args.timeout) if args.sudo else run(
        client, args.command, timeout=args.timeout
    )
    client.close()
    raise SystemExit(code if code >= 0 else 1)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inspect").set_defaults(func=inspect)
    sub.add_parser("deploy").set_defaults(func=deploy)
    sub.add_parser("check").set_defaults(func=check)
    sensor = sub.add_parser("sensor-test")
    sensor.add_argument("--seconds", type=int, default=8)
    sensor.set_defaults(func=sensor_test)
    sub.add_parser("frame-test").set_defaults(func=frame_test)
    sub.add_parser("csv-test").set_defaults(func=csv_test)
    lte = sub.add_parser("configure-lte")
    lte.add_argument("--apn", default="airtelgprs.com")
    lte.set_defaults(func=configure_lte)
    console = sub.add_parser("configure-console")
    console.add_argument("--user", default=USER)
    console.add_argument("--mode", choices=["console", "app"], default="console")
    console.set_defaults(func=configure_console)
    remote = sub.add_parser("remote")
    remote.add_argument("--sudo", action="store_true")
    remote.add_argument("--timeout", type=int, default=120)
    remote.add_argument("command")
    remote.set_defaults(func=remote_cmd)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
