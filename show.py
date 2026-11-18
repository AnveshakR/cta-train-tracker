# CTA train tracker show. Every POLL_S it fetches train positions and lights the
# stop each train is approaching (isApp) until it nears the next. main.py calls tick() every STEP_MS.
import time
import json
import gc
import socket
import neopixel
from machine import Pin
from secrets import API_KEY

POLL_S = 10          # fetch cadence (CTA feed refreshes in ~20 s batches)
STEP_MS = 50         # animation frame time
FADE_MS = 3000       # time for a stop to fade fully in or out
BRIGHTNESS = 64      # 0-255; keep low on USB power
DEBUG = False        # print lit stops each poll (serial)

STRIPS = [
    ("red",    12),
    ("blue",   13),
    ("green",  14),
    ("brown",  15),
    ("pink",   19),
    ("orange", 5),    # no D17 on this board
    ("purple", 18),
]

STRIP_LEDS = {
    "red": 5, "blue": 7, "green": 5,
    "brown": 9, "pink": 9, "orange": 8, "purple": 9,
}
LEDS_PER_STRIP = 10  # status page only

# CTA route codes differ from line names
LINE_RT = {
    "red": "red", "blue": "blue", "green": "g", "brown": "brn",
    "pink": "pink", "orange": "org", "purple": "p",
}

LINE_RGB = {
    "red":    (255, 32, 32),
    "blue":   (32, 128, 255),
    "green":  (0, 200, 0),
    "brown":  (150, 75, 0),
    "pink":   (255, 105, 180),
    "orange": (255, 140, 0),
    "purple": (150, 0, 200),
}

# (map_id, name) per line; list index = LED number. map_id matches ttpositions nextStaId.
LINE_STOPS = {
    "red": [
        (41490, "Harrison"),
        (40560, "Jackson"),
        (41090, "Monroe"),
        (41660, "Lake"),
        (40330, "Grand"),
    ],
    "blue": [
        (40350, "UIC-Halsted"),
        (40430, "Clinton"),
        (41340, "LaSalle"),
        (40070, "Jackson"),
        (40790, "Monroe"),
        (40370, "Washington"),
        (40380, "Clark/Lake"),
    ],
    # State/Lake is closed until 2029, so it stays dark
    "green": [
        (40680, "Adams/Wabash"),
        (41700, "Washington/Wabash"),
        (40260, "State/Lake"),
        (40380, "Clark/Lake"),
        (41160, "Clinton"),
    ],
    "purple": [
        (40460, "Merchandise Mart"), (40730, "Washington/Wells"), (40040, "Quincy/Wells"),
        (40160, "LaSalle/Van Buren"), (40850, "Library"), (40680, "Adams/Wabash"),
        (41700, "Washington/Wabash"), (40260, "State/Lake"), (40380, "Clark/Lake"),
    ],
    "pink": [
        (41160, "Clinton"), (40380, "Clark/Lake"), (40260, "State/Lake"),
        (41700, "Washington/Wabash"), (40680, "Adams/Wabash"), (40850, "Library"),
        (40160, "LaSalle/Van Buren"), (40040, "Quincy/Wells"), (40730, "Washington/Wells"),
    ],
    "orange": [
        (40040, "Quincy/Wells"), (40730, "Washington/Wells"), (40380, "Clark/Lake"),
        (40260, "State/Lake"), (41700, "Washington/Wabash"), (40680, "Adams/Wabash"),
        (40850, "Library"), (40160, "LaSalle/Van Buren"),
    ],
    "brown": [
        (40160, "LaSalle/Van Buren"), (40850, "Library"), (40680, "Adams/Wabash"),
        (41700, "Washington/Wabash"), (40260, "State/Lake"), (40380, "Clark/Lake"),
        (40460, "Merchandise Mart"), (40730, "Washington/Wells"), (40040, "Quincy/Wells"),
    ],
}

NP = {}
for _name, _pin in STRIPS:
    NP[_name] = neopixel.NeoPixel(Pin(_pin, Pin.OUT), STRIP_LEDS.get(_name, 10))

OFF = (0, 0, 0)
# makes the first tick() fetch immediately
_last_poll = time.ticks_add(time.ticks_ms(), -POLL_S * 1000)
_lit = {name: [False] * len(stops) for name, stops in LINE_STOPS.items()}
# displayed brightness 0..1 per LED, eases toward _lit
_cur = {name: [0.0] * len(stops) for name, stops in LINE_STOPS.items()}
_last_frame = time.ticks_ms()


def _scale(rgb, frac=1.0):
    # squared for a perceptual brightness curve
    k = BRIGHTNESS * frac * frac / 255
    return (int(rgb[0] * k), int(rgb[1] * k), int(rgb[2] * k))


API_HOST = "lapi.transitchicago.com"
API_PATH = "/api/1.0/ttpositions.aspx"
HTTP_TIMEOUT_S = 8   # bounds a stalled CTA response


def _http_get_json(rt_list):
    """GET ttpositions for comma-separated route codes."""
    path = "%s?key=%s&rt=%s&outputType=JSON" % (API_PATH, API_KEY, rt_list)
    addr = socket.getaddrinfo(API_HOST, 80)[0][-1]
    s = socket.socket()
    s.settimeout(HTTP_TIMEOUT_S)
    chunks = []
    try:
        s.connect(addr)
        s.send(("GET %s HTTP/1.0\r\nHost: %s\r\nConnection: close\r\n\r\n"
                % (path, API_HOST)).encode())
        while True:
            c = s.recv(1024)
            if not c:
                break
            chunks.append(c)
    finally:
        s.close()
    raw = b"".join(chunks)
    del chunks
    body = raw.split(b"\r\n\r\n", 1)[1]
    del raw
    gc.collect()
    return json.loads(body.decode())


def _by_route(data):
    """-> (tmst, {route_code: [train dicts]}) with dict/list normalized."""
    ctatt = data.get("ctatt", {})
    routes = ctatt.get("route", [])
    if isinstance(routes, dict):
        routes = [routes]
    out = {}
    for rt in routes:
        tr = rt.get("train", [])
        if isinstance(tr, dict):
            tr = [tr]
        out[rt.get("@name", "")] = tr
    return ctatt.get("tmst", ""), out


# per line: run number -> mapped stop that train last held. Entries are dropped
# when the train is flagged approaching a stop off the map, or leaves the feed.
_held = {}
_MAPPED = {l: set(str(m) for m, _n in stops) for l, stops in LINE_STOPS.items()}


def _compute_lit(line, tmst, trains):
    """A train lights its next stop when flagged approaching/due (isApp) and
    keeps it lit until it is flagged approaching the following stop. Once that
    following stop is off the map, the train is forgotten. LED i is on while
    any train holds stop i."""
    held = _held.setdefault(line, {})
    mapped = _MAPPED[line]
    seen = set()
    for t in trains:
        rn = t.get("rn")
        seen.add(rn)
        if str(t.get("isApp")) == "1":
            nxt = str(t.get("nextStaId"))
            if nxt in mapped:
                held[rn] = nxt
            else:
                held.pop(rn, None)
    for rn in list(held):
        if rn not in seen:      # train left the feed (end of run)
            del held[rn]
    here = set(held.values())
    return [str(map_id) in here for map_id, _n in LINE_STOPS[line]]


class _RouteLimit(Exception):
    """CTA error 107: more routes in one request than the API allows."""


def _fetch_routes(codes):
    """-> (tmst, {route_code: [trains]}) for the given route codes."""
    gc.collect()
    data = _http_get_json(",".join(codes))
    try:
        err = int(data.get("ctatt", {}).get("errCd", 0))
    except (TypeError, ValueError):
        err = 0
    if err == 107:
        raise _RouteLimit()
    return _by_route(data)


def _apply(lines, tmst, by_rt):
    for line in lines:
        _lit[line] = _compute_lit(line, tmst, by_rt.get(LINE_RT[line], []))


def _poll_groups(groups):
    """One request per group of lines."""
    for lines in groups:
        tmst, by_rt = _fetch_routes([LINE_RT[l] for l in lines])
        _apply(lines, tmst, by_rt)


def _poll_all():
    """One request for all lines. On the route-limit error (107), two requests
    of at most 4 routes each. On MemoryError, one request per line."""
    lines = list(LINE_STOPS)
    try:
        try:
            _poll_groups([lines])
            return
        except _RouteLimit:
            print("show: route limit hit, splitting into 2 requests")
        half = (len(lines) + 1) // 2
        _poll_groups([lines[:half], lines[half:]])
        return
    except MemoryError:
        print("show: grouped fetch out of memory, falling back per line")
    except Exception as e:
        print("show: fetch failed: %s" % e)
        return
    for line in lines:
        try:
            _poll_groups([[line]])
        except Exception as e:
            print("show: fetch %s failed: %s" % (line, e))


def _render():
    """One animation frame: move every LED toward its target, write if changed."""
    global _last_frame
    now = time.ticks_ms()
    step = time.ticks_diff(now, _last_frame) / FADE_MS
    _last_frame = now
    if step > 1.0:
        step = 1.0
    for name, stops in LINE_STOPS.items():
        cur = _cur[name]
        lit = _lit[name]
        changed = False
        for i in range(len(stops)):
            tgt = 1.0 if lit[i] else 0.0
            c = cur[i]
            if c != tgt:
                c = min(tgt, c + step) if tgt > c else max(tgt, c - step)
                cur[i] = c
                changed = True
        if changed:
            np = NP[name]
            rgb = LINE_RGB.get(name, (255, 255, 255))
            for i in range(len(stops)):
                np[i] = _scale(rgb, cur[i])
            np.write()


def tick():
    """Called by main.py every STEP_MS (a frame). Fetches once per POLL_S."""
    global _last_poll
    now = time.ticks_ms()
    if time.ticks_diff(now, _last_poll) >= POLL_S * 1000:
        _last_poll = now
        try:
            _poll_all()
        except Exception as e:
            print("show: poll failed: %s" % e)
        print("show: updated %d lines, free mem %d" % (len(LINE_STOPS), gc.mem_free()))
        if DEBUG:
            for line, stops in LINE_STOPS.items():
                on = [stops[i][1] for i, v in enumerate(_lit[line]) if v]
                print("  %s: %s" % (line, ", ".join(on) if on else "-"))
    _render()


for _np in NP.values():
    _np.fill(OFF)
    _np.write()
