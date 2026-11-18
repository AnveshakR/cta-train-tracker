#!/usr/bin/env python3
"""Push a file to the ESP32 via raw REPL with SHA-256 read-back verification.
Usage: python3.12 push_verified.py <local_file> <remote_name>
"""
import serial, time, sys, hashlib

PORT = '/dev/ttyUSB0'

def enter_raw_repl(s, tries=5):
    for t in range(tries):
        s.write(b'\r\x03\x03'); time.sleep(1.2); s.flushInput()
        s.write(b'\x01')
        buf = b''; end = time.time() + 6
        while time.time() < end:
            if s.in_waiting: buf += s.read(s.in_waiting)
            if b'raw REPL' in buf: return True
            time.sleep(0.03)
        time.sleep(1.0)
    return False

def raw_cmd(s, cmd, marker, timeout=15):
    s.write(cmd.encode()); s.write(b'\x04')
    buf = b''; end = time.time() + timeout
    while time.time() < end:
        if s.in_waiting: buf += s.read(s.in_waiting)
        if marker.encode() in buf: return buf
        time.sleep(0.03)
    return buf

def board_sha256(s, remote):
    out = raw_cmd(s, 'import hashlib,binascii; print("SHA", binascii.hexlify(hashlib.sha256(open("%s","rb").read()).digest()).decode())' % remote, "SHA", 10)
    import re
    m = re.search(r'SHA ([0-9a-f]{64})', out.decode('utf-8', 'replace'))
    return m.group(1) if m else None

def main():
    local, remote = sys.argv[1], sys.argv[2]
    src = open(local, 'rb').read()
    local_sha = hashlib.sha256(src).hexdigest()
    s = serial.Serial(PORT, 115200, timeout=1)
    s.dtr = False; s.rts = False; time.sleep(3.0)
    if not enter_raw_repl(s):
        print("FAILED raw REPL"); s.close(); return 1
    print("[ok] raw REPL")
    for attempt in range(1, 5):
        raw_cmd(s, 'open("%s","w").write(%r)' % (remote, src.decode()), 'OK', 20)
        got = board_sha256(s, remote)
        if got == local_sha:
            print("[ok] %s verified sha256=%s (attempt %d)" % (remote, got, attempt))
            break
        print("[warn] %s mismatch attempt %d: board=%s local=%s" % (remote, attempt, got, local_sha))
    else:
        print("[FAIL] %s could not be verified" % remote); s.close(); return 1
    s.write(b'\x02'); time.sleep(0.5); s.flushInput()
    s.close()
    print("[done] %s" % remote)
    return 0

if __name__ == '__main__':
    sys.exit(main())
