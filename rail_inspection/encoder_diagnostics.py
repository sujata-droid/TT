#!/usr/bin/env python3
"""Live PRU encoder diagnostics. Run with sudo.
Press Enter to zero the readings (do this before each test turn)."""
import mmap, os, select, struct, sys, time

fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
mm = mmap.mmap(fd, 0x2000, mmap.MAP_SHARED, mmap.PROT_READ, offset=0x4A300000)

def snap():
    # offsets 0x00..0x18: count, status, sample_us, cookie, a_edges, b_edges, invalid
    cnt, status, _dbg, _rsvd, a, b, inv = struct.unpack_from("<iIIIIII", mm, 0)
    return cnt, a, b, inv

base = snap()
print("Press Enter to zero. Ctrl-C to quit.")
print("1 turn of a 400 PPR encoder should give: count~1600  A~800  B~800  invalid=0")
try:
    while True:
        cnt, a, b, inv = snap()
        print(f"count={cnt-base[0]:7d}  A_edges={a-base[1]:6d}  "
              f"B_edges={b-base[2]:6d}  invalid={inv-base[3]:5d}", end="\r", flush=True)
        if select.select([sys.stdin], [], [], 0.5)[0]:
            sys.stdin.readline()
            base = snap()
            print()
except KeyboardInterrupt:
    print()
