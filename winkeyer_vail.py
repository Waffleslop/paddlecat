#!/usr/bin/env python3
"""
winkeyer_vail.py
================
Turn a K1EL WinKeyer + paddle into a Vail/VBand-style keyboard so you can
play browser CW games (Morse Invaders, vail.woozle.org, VBand) with the
gear you already own -- no extra hardware.

How it works
------------
The WinKeyer host protocol has a little-known "Paddle Status" feature.
When enabled, the keyer reports the *raw* dit-lever and dah-lever closures
back over USB (WinKeyer3 datasheet, "Versions Compared", p.43):

    a status byte with the top bits 110 and bit 3 set carries
        bit 0 = 1 while the dit lever is closed
        bit 1 = 1 while the dah lever is closed

We map the dit lever to Left Ctrl and the dah lever to Right Ctrl, and let
the game's own iambic keyer do the timing -- exactly like a VBand adapter.

Commands
--------
    python winkeyer_vail.py ports             list serial ports
    python winkeyer_vail.py detect            probe ports for a WinKeyer
    python winkeyer_vail.py monitor [--port]  show raw bytes (diagnostics)
    python winkeyer_vail.py keytest           press the mapped keys once
    python winkeyer_vail.py run     [--port]  translate paddle -> keyboard

The serial link is fixed at 1200 baud, 8 data bits, no parity, 2 stop bits
(WinKeyer3 datasheet, p.18).
"""

import argparse
import ctypes
import sys
import time
from ctypes import wintypes

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.exit("pyserial is required:  python -m pip install pyserial")


# --------------------------------------------------------------------------
# WinKeyer host-mode protocol
# --------------------------------------------------------------------------
WK_BAUD = 1200

HOST_OPEN  = bytes([0x00, 0x02])   # Admin 2  -- enter host mode
HOST_CLOSE = bytes([0x00, 0x03])   # Admin 3  -- back to standalone
SET_WK2    = bytes([0x00, 0x0B])   # Admin 11 -- WK2 mode (pushbutton/paddle status)
ADMIN_LOAD_X1MODE = 0x0F           # Admin 15 -- load X1MODE register
X1MODE_PADDLE_STATUS = 0x02        # X1MODE bit 1 -- "Enable Paddle Status"
CMD_SET_MODE = 0x0E                # Set WinKeyer Mode
MODE_IAMBICB_NO_WDOG = 0x80        # bit 7 disables the paddle watchdog
CMD_SET_PINCFG = 0x09              # Set output pin config (bit 1 = sidetone enable)


def open_wk(port):
    """Open a serial port with the WinKeyer's framing (1200 8N2)."""
    return serial.Serial(
        port=port, baudrate=WK_BAUD,
        bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_TWO,
        timeout=0, write_timeout=1.0)


def open_wk_or_exit(port):
    """open_wk() with a friendly message when the port is busy or missing."""
    try:
        return open_wk(port)
    except serial.SerialException as exc:
        if "Access is denied" in str(exc):
            sys.exit(
                f"Cannot open {port}: the port is in use.\n"
                "  - Close any other window still running this tool "
                "(an earlier 'run' or 'monitor' -- Ctrl+C it).\n"
                "  - Close other software that may hold the port (a logger, "
                "rig control, WK3tools).\n"
                "  - If it persists, unplug and replug the WinKeyer USB.")
        sys.exit(f"Cannot open {port}: {exc}")


def _wait_byte(ser, timeout):
    """Block up to `timeout` seconds for one byte; return it or None."""
    end = time.time() + timeout
    while time.time() < end:
        b = ser.read(1)
        if b:
            return b[0]
        time.sleep(0.005)
    return None


def init_wk(ser, wkmode="wk2", paddle_echo=False, mute_sidetone=True):
    """Enter host mode and turn on raw paddle-status reporting.

    wkmode        'wk2' -> WK2 mode + X1MODE paddle-status bit (datasheet Table 1)
                  'wk3' -> WK3 mode + X2MODE paddle-status bit (datasheet Table 5)
                  'off' -> host mode only, no paddle status
    paddle_echo   also echo decoded letters back -- a diagnostic fallback so we
                  can tell whether the keyer sees the paddle at all.
    mute_sidetone clear PINCFG so the WinKeyer stops beeping its own sidetone.

    Returns the firmware revision byte WinKeyer sends on open (or None)."""
    ser.reset_input_buffer()
    ser.write(HOST_OPEN)
    ver = _wait_byte(ser, 1.5)          # datasheet: wait for the revision code
    time.sleep(0.1)
    if wkmode == "wk3":
        ser.write(bytes([0x00, 0x14]))            # Admin 20: WK3 mode
        time.sleep(0.1)
        ser.write(bytes([0x00, 0x16, 0x80]))      # Admin 22: X2MODE bit7 = paddle status
        time.sleep(0.1)
    elif wkmode == "wk2":
        ser.write(bytes([0x00, 0x0B]))            # Admin 11: WK2 mode
        time.sleep(0.1)
        ser.write(bytes([0x00, 0x0F, 0x02]))      # Admin 15: X1MODE bit1 = paddle status
        time.sleep(0.1)
    mode = 0x80                                    # bit7: disable paddle watchdog
    if paddle_echo:
        mode |= 0x40                               # bit6: paddle echoback (decoded letters)
    ser.write(bytes([CMD_SET_MODE, mode]))
    time.sleep(0.1)
    if mute_sidetone:
        # PINCFG bit 1 = sidetone enable; 0x00 clears it (and the unused key/PTT
        # outputs). The browser does the keying, so the WinKeyer's beep is just
        # a confusing second keyer. Host-Close restores the standby settings.
        ser.write(bytes([CMD_SET_PINCFG, 0x00]))
        time.sleep(0.1)
    ser.reset_input_buffer()
    return ver


def close_wk(ser):
    """Return the keyer to standalone mode and close the port."""
    try:
        ser.write(HOST_CLOSE)
        ser.flush()
        time.sleep(0.1)
    except Exception:
        pass
    try:
        ser.close()
    except Exception:
        pass


def is_paddle_byte(b):
    """True if `b` is a pushbutton/paddle status byte (110, bit 3 set)."""
    return (b & 0xC0) == 0xC0 and (b & 0x08)


# --------------------------------------------------------------------------
# Synthetic keyboard output (Windows SendInput, scancode injection)
# --------------------------------------------------------------------------
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP       = 0x0002
KEYEVENTF_SCANCODE    = 0x0008
INPUT_KEYBOARD        = 1

ULONG_PTR = wintypes.WPARAM        # pointer-sized, matches ULONG_PTR


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):   # only present so the union is sized right
    _fields_ = [("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_user32.SendInput.restype = wintypes.UINT

# Scancode set 1.  (scancode, is_extended) -- right Ctrl is an E0-extended key,
# which is how the browser tells "ControlRight" from "ControlLeft".
KEYMAP = {
    "lctrl":    (0x1D, False),
    "rctrl":    (0x1D, True),
    "lbracket": (0x1A, False),
    "rbracket": (0x1B, False),
    "space":    (0x39, False),
    "lshift":   (0x2A, False),
    "z":        (0x2C, False),
    "x":        (0x2D, False),
    "comma":    (0x33, False),
    "period":   (0x34, False),
    "slash":    (0x35, False),
}


def send_key(scan, extended, keyup):
    """Inject one keyboard event by scancode."""
    flags = KEYEVENTF_SCANCODE
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if keyup:
        flags |= KEYEVENTF_KEYUP
    inp = INPUT(type=INPUT_KEYBOARD,
                u=_INPUTUNION(ki=KEYBDINPUT(0, scan, flags, 0, 0)))
    if _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError(ctypes.get_last_error())


# --------------------------------------------------------------------------
# Serial port discovery
# --------------------------------------------------------------------------
def list_ports():
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return
    print("Serial ports:")
    for p in ports:
        print(f"  {p.device:8}  {p.description}")


def probe(port):
    """Open `port`, send Host-Open, return the revision byte WinKeyer sends."""
    try:
        ser = open_wk(port)
    except Exception as exc:
        return None, f"could not open ({exc})"
    try:
        ser.reset_input_buffer()
        ser.write(HOST_OPEN)
        ver = _wait_byte(ser, 1.0)
        ser.write(HOST_CLOSE)
        ser.flush()
        time.sleep(0.1)
    finally:
        ser.close()
    if ver is None:
        return None, "no response"
    return ver, f"responded with 0x{ver:02X}"


def detect():
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return None
    print("Probing ports for a WinKeyer (sending Host-Open)...")
    found = None
    for p in ports:
        ver, note = probe(p.device)
        flag = ""
        if ver is not None and 0x09 <= ver <= 0x40:
            flag = "  <-- looks like a WinKeyer"
            found = found or p.device
        print(f"  {p.device:8}  {note}{flag}")
    if found:
        print(f"\nUse:  python winkeyer_vail.py run --port {found}")
    else:
        print("\nNo WinKeyer found. Is it plugged in? Try a specific --port.")
    return found


def resolve_port(port):
    """Return the chosen port, auto-detecting if none was given."""
    if port:
        return port
    print("No --port given; auto-detecting...")
    found = detect()
    if not found:
        sys.exit(1)
    print()
    return found


# --------------------------------------------------------------------------
# monitor -- raw byte diagnostics
# --------------------------------------------------------------------------
def decode(b, wkmode="wk2"):
    """Human-readable decode of a WinKeyer->host byte.

    The meaning of status-byte bit 3 depends on the mode the keyer is in:
    in WK1 mode it is KEYDOWN; in WK2/WK3 mode a set bit 3 marks a
    pushbutton/paddle status byte."""
    bit = lambda m: int(bool(b & m))
    if (b & 0xC0) == 0xC0:
        if wkmode == "wk1":
            return (f"status    WAIT={bit(0x10)} KEYDOWN={bit(0x08)} "
                    f"BUSY={bit(0x04)} BREAKIN={bit(0x02)} XOFF={bit(0x01)}")
        if b & 0x08:                  # WK2/WK3: pushbutton/paddle status byte
            return (f"paddle    dit={bit(0x01)} dah={bit(0x02)}  "
                    f"(pb3={bit(0x04)} pb4={bit(0x10)})")
        return (f"status    WAIT={bit(0x10)} BUSY={bit(0x04)} "
                f"BREAKIN={bit(0x02)} XOFF={bit(0x01)}")
    if (b & 0xC0) == 0x80:
        return f"speedpot  wpm-offset={b & 0x3F}"
    ch = chr(b) if 0x20 <= b <= 0x7E else "."
    return f"echo      '{ch}'"


def monitor(port, wkmode="wk2"):
    port = resolve_port(port)
    ser = open_wk_or_exit(port)
    ver = init_wk(ser, wkmode=wkmode, paddle_echo=True)
    print(f"Opened {port}. WinKeyer firmware byte: "
          f"{'0x%02X' % ver if ver is not None else 'no response'}")
    print(f"Init: {wkmode} mode, paddle-echo on"
          f"{', paddle-status requested' if wkmode in ('wk2', 'wk3') else ''}.")
    print()
    print("Work the paddle for several seconds, then Ctrl+C. Look for:")
    if wkmode == "wk1":
        print("  'status' lines whose KEYDOWN flips 0/1 with every dit and dah")
    else:
        print("  'paddle' lines whose dit/dah track your levers")
    print("  'echo' lines = decoded letters (proves the keyer sees the paddle)")
    print("A 'status' line should appear immediately (a poke to confirm RX).")
    print("Ctrl+C to stop.\n")
    ser.write(bytes([0x15]))            # Request WinKeyer Status -- confirms RX path
    t0 = time.time()
    try:
        while True:
            n = ser.in_waiting
            if not n:
                time.sleep(0.001)
                continue
            for b in ser.read(n):
                print(f"[{(time.time() - t0) * 1000:9.1f} ms]  "
                      f"0x{b:02X}  {decode(b, wkmode)}")
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        close_wk(ser)


# --------------------------------------------------------------------------
# keytest -- verify keyboard injection without the WinKeyer
# --------------------------------------------------------------------------
def keytest(dit_key, dah_key):
    print("Click into a text box or the game, then watch for 5 seconds...")
    for i in (3, 2, 1):
        print(f"  {i}...")
        time.sleep(1)
    for name in (dit_key, dah_key):
        scan, ext = KEYMAP[name]
        print(f"  pressing {name}")
        send_key(scan, ext, keyup=False)
        time.sleep(0.25)
        send_key(scan, ext, keyup=True)
        time.sleep(0.5)
    print("Done. If the game/textbox reacted, keyboard output works.")


# --------------------------------------------------------------------------
# run -- the translator
# --------------------------------------------------------------------------
def run(port, dit_key, dah_key, wkmode="wk3", send_keys=True, quiet=False,
        debounce_ms=None, keyer_sidetone=False):
    """Translate WinKeyer paddle activity into keystrokes.

    wk2 / wk3 : paddle mode -- raw dit/dah levers drive two keys, the game's
                own keyer does the iambic timing (set the game to paddle input).
    wk1       : straight-key mode -- the WinKeyer does the iambic and we follow
                its KEYDOWN status with a single key (set the game to straight
                key on that key)."""
    port = resolve_port(port)
    for k in (dit_key, dah_key):
        if k not in KEYMAP:
            sys.exit(f"Unknown key '{k}'. Choose from: {', '.join(sorted(KEYMAP))}")

    # Ask Windows for ~1 ms timer resolution so our poll loop stays snappy.
    winmm = ctypes.WinDLL("winmm")
    winmm.timeBeginPeriod(1)

    ser = open_wk_or_exit(port)
    ver = init_wk(ser, wkmode=wkmode, mute_sidetone=not keyer_sidetone)
    straight = (wkmode == "wk1")
    if debounce_ms is None:
        # wk1 follows the keyer's clean digital output -- no bounce to filter.
        debounce_ms = 0 if straight else 25
    print(f"Connected to {port} "
          f"(firmware byte {'0x%02X' % ver if ver is not None else '?'}).")
    if straight:
        print(f"  straight-key mode: WinKeyer does the iambic; key -> {dit_key}")
        print(f"  >> set the game to STRAIGHT KEY input on '{dit_key}'")
    else:
        print(f"  paddle mode ({wkmode}): dit lever -> {dit_key}, "
              f"dah lever -> {dah_key}")
        print("  >> set the game to IAMBIC/PADDLE input on those keys")
    print(f"  WinKeyer sidetone: {'on' if keyer_sidetone else 'muted'}")
    if not send_keys:
        print("  (--no-keyboard: printing events only, not pressing keys)")
    print("Sending. Ctrl+C to stop.\n")

    keys = {"dit": KEYMAP[dit_key], "dah": KEYMAP[dah_key]}
    down = {"dit": False, "dah": False}
    lock_until = {"dit": 0.0, "dah": 0.0}
    debounce_s = debounce_ms / 1000.0
    saw = False

    def apply(name, now):
        if down[name] == now:
            return
        t = time.monotonic()
        if t < lock_until[name]:
            return                       # within debounce window -- contact bounce
        down[name] = now
        lock_until[name] = t + debounce_s
        if send_keys:
            scan, ext = keys[name]
            send_key(scan, ext, keyup=not now)
        if not quiet:
            print(f"  {name} {'DOWN' if now else 'up'}")

    try:
        while True:
            n = ser.in_waiting
            if not n:
                time.sleep(0.001)
                continue
            for b in ser.read(n):
                if (b & 0xC0) != 0xC0:
                    continue                       # not a status byte
                if straight:
                    saw = True
                    apply("dit", bool(b & 0x08))   # WK1 status bit 3 = KEYDOWN
                elif b & 0x08:                     # WK2/WK3 paddle status byte
                    saw = True
                    apply("dit", bool(b & 0x01))
                    apply("dah", bool(b & 0x02))
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        if send_keys:                              # never leave a key stuck down
            for name in ("dit", "dah"):
                if down[name]:
                    scan, ext = keys[name]
                    send_key(scan, ext, keyup=True)
        close_wk(ser)
        winmm.timeEndPeriod(1)
        if not saw:
            other = {"wk1": "wk2", "wk2": "wk3", "wk3": "wk1"}[wkmode]
            print(f"\nNote: no usable {wkmode} events were seen. "
                  f"Try --wkmode {other}, or run 'monitor' to diagnose.")


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Translate WinKeyer paddle input into keyboard keystrokes.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ports", help="list serial ports")
    sub.add_parser("detect", help="probe ports for a WinKeyer")

    keys = ", ".join(sorted(KEYMAP))
    for name, help_ in (("monitor", "show raw bytes (diagnostics)"),
                        ("run", "translate paddle -> keyboard")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--port", help="serial port, e.g. COM24 (auto-detect if omitted)")
        sp.add_argument("--wkmode", choices=["wk1", "wk2", "wk3"], default="wk3",
                        help="wk2/wk3 = paddle mode (raw levers); "
                             "wk1 = straight-key mode (follow KEYDOWN)")
        if name == "run":
            sp.add_argument("--dit", default="lctrl", help=f"key for dit lever ({keys})")
            sp.add_argument("--dah", default="rctrl", help=f"key for dah lever ({keys})")
            sp.add_argument("--swap", action="store_true", help="swap dit/dah levers")
            sp.add_argument("--debounce", type=int, default=None, metavar="MS",
                            help="contact-bounce filter, ms "
                                 "(default 25 for paddle modes, 0 for wk1)")
            sp.add_argument("--keyer-sidetone", action="store_true",
                            help="keep the WinKeyer's own sidetone beep on "
                                 "(muted by default)")
            sp.add_argument("--no-keyboard", action="store_true",
                            help="print events only, do not press keys")
            sp.add_argument("--quiet", action="store_true", help="don't print events")

    kt = sub.add_parser("keytest", help="press the mapped keys once, to verify output")
    kt.add_argument("--dit", default="lctrl")
    kt.add_argument("--dah", default="rctrl")

    args = ap.parse_args()
    if args.cmd == "ports":
        list_ports()
    elif args.cmd == "detect":
        detect()
    elif args.cmd == "monitor":
        monitor(args.port, wkmode=args.wkmode)
    elif args.cmd == "keytest":
        keytest(args.dit, args.dah)
    elif args.cmd == "run":
        dit, dah = (args.dah, args.dit) if args.swap else (args.dit, args.dah)
        run(args.port, dit, dah, wkmode=args.wkmode,
            send_keys=not args.no_keyboard, quiet=args.quiet,
            debounce_ms=args.debounce, keyer_sidetone=args.keyer_sidetone)


if __name__ == "__main__":
    main()
