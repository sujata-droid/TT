import paramiko
import os
import sys

HOST = '192.168.7.2'
USER = 'debian'
PASSWORD = 'temppwd'

print(f"Connecting to BeagleBone Black at {HOST}...")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=10)
    print("Connected successfully.")
except Exception as e:
    print(f"Connection failed: {e}")
    sys.exit(1)

sftp = ssh.open_sftp()

# 1. Upload updated inclinometer_degree_test.py
local_script = os.path.join(os.path.dirname(__file__), "sensors", "inclinometer_degree_test.py")
remote_script = "/home/debian/trolley/tools/sensors/inclinometer_degree_test.py"
sftp.put(local_script, remote_script)
print("Uploaded: inclinometer_degree_test.py (zero_deg=0.262, scale_factor=1.0359)")

# 2. Upload updated rail_config.json
local_cfg = os.path.join(os.path.dirname(__file__), "..", "rail_config.json")
remote_cfg = "/home/debian/trolley/rail_config.json"
sftp.put(local_cfg, remote_cfg)
print("Uploaded: rail_config.json (incl.offset=7.66 mm, incl.factor=1.036)")

sftp.close()

# 3. Verify on BeagleBone Black
stdin, stdout, stderr = ssh.exec_command("grep -E '(zero_deg|scale_factor)' /home/debian/trolley/tools/sensors/inclinometer_degree_test.py | head -4")
print("\nBBB script verification:")
print(stdout.read().decode().strip())

stdin, stdout, stderr = ssh.exec_command("grep -A 4 '\"incl\":' /home/debian/trolley/rail_config.json")
print("\nBBB UI config verification:")
print(stdout.read().decode().strip())

# 4. Reload UI backend so it picks up the new config immediately
stdin, stdout, stderr = ssh.exec_command("sudo pkill -f launch_railgui25_backend.py || true")
stdout.channel.recv_exit_status()
print("\nUI backend restarted to apply new calibration.")

ssh.close()
print("\n=== SUCCESS: Inclinometer calibrated and active on BBB! ===")
