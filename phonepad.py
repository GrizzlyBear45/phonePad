#!/usr/bin/env python3
"""
PhonePad - use your iPhone as a game controller for your Mac.

Run this on the Mac:      python3 phonepad.py
Then on the iPhone (same Wi-Fi), open the URL it prints in Safari.

The phone's buttons and sticks are turned into keyboard + mouse input on the
Mac. Tap the gear on the phone to remap buttons, change stick speed, and add
your own buttons. Those settings are saved next to this script in
phonepad_settings.json.

Needs no extra packages. The first time, macOS must allow your terminal app to
control the computer: System Settings > Privacy & Security > Accessibility.
"""

import asyncio
import base64
import copy
import ctypes
import hashlib
import json
import socket
import struct
import sys
from pathlib import Path

PORT = 8765
SETTINGS_FILE = Path(__file__).with_name("phonepad_settings.json")

# Mac key codes (US layout), in the order the phone's key menus list them.
KEY = {
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5, "h": 4, "i": 34,
    "j": 38, "k": 40, "l": 37, "m": 46, "n": 45, "o": 31, "p": 35, "q": 12,
    "r": 15, "s": 1, "t": 17, "u": 32, "v": 9, "w": 13, "x": 7, "y": 16, "z": 6,
    "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22, "7": 26, "8": 28,
    "9": 25, "0": 29,
    "space": 49, "return": 36, "tab": 48, "escape": 53, "delete": 51,
    "up": 126, "down": 125, "left": 123, "right": 124,
    "shift": 56, "control": 59, "option": 58, "command": 55,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
    "-": 27, "=": 24, "[": 33, "]": 30, ";": 41, "'": 39, ",": 43, ".": 47,
    "/": 44, "`": 50, "\\": 42,
    "page_up": 116, "page_down": 121, "home": 115, "end": 119,
}
MOUSE = ["mouse_left", "mouse_right"]
MODIFIERS = {"shift": 0x20000, "control": 0x40000, "option": 0x80000, "command": 0x100000}

# Starting settings. Changes made on the phone are saved to SETTINGS_FILE;
# delete that file to go back to these.
DEFAULTS = {
    "buttons": {
        "A": "space", "B": "e", "X": "r", "Y": "f",
        "LB": "q", "RB": "tab", "LT": "mouse_right", "RT": "mouse_left",
        "UP": "up", "DOWN": "down", "LEFT": "left", "RIGHT": "right",
        "START": "return", "SELECT": "escape",
    },
    "left_stick": {"up": "w", "down": "s", "left": "a", "right": "d"},
    "stick_threshold": 0.35,  # how far to push the left stick before a key is pressed
    "mouse_speed": 18,        # right stick: pixels per tick at full tilt (120 ticks/sec)
    "custom": [],             # [{"id", "label", "key", "x", "y"}], x/y in % of screen
}
RIGHT_STICK_DEADZONE = 0.08
MAX_CUSTOM = 16


def clean_config(raw):
    """Turn whatever the phone sent into a safe, complete config."""
    c = copy.deepcopy(DEFAULTS)
    if not isinstance(raw, dict):
        return c
    valid = set(KEY) | set(MOUSE) | {""}

    def section(name):
        given = raw.get(name)
        given = given if isinstance(given, dict) else {}
        for k in c[name]:
            if given.get(k) in valid:
                c[name][k] = given[k]

    def number(name, lo, hi):
        v = raw.get(name)
        if isinstance(v, (int, float)):
            c[name] = min(hi, max(lo, float(v)))

    section("buttons")
    section("left_stick")
    number("stick_threshold", 0.1, 0.9)
    number("mouse_speed", 1, 80)

    custom = raw.get("custom")
    for item in (custom if isinstance(custom, list) else []):
        if len(c["custom"]) >= MAX_CUSTOM:
            break
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()[:10] or "?"
        key = item.get("key") if item.get("key") in valid else ""
        pos = [item.get(a) for a in ("x", "y")]
        x, y = (min(97, max(3, float(v))) if isinstance(v, (int, float)) else 50 for v in pos)
        c["custom"].append({"id": f"c{len(c['custom'])}", "label": label, "key": key,
                            "x": round(x, 1), "y": round(y, 1)})
    return c


def load_config():
    try:
        return clean_config(json.loads(SETTINGS_FILE.read_text()))
    except (OSError, ValueError):
        return copy.deepcopy(DEFAULTS)


config = load_config()


# ------------------------------------------------- macOS input via Quartz ---
class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
ax = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")

cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
cg.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32]
cg.CGEventCreate.restype = ctypes.c_void_p
cg.CGEventCreate.argtypes = [ctypes.c_void_p]
cg.CGEventGetLocation.restype = CGPoint
cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
cg.CGEventSetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int64]
cg.CGEventSetType.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
cf.CFRelease.argtypes = [ctypes.c_void_p]
ax.AXIsProcessTrusted.restype = ctypes.c_bool

HID_TAP = 0
MOUSE_MOVED, L_DOWN, L_UP, R_DOWN, R_UP, L_DRAG, R_DRAG = 5, 1, 2, 3, 4, 6, 7
FLAGS_CHANGED = 12
DELTA_X_FIELD, DELTA_Y_FIELD = 4, 5


def post(event):
    cg.CGEventPost(HID_TAP, event)
    cf.CFRelease(event)


def mouse_location():
    e = cg.CGEventCreate(None)
    p = cg.CGEventGetLocation(e)
    cf.CFRelease(e)
    return p


class Output:
    """Presses and releases keys/mouse buttons, remembering what is held."""

    def __init__(self):
        self.held = set()

    def press(self, name, down):
        if down == (name in self.held):
            return
        (self.held.add if down else self.held.discard)(name)
        if name == "mouse_left":
            post(cg.CGEventCreateMouseEvent(None, L_DOWN if down else L_UP, mouse_location(), 0))
        elif name == "mouse_right":
            post(cg.CGEventCreateMouseEvent(None, R_DOWN if down else R_UP, mouse_location(), 1))
        elif name in KEY:
            e = cg.CGEventCreateKeyboardEvent(None, KEY[name], down)
            if name in MODIFIERS:
                cg.CGEventSetType(e, FLAGS_CHANGED)
            cg.CGEventSetFlags(e, sum(f for m, f in MODIFIERS.items() if m in self.held))
            post(e)

    def move_mouse(self, dx, dy):
        p = mouse_location()
        kind = L_DRAG if "mouse_left" in self.held else R_DRAG if "mouse_right" in self.held else MOUSE_MOVED
        e = cg.CGEventCreateMouseEvent(None, kind, CGPoint(p.x + dx, p.y + dy), 0)
        cg.CGEventSetIntegerValueField(e, DELTA_X_FIELD, int(dx))
        cg.CGEventSetIntegerValueField(e, DELTA_Y_FIELD, int(dy))
        post(e)


out = Output()
active_buttons = set()   # button ids currently held on the phone
stick_dirs = set()       # left stick directions currently pushed
right_stick = [0.0, 0.0]


def sync_output():
    """Press exactly the keys the current buttons + mapping call for."""
    custom = {b["id"]: b["key"] for b in config["custom"]}
    want = {config["buttons"].get(b) or custom.get(b) for b in active_buttons}
    want |= {config["left_stick"][d] for d in stick_dirs}
    want -= {None, ""}
    for name in out.held - want:
        out.press(name, False)
    # Modifiers first, so e.g. shift is already down when the other key lands.
    for name in sorted(want - out.held, key=lambda n: n not in MODIFIERS):
        out.press(name, True)


def handle_message(msg):
    kind, _, arg = msg.partition(":")
    if kind == "d":
        active_buttons.add(arg)
    elif kind == "u":
        active_buttons.discard(arg)
    elif kind == "ls":
        x, y = (float(v) for v in arg.split(","))
        t = config["stick_threshold"]
        stick_dirs.clear()
        stick_dirs.update(d for d, on in (("left", x < -t), ("right", x > t),
                                          ("up", y < -t), ("down", y > t)) if on)
    elif kind == "rs":
        right_stick[0], right_stick[1] = (float(v) for v in arg.split(","))
        return
    sync_output()


async def mouse_loop():
    carry = [0.0, 0.0]
    while True:
        await asyncio.sleep(1 / 120)
        x, y = right_stick
        if abs(x) < RIGHT_STICK_DEADZONE and abs(y) < RIGHT_STICK_DEADZONE:
            carry = [0.0, 0.0]
            continue
        # Squared response curve: fine aim near center, fast at the edge.
        for i, v in enumerate((x, y)):
            carry[i] += v * abs(v) * config["mouse_speed"]
        dx, dy = int(carry[0]), int(carry[1])
        carry[0] -= dx
        carry[1] -= dy
        if dx or dy:
            out.move_mouse(dx, dy)


# ----------------------------------------------- tiny HTTP + WebSocket server ---
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
clients = set()


async def ws_read(reader):
    b1, b2 = await reader.readexactly(2)
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if b2 & 0x80 else b"\0\0\0\0"
    data = bytearray(await reader.readexactly(n))
    for i in range(n):
        data[i] ^= mask[i % 4]
    return b1 & 0x0F, bytes(data)


def ws_frame(opcode, payload=b""):
    n = len(payload)
    if n < 126:
        head = bytes([0x80 | opcode, n])
    elif n < 65536:
        head = bytes([0x80 | opcode, 126]) + struct.pack(">H", n)
    else:
        head = bytes([0x80 | opcode, 127]) + struct.pack(">Q", n)
    return head + payload


def config_frame():
    keys = list(KEY) + MOUSE
    return ws_frame(1, json.dumps({"type": "config", "config": config,
                                   "defaults": DEFAULTS, "keys": keys}).encode())


async def apply_config(text):
    global config
    try:
        config = clean_config(json.loads(text))
    except ValueError:
        return
    try:
        SETTINGS_FILE.write_text(json.dumps(config, indent=2))
    except OSError as e:
        print(f"Could not save settings: {e}")
    # Custom button ids may have shifted, so drop any held ones.
    active_buttons.difference_update({b for b in active_buttons if b.startswith("c")})
    sync_output()
    print("Settings saved")
    frame = config_frame()
    for w in list(clients):
        try:
            w.write(frame)
            await w.drain()
        except ConnectionError:
            pass


async def handle_client(reader, writer):
    try:
        head = (await reader.readuntil(b"\r\n\r\n")).decode(errors="ignore")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        writer.close()
        return
    lines = head.split("\r\n")
    headers = {k.strip().lower(): v.strip() for k, v in
               (l.split(":", 1) for l in lines[1:] if ":" in l)}

    if headers.get("upgrade", "").lower() != "websocket":
        body = PAGE.encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                     b"Cache-Control: no-store\r\nContent-Length: %d\r\n"
                     b"Connection: close\r\n\r\n" % len(body) + body)
        await writer.drain()
        writer.close()
        return

    accept = base64.b64encode(hashlib.sha1(
        (headers["sec-websocket-key"] + WS_GUID).encode()).digest()).decode()
    writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                  f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
    writer.write(config_frame())
    await writer.drain()
    clients.add(writer)
    peer = writer.get_extra_info("peername")
    print(f"Controller connected from {peer[0]}")
    try:
        while True:
            op, data = await ws_read(reader)
            if op == 8:          # close
                break
            if op == 9:          # ping
                writer.write(ws_frame(10, data))
                await writer.drain()
            elif op == 1:        # text
                text = data.decode()
                if text.startswith("cfg:"):
                    await apply_config(text[4:])
                    continue
                for msg in text.split(";"):
                    if msg:
                        handle_message(msg)
    except (asyncio.IncompleteReadError, ConnectionError, ValueError):
        pass
    finally:
        clients.discard(writer)
        active_buttons.clear()
        stick_dirs.clear()
        right_stick[0] = right_stick[1] = 0.0
        sync_output()
        print("Controller disconnected")
        writer.close()


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


async def main():
    if not ax.AXIsProcessTrusted():
        print("!! macOS has not given this terminal permission to control the computer,")
        print("!! so button presses will be ignored. Fix it in:")
        print("!!   System Settings > Privacy & Security > Accessibility")
        print("!! Turn on your terminal app there, then restart this script.\n")
    server = await asyncio.start_server(handle_client, "0.0.0.0", PORT)
    print(f"PhonePad is running. On your iPhone (same Wi-Fi), open Safari and go to:\n")
    print(f"    http://{local_ip()}:{PORT}\n")
    print("Tip: tap Share > Add to Home Screen for a full-screen controller.")
    print("Tap the gear on the phone to change controls.")
    print("Press Ctrl+C here to stop.")
    asyncio.create_task(mouse_loop())
    async with server:
        await server.serve_forever()


# ------------------------------------------------------- the phone's page ---
PAGE = r"""<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="PhonePad">
<title>PhonePad</title>
<style>
  :root { --bg:#121417; --pad:#1d2126; --edge:#2c323a; --txt:#c9d1d9; --on:#3b82f6; }
  * { box-sizing:border-box; -webkit-user-select:none; user-select:none;
      -webkit-touch-callout:none; -webkit-tap-highlight-color:transparent; }
  html,body { margin:0; height:100%; background:var(--bg); color:var(--txt);
      font:600 15px -apple-system,system-ui,sans-serif; overflow:hidden; overscroll-behavior:none; }
  #pad { position:fixed; inset:0; touch-action:none; padding:env(safe-area-inset-top)
      env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); }
  .b { position:absolute; display:flex; align-items:center; justify-content:center;
      background:var(--pad); border:2px solid var(--edge); border-radius:50%; }
  .b.on { background:var(--on); border-color:var(--on); color:#fff; }
  .shoulder { border-radius:14px; width:17vw; height:11vh; }
  .face { width:15vh; height:15vh; font-size:20px; }
  .dpad { width:12vh; height:12vh; border-radius:12px; }
  .small { width:12vw; height:8vh; border-radius:20px; font-size:12px; }
  .custom { width:14vh; height:14vh; transform:translate(-50%,-50%); font-size:12px;
      text-align:center; line-height:1.1; padding:3px; overflow:hidden; word-break:break-word; }
  .stick { position:absolute; width:40vh; height:40vh; border-radius:50%;
      background:var(--pad); border:2px solid var(--edge); }
  .knob { position:absolute; left:50%; top:50%; width:16vh; height:16vh; margin:-8vh 0 0 -8vh;
      border-radius:50%; background:#3a414b; transition:transform .06s; }
  .stick.on .knob { background:var(--on); transition:none; }
  #gear { position:absolute; left:50%; top:3vh; transform:translateX(-50%); width:11vh; height:11vh;
      border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:22px;
      background:var(--pad); border:2px solid var(--edge); }
  #status { position:absolute; left:50%; top:15.5vh; transform:translateX(-50%); font-size:11px; opacity:.6; white-space:nowrap; }

  #arrangeBar { display:none; position:fixed; top:3vh; left:50%; transform:translateX(-50%); z-index:5;
      align-items:center; gap:12px; background:var(--pad); border:1px solid var(--edge);
      border-radius:22px; padding:6px 6px 6px 16px; font-size:13px; white-space:nowrap; }
  body.arranging #arrangeBar { display:flex; }
  body.arranging #gear, body.arranging #status { display:none; }
  body.arranging .custom { border-style:dashed; border-color:var(--on); }
  body.arranging .b:not(.custom), body.arranging .stick { opacity:.35; }

  #settings { display:none; position:fixed; inset:0; z-index:10; background:var(--bg);
      overflow-y:auto; -webkit-overflow-scrolling:touch; touch-action:pan-y;
      padding:0 max(16px, env(safe-area-inset-right)) 32px max(16px, env(safe-area-inset-left)); }
  #settings.open { display:block; }
  #settings .bar { position:sticky; top:0; z-index:1; display:flex; align-items:center; gap:8px;
      padding:10px 0; background:var(--bg); border-bottom:1px solid var(--edge); }
  #settings .bar b { font-size:18px; }
  #settings .sp { flex:1; }
  #settings h3 { font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.06em;
      opacity:.6; margin:18px 0 6px; }
  #settings .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr)); gap:6px 20px; }
  #settings .grid.wide { grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); }
  #settings .row { display:flex; align-items:center; gap:8px; min-height:42px; }
  #settings .lbl { flex:1; font-weight:500; }
  #settings .val { min-width:44px; text-align:right; font-variant-numeric:tabular-nums; }
  #settings input[type=range] { flex:1.4; }
  #settings select, #settings input[type=text] { font:inherit; font-size:16px; font-weight:500;
      color:var(--txt); background:var(--pad); border:1px solid var(--edge); border-radius:8px; padding:6px 8px; }
  #settings input[type=text] { flex:1; min-width:0; -webkit-user-select:text; user-select:text; }
  #settings .note { opacity:.6; font-weight:500; margin:4px 0; }
  #settings .actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
  button { font:inherit; color:var(--txt); background:var(--pad); border:1px solid var(--edge);
      border-radius:10px; padding:8px 14px; }
  button.pri { background:var(--on); border-color:var(--on); color:#fff; }
  button.del { padding:8px 12px; }
  button:disabled { opacity:.4; }

  #rotate { display:none; position:fixed; inset:0; z-index:20; background:var(--bg); align-items:center;
      justify-content:center; text-align:center; padding:24px; font-size:18px; }
  @media (orientation:portrait) { #rotate { display:flex; } }
</style></head><body>
<div id="pad">
  <div class="b shoulder" data-btn="LT" style="left:4vw;top:3vh">LT</div>
  <div class="b shoulder" data-btn="LB" style="left:23vw;top:3vh">LB</div>
  <div class="b shoulder" data-btn="RB" style="right:23vw;top:3vh">RB</div>
  <div class="b shoulder" data-btn="RT" style="right:4vw;top:3vh">RT</div>
  <div id="gear" aria-label="Settings">⚙︎</div>
  <div id="status">connecting…</div>

  <div class="stick" data-stick="ls" style="left:5vw;top:20vh"><div class="knob"></div></div>

  <div class="b dpad" data-btn="UP"    style="left:33vw;top:52vh">▲</div>
  <div class="b dpad" data-btn="LEFT"  style="left:calc(33vw - 13vh);top:65vh">◀</div>
  <div class="b dpad" data-btn="RIGHT" style="left:calc(33vw + 13vh);top:65vh">▶</div>
  <div class="b dpad" data-btn="DOWN"  style="left:33vw;top:78vh">▼</div>

  <div class="b small" data-btn="SELECT" style="left:38vw;top:22vh">SELECT</div>
  <div class="b small" data-btn="START"  style="right:38vw;top:22vh">START</div>

  <div class="b face" data-btn="Y" style="right:calc(10vw + 15vh);top:18vh">Y</div>
  <div class="b face" data-btn="X" style="right:calc(10vw + 30vh);top:33vh">X</div>
  <div class="b face" data-btn="B" style="right:10vw;top:33vh">B</div>
  <div class="b face" data-btn="A" style="right:calc(10vw + 15vh);top:48vh">A</div>

  <div class="stick" data-stick="rs" style="right:calc(33vw - 10vh);top:56vh;width:36vh;height:36vh"><div class="knob"></div></div>

  <div id="custom"></div>
</div>
<div id="arrangeBar">Drag your custom buttons <button class="pri" id="arrangeDone">Done</button></div>
<div id="settings"></div>
<div id="rotate">Turn your phone sideways to use the controller.</div>
<script>
const $ = s => document.querySelector(s);
const status = $('#status');
let ws, queue = [], cfg = null, defaults = null, keys = [], draft = null, arranging = false, drag = null;

function connect() {
  ws = new WebSocket('ws://' + location.host + '/ws');
  ws.onopen = () => status.textContent = 'connected';
  ws.onclose = () => { status.textContent = 'reconnecting…'; setTimeout(connect, 1000); };
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.type === 'config') {
      cfg = m.config; defaults = m.defaults; keys = m.keys;
      if (!arranging) renderCustom(cfg);
    }
  };
}
connect();
function send(m) { queue.push(m); }
(function flush() {
  if (queue.length && ws.readyState === 1) ws.send(queue.join(';'));
  queue = [];
  requestAnimationFrame(flush);
})();

// Buttons: each tracks which fingers are on it. In arrange mode, custom buttons drag instead.
function bindButton(el, id) {
  const touches = new Set();
  const update = () => {
    const on = touches.size > 0;
    if (on !== el.classList.contains('on')) { el.classList.toggle('on', on); send((on ? 'd:' : 'u:') + id); }
  };
  el.addEventListener('touchstart', e => {
    e.preventDefault();
    if (arranging) {
      if (el.dataset.idx !== undefined && !drag) {
        drag = { el, idx: +el.dataset.idx, touch: e.changedTouches[0].identifier };
        el.classList.add('on');
      }
      return;
    }
    for (const t of e.changedTouches) touches.add(t.identifier);
    update();
  });
  const end = e => { e.preventDefault(); for (const t of e.changedTouches) touches.delete(t.identifier); update(); };
  el.addEventListener('touchend', end);
  el.addEventListener('touchcancel', end);
}
document.querySelectorAll('[data-btn]').forEach(el => bindButton(el, el.dataset.btn));

function renderCustom(c) {
  const box = $('#custom');
  box.innerHTML = '';
  c.custom.forEach((b, i) => {
    const el = document.createElement('div');
    el.className = 'b custom';
    el.textContent = b.label;
    el.dataset.idx = i;
    el.style.left = b.x + 'vw';
    el.style.top = b.y + 'vh';
    bindButton(el, b.id || 'new' + i);
    box.appendChild(el);
  });
}

// Sticks: follow one finger, report x,y in -1..1.
document.querySelectorAll('[data-stick]').forEach(el => {
  const name = el.dataset.stick, knob = el.querySelector('.knob');
  let id = null, last = '';
  const set = (x, y) => {
    knob.style.transform = `translate(${x * el.clientWidth * 0.35}px,${y * el.clientHeight * 0.35}px)`;
    const m = name + ':' + x.toFixed(2) + ',' + y.toFixed(2);
    if (m !== last) { last = m; queue = queue.filter(q => !q.startsWith(name + ':')); send(m); }
  };
  const move = t => {
    const r = el.getBoundingClientRect(), rad = r.width * 0.35;
    let x = (t.clientX - r.left - r.width / 2) / rad, y = (t.clientY - r.top - r.height / 2) / rad;
    const len = Math.hypot(x, y); if (len > 1) { x /= len; y /= len; }
    set(x, y);
  };
  el.addEventListener('touchstart', e => {
    e.preventDefault();
    if (arranging) return;
    if (id === null) { const t = e.changedTouches[0]; id = t.identifier; el.classList.add('on'); move(t); }
  });
  el.addEventListener('touchmove', e => {
    e.preventDefault();
    for (const t of e.changedTouches) if (t.identifier === id) move(t);
  });
  const end = e => {
    e.preventDefault();
    for (const t of e.changedTouches) if (t.identifier === id) { id = null; el.classList.remove('on'); set(0, 0); }
  };
  el.addEventListener('touchend', end);
  el.addEventListener('touchcancel', end);
});

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
document.addEventListener('touchmove', e => {
  if (e.target.closest && e.target.closest('#settings')) return;
  e.preventDefault();
  if (drag) for (const t of e.changedTouches) if (t.identifier === drag.touch) {
    const x = Math.round(clamp(t.clientX / innerWidth * 100, 3, 97) * 10) / 10;
    const y = Math.round(clamp(t.clientY / innerHeight * 100, 3, 97) * 10) / 10;
    drag.el.style.left = x + 'vw'; drag.el.style.top = y + 'vh';
    Object.assign(draft.custom[drag.idx], { x, y });
  }
}, { passive: false });
const dragEnd = e => {
  if (drag) for (const t of e.changedTouches) if (t.identifier === drag.touch) { drag.el.classList.remove('on'); drag = null; }
};
document.addEventListener('touchend', dragEnd);
document.addEventListener('touchcancel', dragEnd);

// ---------- settings ----------
const BUTTON_NAMES = [['A', 'A'], ['B', 'B'], ['X', 'X'], ['Y', 'Y'], ['LB', 'LB'], ['RB', 'RB'],
  ['LT', 'LT'], ['RT', 'RT'], ['UP', 'D-pad up'], ['DOWN', 'D-pad down'], ['LEFT', 'D-pad left'],
  ['RIGHT', 'D-pad right'], ['START', 'Start'], ['SELECT', 'Select']];
const KEY_NAMES = { '': '(nothing)', mouse_left: 'Left click', mouse_right: 'Right click',
  up: '↑ Up arrow', down: '↓ Down arrow', left: '← Left arrow', right: '→ Right arrow',
  page_up: 'Page Up', page_down: 'Page Down' };
const keyName = k => KEY_NAMES[k] || (k.length === 1 ? k.toUpperCase() : k[0].toUpperCase() + k.slice(1));
const clone = o => JSON.parse(JSON.stringify(o));

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
function btn(text, onClick, cls) { const b = el('button', cls, text); b.onclick = onClick; return b; }
function row(label, ...controls) { const r = el('div', 'row'); r.append(el('span', 'lbl', label), ...controls); return r; }
function keySelect(value, onChange) {
  const s = el('select');
  ['', ...keys].forEach(k => { const o = el('option', null, keyName(k)); o.value = k; s.appendChild(o); });
  s.value = value || '';
  s.onchange = () => onChange(s.value);
  return s;
}
function slider(label, min, max, step, value, onChange, fmt = v => v) {
  const i = el('input'); Object.assign(i, { type: 'range', min, max, step, value });
  const out = el('span', 'val');
  const show = () => out.textContent = fmt(+i.value);
  i.oninput = () => { onChange(+i.value); show(); };
  show();
  return row(label, i, out);
}

function renderSettings() {
  const p = $('#settings'), scroll = p.scrollTop;
  p.innerHTML = '';
  const bar = el('div', 'bar');
  bar.append(el('b', null, 'Settings'), el('span', 'sp'),
    btn('Reset all', () => { draft = clone(defaults); renderSettings(); }),
    btn('Cancel', closeSettings),
    btn('Save', () => { saveDraft(); closeSettings(); }, 'pri'));
  p.append(bar);

  p.append(el('h3', null, 'Joystick speed'));
  const js = el('div', 'grid wide');
  js.append(
    slider('Right stick (mouse) speed', 2, 60, 1, draft.mouse_speed, v => draft.mouse_speed = v),
    slider('Left stick push needed', 0.1, 0.9, 0.05, draft.stick_threshold, v => draft.stick_threshold = v,
      v => Math.round(v * 100) + '%'));
  p.append(js);

  p.append(el('h3', null, 'Left stick keys'));
  const lg = el('div', 'grid');
  for (const [d, n] of [['up', 'Push up'], ['down', 'Push down'], ['left', 'Push left'], ['right', 'Push right']])
    lg.append(row(n, keySelect(draft.left_stick[d], v => draft.left_stick[d] = v)));
  p.append(lg);

  p.append(el('h3', null, 'Buttons'));
  const bg = el('div', 'grid');
  for (const [id, n] of BUTTON_NAMES) bg.append(row(n, keySelect(draft.buttons[id], v => draft.buttons[id] = v)));
  p.append(bg);

  p.append(el('h3', null, 'Custom buttons'));
  const cl = el('div', 'grid');
  draft.custom.forEach((b, i) => {
    const name = el('input'); Object.assign(name, { type: 'text', value: b.label, maxLength: 10, placeholder: 'Name' });
    name.oninput = () => b.label = name.value;
    const r = el('div', 'row');
    r.append(name, keySelect(b.key, v => b.key = v),
      btn('✕', () => { draft.custom.splice(i, 1); renderSettings(); }, 'del'));
    cl.append(r);
  });
  if (!draft.custom.length) cl.append(el('p', 'note', 'No custom buttons yet.'));
  p.append(cl);

  const actions = el('div', 'actions');
  const add = btn('+ Add button', () => {
    draft.custom.push({ label: 'Btn ' + (draft.custom.length + 1), key: '', x: 50, y: 45 });
    renderSettings();
  });
  add.disabled = draft.custom.length >= 16;
  const move = btn('Move buttons on screen', startArrange);
  move.disabled = !draft.custom.length;
  actions.append(add, move);
  p.append(actions);
  p.scrollTop = scroll;
}

function openSettings() {
  if (!cfg) { alert('Not connected to your Mac yet.'); return; }
  draft = clone(cfg);
  $('#settings').classList.add('open');
  $('#settings').scrollTop = 0;
  renderSettings();
}
function closeSettings() { $('#settings').classList.remove('open'); }
function saveDraft() {
  if (ws.readyState !== 1) { alert('Not connected to your Mac, so settings were not saved.'); renderCustom(cfg); return; }
  ws.send('cfg:' + JSON.stringify(draft));
  cfg = draft;
  renderCustom(cfg);
}
function startArrange() {
  closeSettings();
  arranging = true;
  document.body.classList.add('arranging');
  renderCustom(draft);
}
$('#arrangeDone').onclick = () => {
  arranging = false;
  drag = null;
  document.body.classList.remove('arranging');
  saveDraft();
};
$('#gear').addEventListener('click', openSettings);
</script>
</body></html>
"""

if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("This script is for macOS.")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        active_buttons.clear()
        stick_dirs.clear()
        sync_output()
        print("\nStopped.")
