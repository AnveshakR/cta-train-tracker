# main.py -- WiFi + OTA HTTP server + drives the LED show (show.py)
#
# Endpoints:
#   GET  /         status
#   POST /update   body = new show.py source -> syntax-check, write, live-reload
#   POST /reboot   machine.reset()
import network
import time
import socket
import sys
import machine
from secrets import WIFI_SSID, WIFI_PASS

OTA_PORT = 80
WIFI_TIMEOUT_S = 45

# Power saving, set before WiFi starts: 80 MHz is the lowest clock WiFi supports,
# and the onboard blue LED (GPIO2) is held off.
machine.freq(80000000)
machine.Pin(2, machine.Pin.OUT, value=0)

show = None


def load_show():
    """(Re)load show.py from disk. Returns (ok, message)."""
    global show
    try:
        if 'show' in sys.modules:
            del sys.modules['show']
        import show
        show = sys.modules['show']
        return True, "show.py loaded (%d strips)" % len(show.STRIPS)
    except Exception as e:
        show = None
        return False, "show.py error: %s" % e


def connect_wifi(timeout_s=WIFI_TIMEOUT_S):
    w = network.WLAN(network.STA_IF)
    w.active(True)
    if not w.isconnected():
        print("Connecting to WiFi '%s' ..." % WIFI_SSID)
        w.connect(WIFI_SSID, WIFI_PASS)
        start = time.ticks_ms()
        while not w.isconnected():
            if time.ticks_diff(time.ticks_ms(), start) > timeout_s * 1000:
                print("WiFi connect timed out. Server is up; reachable once WiFi connects.")
                break
            time.sleep(1)
    ip = w.ifconfig()[0]
    print("WiFi IP:", ip)
    return ip


def _send_all(conn, data):
    """Send all bytes on a non-blocking socket (small payloads)."""
    view = memoryview(data)
    while view:
        try:
            n = conn.send(view)
        except OSError:
            n = 0
        if n > 0:
            view = view[n:]
        else:
            time.sleep_ms(5)


def _http_send(conn, code, text):
    body = text.encode('utf-8')
    reason = {200: 'OK', 404: 'Not Found', 500: 'Error'}.get(code, '')
    head = ("HTTP/1.0 %d %s\r\nContent-Type: text/plain\r\n"
            "Content-Length: %d\r\nConnection: close\r\n\r\n"
            % (code, reason, len(body)))
    try:
        _send_all(conn, head.encode())
        _send_all(conn, body)
    except Exception:
        pass


def status_text():
    if show is not None:
        try:
            strips = show.STRIPS
            nleds = show.LEDS_PER_STRIP
            step = show.STEP_MS
        except Exception:
            strips, nleds, step = [], 0, 0
        show_state = "loaded"
    else:
        strips, nleds, step = [], 0, 0
        show_state = "NOT LOADED (POST /update to fix)"
    return ("ESP32 LED OTA server\n"
            "Show: %s\n"
            "Strips: %d (pins %s)\n"
            "LEDs/strip: %d\n"
            "Step: %d ms\n"
            "POST /update with new show.py source; POST /reboot to reset.\n"
            % (show_state, len(strips), [p for _, p in strips], nleds, step))


def do_update(src):
    try:
        text = src.decode('utf-8')
        compile(text, 'show.py', 'exec')
        with open('show.py', 'w') as f:
            f.write(text)
    except SyntaxError as e:
        return False, "Syntax error in new code: %s" % e
    except Exception as e:
        return False, "Update failed: %s" % e
    # a show that fails to import leaves the board up; POST a fixed one
    ok, msg = load_show()
    if ok:
        return True, "show.py updated and loaded. %s" % msg
    return True, "show.py written but %s. Board still up; POST a fixed show.py." % msg


class OtaServer:
    """Minimal non-blocking HTTP server, one connection at a time.
    An idle timeout drops stalled/probe connections so the server can't wedge."""
    IDLE_MS = 8000

    def __init__(self, port):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.s.bind(('0.0.0.0', port))
        self.s.listen(3)
        self.s.settimeout(0)
        self.conn = None
        self.buf = b''
        self.clen = 0
        self.headers_done = False
        self.last_recv = 0

    def poll(self):
        if self.conn is None:
            try:
                conn, addr = self.s.accept()
            except OSError:
                return
            conn.settimeout(0)
            self.conn = conn
            self.buf = b''
            self.clen = 0
            self.headers_done = False
            self.last_recv = time.ticks_ms()
        try:
            chunk = self.conn.recv(2048)
        except OSError:
            chunk = b''
        if chunk:
            self.buf += chunk
            self.last_recv = time.ticks_ms()
        # drop connections that never send a full request
        if (b'\r\n\r\n' not in self.buf and
                time.ticks_diff(time.ticks_ms(), self.last_recv) > self.IDLE_MS):
            self._drop()
            return
        if b'\r\n\r\n' not in self.buf:
            return
        head, _, body = self.buf.partition(b'\r\n\r\n')
        if not self.headers_done:
            for line in head.split(b'\r\n'):
                if line.lower().startswith(b'content-length:'):
                    self.clen = int(line.split(b':', 1)[1].strip())
            self.headers_done = True
        if len(body) < self.clen:
            return
        self._process(head, body[:self.clen])
        self._drop()

    def _drop(self):
        try:
            self.conn.close()
        except Exception:
            pass
        self.conn = None

    def _process(self, head, body):
        reqline = head.split(b'\r\n')[0].decode('utf-8', 'replace')
        parts = reqline.split(' ')
        method = parts[0]
        path = parts[1] if len(parts) > 1 else '/'
        if method == 'GET' and path == '/':
            _http_send(self.conn, 200, status_text())
        elif method == 'POST' and path == '/update':
            ok, msg = do_update(body)
            _http_send(self.conn, 200 if ok else 500, msg)
        elif method == 'POST' and path == '/reboot':
            _http_send(self.conn, 200, "rebooting")
            time.sleep_ms(300)
            machine.reset()
        else:
            _http_send(self.conn, 404, "not found. GET / | POST /update | POST /reboot")


WDT_MS = 60000   # worst case: per-line fallback = 7 fetches x 8s socket timeout


def main():
    ip = connect_wifi()
    ota = OtaServer(OTA_PORT)
    print("OTA server: http://%s/  (update: POST /update, reset: POST /reboot)" % ip)
    ok, msg = load_show()
    print(msg)
    print("Starting LED show...")
    # resets the board if anything hangs
    wdt = machine.WDT(timeout=WDT_MS)
    wlan = network.WLAN(network.STA_IF)
    step = 0
    last = time.ticks_ms()
    last_wifi = time.ticks_ms()
    while True:
        wdt.feed()
        try:
            ota.poll()
        except Exception as e:
            print("ota error:", e)
            try:
                ota._drop()
            except Exception:
                pass
        if show is not None:
            now = time.ticks_ms()
            if time.ticks_diff(now, last) >= show.STEP_MS:
                last = now
                # catch show errors so OTA keeps running; the show can be swapped
                # between tick() and SEQ, so check each iteration
                try:
                    if hasattr(show, "tick"):
                        show.tick()
                    else:
                        name, color = show.SEQ[step]
                        show.set_color(color)
                        step = (step + 1) % len(show.SEQ)
                except Exception as e:
                    print("show error:", e)
        if time.ticks_diff(time.ticks_ms(), last_wifi) > 30000:
            last_wifi = time.ticks_ms()
            if not wlan.isconnected():
                print("WiFi lost, reconnecting")
                try:
                    wlan.connect(WIFI_SSID, WIFI_PASS)
                except Exception as e:
                    print("reconnect error:", e)
        time.sleep_ms(50)


main()
