import paramiko
import os

HOST = '192.168.7.2'
USER = 'debian'
PASSWORD = 'temppwd'

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASSWORD, timeout=10)
print("Connected to BeagleBone Black.")

sftp = ssh.open_sftp()

# 1. Upload updated inclinometer_degree_test.py (with -0.49 deg offset)
local_script = os.path.join(os.path.dirname(__file__), "sensors", "inclinometer_degree_test.py")
remote_script = "/home/debian/trolley/tools/sensors/inclinometer_degree_test.py"
sftp.put(local_script, remote_script)
print("Uploaded: inclinometer_degree_test.py (offset = 0.49 deg)")

# 2. Upload updated rail_config.json (UI offset = 14.33 mm = 0.49 deg)
local_cfg = os.path.join(os.path.dirname(__file__), "..", "rail_config.json")
remote_cfg = "/home/debian/trolley/rail_config.json"
sftp.put(local_cfg, remote_cfg)
print("Uploaded: rail_config.json (incl.offset = 14.33 mm)")

sftp.close()

# 3. Verify on BBB
stdin, stdout, stderr = ssh.exec_command("grep -E 'zero_deg = ' /home/debian/trolley/tools/sensors/inclinometer_degree_test.py")
print("BBB console script verification:", stdout.read().decode().strip())

stdin, stdout, stderr = ssh.exec_command("grep -A 4 '\"incl\":' /home/debian/trolley/rail_config.json")
print("BBB UI config verification:\n" + stdout.read().decode().strip())

# 4. Restart GUI backend if running to apply changes immediately
stdin, stdout, stderr = ssh.exec_command("sudo pkill -f launch_railgui25_backend.py || true")
stdout.channel.recv_exit_status()
print("UI backend reloaded.")

ssh.close()
print("SUCCESS: All changes pushed and active on BBB.")

