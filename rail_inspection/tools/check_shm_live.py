import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.7.2', username='debian', password='temppwd', timeout=5)

cmd = """python3 -c "
import struct, math, mmap
f = open('/dev/shm/rail_sensor_shm', 'rb')
mm = mmap.mmap(f.fileno(), 64, mmap.MAP_SHARED, mmap.PROT_READ)
v = struct.unpack('<IIIIqddddiBBBB', mm[:64])
mm.close()
f.close()
cl = v[5]
theta = math.degrees(math.asin(max(-1.0, min(1.0, cl / 1676.0))))
print('SHM live values:')
print(f'  cross_level_mm = {cl:+.4f} mm')
print(f'  theta_deg      = {theta:+.4f} deg')
"
"""
stdin, stdout, stderr = ssh.exec_command(cmd)
print(stdout.read().decode())
print(stderr.read().decode())
ssh.close()

