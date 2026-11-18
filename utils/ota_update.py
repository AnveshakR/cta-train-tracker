#!/usr/bin/env python3
"""OTA-update the ESP32 LED show (no USB needed).

Usage:
  python3 utils/ota_update.py <ip> <show.py>   Push a new show.py to the ESP32.
  python3 utils/ota_update.py <ip>             Show the ESP32's status page.
  python3 utils/ota_update.py scan             Scan the local network for the ESP32.

The ESP32 runs an HTTP server on port 80. POST the new show.py source to
http://<ip>/update and it syntax-checks, writes the file, and live-reloads it.
"""
import sys
import socket
import urllib.request
import urllib.error


def http_get(url, timeout=6):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read().decode('utf-8', 'replace')


def http_post(url, data, timeout=20):
    req = urllib.request.Request(url, data=data, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')


def scan():
    import concurrent.futures

    def check(ip):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.3)
            s.connect((ip, 80))
            s.close()
            return ip
        except Exception:
            return None

    candidates = []
    for net in ('192.168.0', '192.168.1', '192.168.4', '10.0.0'):
        for h in range(1, 255):
            candidates.append('%s.%d' % (net, h))
    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=200) as ex:
        for ip in ex.map(check, candidates):
            if ip:
                found.append(ip)
    return found


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    arg1 = sys.argv[1]
    if arg1 == 'scan':
        print('Scanning local network for the ESP32 (port 80)...')
        found = scan()
        if found:
            print('Found:', ', '.join(found))
        else:
            print('Not found. Is the ESP32 on the same network as this PC?')
        return 0
    ip = arg1
    if len(sys.argv) >= 3:
        path = sys.argv[2]
        with open(path, 'rb') as f:
            data = f.read()
        print('POSTing %d bytes of %s to http://%s/update ...' % (len(data), path, ip))
        code, body = http_post('http://%s/update' % ip, data)
        print('HTTP %d' % code)
        print(body)
        if code == 200:
            print('Update accepted. ESP32 is rebooting to run the new code.')
    else:
        code, body = http_get('http://%s/' % ip)
        print('HTTP %d' % code)
        print(body)
    return 0


if __name__ == '__main__':
    sys.exit(main())
