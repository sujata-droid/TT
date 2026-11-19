#!/usr/bin/env python3
"""Push inclinometer_degree_test.py to BeagleBone Black and apply 3-digit format."""
import paramiko
import os
import sys

HOST     = "192.168.7.2"
USER     = "debian"
PASSWORD = "temppwd"
LOCAL    = os.path.join(os.path.dirname(__file__), "sensors", "inclinometer_degree_test.py")
REMOTE   = "/home/debian/trolley/tools/sensors/inclinometer_degree_test.py"

print(f"Connecting to {USER}@{HOST} ...")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASSWORD, timeout=10)
print("Connected.")

# Ensure remote directory exists
stdin, stdout, stderr = ssh.exec_command("mkdir -p /home/debian/trolley/tools/sensors")
stdout.channel.recv_exit_status()

# Upload file via SFTP
sftp = ssh.open_sftp()
sftp.put(LOCAL, REMOTE)
sftp.close()
print(f"Uploaded: {LOCAL} -> {REMOTE}")

# Verify 3-digit format is in the file
stdin, stdout, stderr = ssh.exec_command(
    "grep 'deg:+12.3f' /home/debian/trolley/tools/sensors/inclinometer_degree_test.py"
)
result = stdout.read().decode().strip()
if result:
    print(f"✅ Format verified on BBB: {result.strip()}")
else:
    # Apply sed as fallback
    print("Applying sed fix on BBB ...")
    cmd = r"sed -i 's/{deg:10\.2f}/{deg:+12.3f}/g; s/{cl_mm:9\.2f}/{cl_mm:+9.3f}/g' " + REMOTE
    stdin, stdout, stderr = ssh.exec_command(cmd)
    stdout.channel.recv_exit_status()
    print("sed applied.")

print("\nDone! Now run on BBB:")
print("  sudo python3 ~/trolley/tools/sensors/inclinometer_degree_test.py --live --axis X")
ssh.close()

