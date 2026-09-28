"""
PaddleCAT - WinKeyer CW Trainer -- GUI
===============================
A small app that does two things with a K1EL WinKeyer + paddle:

  Play Online  Translate your paddle into keystrokes so you can play browser
           CW games (Morse Invaders, Vail, VBand) -- either as raw passthrough
           (game does the iambic), or with the app's own iambic keyer (the
           app owns the speed, slider or live from the WinKeyer knob).

  Trainer  Show you a target CW line; you send it on your paddle; the
           WinKeyer's own decoder echoes back what it heard; the app
           scores your sending against the target. Drill categories cover
           CQ calls, contest/POTA exchanges, full rag chew, and common
           abbreviations.

The engine is reused from winkeyer_vail.py; the iambic keyer is iambic.py.
Drill content lives in drills.py. Your callsign + info live in
%APPDATA%\\WinKeyerKeyboard\\profile.json.
"""

import ctypes
import json
import os
import queue
import sys
import threading
import time
import webbrowser
from pathlib import Path

import serial
import serial.tools.list_ports

try:
    import customtkinter as ctk
except ImportError:
    raise SystemExit("customtkinter is required:\n"
                     "    python -m pip install customtkinter")

import winkeyer_vail as wk
from iambic import IambicKeyer
import drills
import invaders
import morse_decode
import qsos
import theme as T


# Keys offered in the UI:  display label -> winkeyer_vail.KEYMAP name
KEY_CHOICES = {
    "[  left bracket":   "lbracket",
    "]  right bracket":  "rbracket",
    "Left Ctrl":         "lctrl",
    "Right Ctrl":        "rctrl",
    "Space":             "space",
}
KEY_LABELS = list(KEY_CHOICES)

# Browser games the Play Online tab can set up in one click. Keys verified
# 2026-09-28: Vail from its source (static/scripts/inputs.mjs), VBand from its
# adapter convention (Left/Right Ctrl), Morse Invaders on hardware ([ and ]).
GAMES = {
    "Morse Invaders": {"url": "https://morseinvaders.com",
                       "dit": "lbracket", "dah": "rbracket",
                       "setting": "paddle / iambic"},
    "VBand":          {"url": "https://hamradio.solutions/vband/",
                       "dit": "lctrl", "dah": "rctrl",
                       "setting": "Paddle"},
    "Vail":           {"url": "https://vail.woozle.org",
                       "dit": "lbracket", "dah": "rbracket",
                       "setting": "Iambic"},
}
CUSTOM_GAME = "Custom"
GAME_LABELS = list(GAMES) + [CUSTOM_GAME]
PLAY_TAB = "Play Online"


def _label_for(keyname):
    for label, name in KEY_CHOICES.items():
        if name == keyname:
            return label
    return KEY_LABELS[0]


# ---------------------------------------------------------------------------
# Profile persistence  (~ %APPDATA%/WinKeyerKeyboard/profile.json )
# ---------------------------------------------------------------------------
def _resource(rel):
    """Path to a bundled file, both from source and inside the PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


APP_ICON = _resource(os.path.join("icon", "windows", "PaddleCAT-app.ico"))


def _profile_path():
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home()
    return root / "WinKeyerKeyboard" / "profile.json"


PROFILE_PATH = _profile_path()
PROFILE_KEYS = ["CALL", "NAME", "QTH", "STATE", "RIG", "ANT"]


def load_profile():
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        return {k: str(data.get(k, "")).strip() for k in PROFILE_KEYS}
    except Exception:
        return {}


def save_profile(profile):
    try:
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_PATH.write_text(
            json.dumps({k: profile.get(k, "") for k in PROFILE_KEYS}, indent=2),
            encoding="utf-8")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# App settings (separate from the CW profile): last port, mode, keys, etc.
# ---------------------------------------------------------------------------
SETTINGS_PATH = _profile_path().parent / "settings.json"


def load_settings():
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(d):
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(d, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


# ===========================================================================
# Engine
# ===========================================================================
class Controller:
    """Owns the serial link, the reader thread, and (in keyer mode) the
    software iambic keyer. The GUI sets the attributes below, then calls
    start()/stop(); wpm, mute, follow_knob and output_enabled may be changed
    while running."""

    def __init__(self, notify):
        self._notify = notify          # thread-safe sink for ("tag", ...) events
        self.ser = None
        self._reader = None
        self._keyer = None
        self._shadow = None            # paddle-mode keyer that only decodes
        self.decoder = None            # app-side Morse decoder
        self.running = False
        # settings (set by the GUI before start; live ones noted)
        self.mode = "keyer"            # "keyer" | "paddle"
        self.dit_key = "lbracket"
        self.dah_key = "rbracket"
        self.swap = False
        self.mute = True               # live
        self.wpm = 20                  # live
        self.follow_knob = False       # live
        self.output_enabled = True     # live -- false on Trainer tab
        self.sidetone_hz = 800         # live
        self.sidetone_vol = "high"     # live -- "low" or "high"
        # internal key state
        self._down = {"dit": False, "dah": False}
        self._lock_until = {"dit": 0.0, "dah": 0.0}
        self._last_busy = False

    # -- lifecycle ---------------------------------------------------------
    def start(self, port):
        """Open the WinKeyer and begin translating. Raises on failure."""
        ser = wk.open_wk(port)
        # paddle_echo=True makes the WinKeyer send the decoded ASCII of what
        # you key on the paddle -- that's what the Trainer scores against.
        ver = wk.init_wk(ser, wkmode="wk3",
                         mute_sidetone=self.mute, paddle_echo=True)
        # A known speed-pot range makes 'follow knob' map cleanly to WPM:
        # Setup Speed Pot, min 5 WPM, range 45 -> pot byte offset = WPM - 5.
        ser.write(bytes([0x05, 5, 45, 0]))
        time.sleep(0.05)
        # Set the WinKeyer's internal keyer speed (drives its echo decoder).
        # 0x02 <n>: n>0 sets a fixed WPM; n=0 means "use the speed pot".
        wpm_byte = 0 if self.follow_knob else max(5, min(99, int(self.wpm)))
        ser.write(bytes([0x02, wpm_byte]))
        time.sleep(0.05)
        # Sidetone frequency (WK3 mode: byte value = 62500 / freq Hz).
        hz = max(500, min(2000, int(self.sidetone_hz)))
        ser.write(bytes([0x01, max(16, min(125, 62500 // hz))]))
        time.sleep(0.05)
        # Sidetone volume (Admin 25, n=1 low / n=4 high -- two levels only).
        ser.write(bytes([0x00, 0x19, 0x01 if self.sidetone_vol == "low" else 0x04]))
        time.sleep(0.05)
        ser.reset_input_buffer()
        self.ser = ser
        self.running = True
        # App-side decoder: watches the keyed element stream to spot what
        # the WinKeyer's echo can't produce -- merged BK and the HH erase
        # signal -- and to measure the user's actual sending speed. Regular
        # scoring characters come from the WK's own echo, which samples the
        # levers directly in firmware and best matches what the user hears.
        self.decoder = morse_decode.MorseDecoder(
            wpm=self.wpm, notify=lambda ev: self._notify(("decode", ev)))
        if self.mode == "keyer":
            self._keyer = IambicKeyer(self._key_down, self._key_up, wpm=self.wpm)
            self._keyer.start()
        else:
            # Shadow keyer: reconstructs elements from the raw levers purely
            # for decoding -- it presses no keys.
            self._shadow = IambicKeyer(lambda: self._decode_key(True),
                                       lambda: self._decode_key(False),
                                       wpm=self.wpm)
            self._shadow.start()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        return ver

    def stop(self):
        self.running = False
        if self._reader:
            self._reader.join(timeout=1.5)
            self._reader = None
        if self._keyer:
            self._keyer.stop()
            self._keyer = None
        if self._shadow:
            self._shadow.stop()
            self._shadow = None
        self.decoder = None
        for name in ("dit", "dah"):              # never leave a key stuck down
            if self._down[name]:
                self._press(name, False)
        if self.ser:
            wk.close_wk(self.ser)
            self.ser = None

    # -- reader thread -----------------------------------------------------
    def _read_loop(self):
        while self.running:
            try:
                n = self.ser.in_waiting
            except Exception:
                if self.running:
                    self._notify(("lost",))
                return
            if not n:
                time.sleep(0.001)
                continue
            try:
                data = self.ser.read(n)
            except Exception:
                continue
            for b in data:
                self._handle(b)

    def _handle(self, b):
        if (b & 0xC0) == 0x80:                   # speed-pot byte
            if self.follow_knob:
                wpm = 5 + (b & 0x3F)
                if wpm != self.wpm:
                    self.set_wpm(wpm)
                    self._notify(("wpm", wpm))
        elif (b & 0xC0) == 0xC0:                 # status / paddle / pushbutton
            if b & 0x08:
                dit = bool(b & 0x01)
                dah = bool(b & 0x02)
                if self.swap:
                    dit, dah = dah, dit
                self._notify(("levers", dit, dah))
                if self.mode == "keyer" and self._keyer:
                    self._keyer.feed(dit, dah)
                else:
                    if self._shadow:
                        self._shadow.feed(dit, dah)
                    self._debounced("dit", dit)
                    self._debounced("dah", dah)
            else:                                # regular WK status byte
                busy = bool(b & 0x04)            # WK is keying out buffered text
                if busy != self._last_busy:
                    self._last_busy = busy
                    self._notify(("busy", busy))
        elif 0x20 <= b <= 0x7E:                  # printable ASCII = paddle echo
            self._notify(("echo", chr(b)))

    # -- paddle passthrough (debounced) ------------------------------------
    def _debounced(self, name, now):
        if self._down[name] == now:
            return
        t = time.perf_counter()
        if t < self._lock_until[name]:
            return                               # contact-bounce filter
        self._lock_until[name] = t + 0.025
        self._press(name, now)

    def set_keys(self, dit_key, dah_key, swap):
        """Change output keys live. Anything held is released first on its
        old key, so a key can't be left stuck down in the game."""
        for name in ("dit", "dah"):
            if self._down[name]:
                self._press(name, False)
        self.dit_key, self.dah_key, self.swap = dit_key, dah_key, swap

    def _press(self, name, down):
        self._down[name] = down
        if not self.output_enabled:
            return
        scan, ext = wk.KEYMAP[self.dit_key if name == "dit" else self.dah_key]
        wk.send_key(scan, ext, keyup=not down)

    # -- keyer-mode single-key output --------------------------------------
    def _key_down(self):
        self._down["dit"] = True
        self._decode_key(True)         # decode even when output is disabled
        if self.output_enabled:
            scan, ext = wk.KEYMAP[self.dit_key]
            wk.send_key(scan, ext, keyup=False)

    def _key_up(self):
        self._down["dit"] = False
        self._decode_key(False)
        if self.output_enabled:
            scan, ext = wk.KEYMAP[self.dit_key]
            wk.send_key(scan, ext, keyup=True)

    # -- app-side decoding --------------------------------------------------
    def _decode_key(self, down):
        d = self.decoder
        if d:
            d.key(down)

    def decoder_tick(self):
        """Flush trailing char/word gaps; called from the GUI poll loop."""
        d = self.decoder
        if d:
            d.tick()

    def decoder_reset(self):
        d = self.decoder
        if d:
            d.reset()

    # -- live settings -----------------------------------------------------
    def set_wpm(self, wpm):
        self.wpm = wpm
        if self._keyer:
            self._keyer.wpm = wpm
        if self._shadow:
            self._shadow.wpm = wpm
        if self.decoder:
            self.decoder.set_wpm(wpm)
        # Also drive the WinKeyer's internal speed (affects echo decoding,
        # autospace, and its own keying). When 'follow_knob' is on, the
        # WinKeyer is already taking speed from the pot -- don't override.
        if self.ser and not self.follow_knob:
            try:
                self.ser.write(bytes([0x02, max(5, min(99, int(wpm)))]))
            except Exception:
                pass

    def set_follow(self, follow):
        self.follow_knob = follow
        if self.ser:
            try:
                # 0x02 0  -> WinKeyer reads its speed pot live
                # 0x02 n  -> fixed WPM (override the pot)
                wpm_byte = 0 if follow else max(5, min(99, int(self.wpm)))
                self.ser.write(bytes([0x02, wpm_byte]))
            except Exception:
                pass

    def set_mute(self, mute):
        self.mute = mute
        if self.ser:
            try:
                self.ser.write(bytes([wk.CMD_SET_PINCFG,
                                      0x00 if mute else 0x0A]))
            except Exception:
                pass

    def set_sidetone_hz(self, hz):
        self.sidetone_hz = int(hz)
        if self.ser:
            v = max(16, min(125, 62500 // max(500, int(hz))))
            try:
                self.ser.write(bytes([0x01, v]))
            except Exception:
                pass

    def set_sidetone_vol(self, level):
        """level: 'low' or 'high'."""
        self.sidetone_vol = level
        if self.ser:
            try:
                self.ser.write(bytes([0x00, 0x19,
                                      0x01 if level == "low" else 0x04]))
            except Exception:
                pass

    # -- WinKeyer transmits Morse (QSO simulator) --------------------------
    def send_text(self, text):
        """Hand `text` to the WinKeyer's buffer; it keys it out at its current
        WPM. With sidetone unmuted, the user hears it; the BUSY status bit
        goes high while sending and clears when the buffer drains."""
        if not self.ser:
            return
        try:
            self.ser.write(text.encode("ascii", errors="ignore"))
        except Exception:
            pass

    def abort_send(self):
        """Clear the WinKeyer's buffer -- aborts any in-progress transmission."""
        if not self.ser:
            return
        try:
            self.ser.write(bytes([0x0A]))      # Clear Buffer
        except Exception:
            pass


# ===========================================================================
# GUI
# ===========================================================================
class App(ctk.CTk):
    POLL_MS = 33
    AUTO_NEXT_MS = 1200

    def __init__(self):
        try:    # own taskbar identity, so Windows shows our icon, not Python's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "PaddleCAT.WinKeyerCWTrainer")
        except Exception:
            pass
        T.apply()                       # must precede the root window
        super().__init__()
        self.title("PaddleCAT - WinKeyer CW Trainer")
        try:
            self.iconbitmap(APP_ICON)   # also stops CTk swapping in its own
        except Exception:
            pass
        self.geometry("560x800")
        self.resizable(False, False)

        self.events = queue.Queue()
        self.ctrl = Controller(self.events.put)
        self.running = False
        self.busy = False
        self.profile = load_profile()
        self.settings = load_settings()

        # Trainer state
        self.t_target = ""
        self.t_template = None
        self.t_buffer = ""
        self.t_correct = 0
        self.t_attempted = 0
        self.t_streak = 0
        self._auto_next_id = None

        self._build_header()

        # Tabs
        self.tabs = ctk.CTkTabview(self, height=660)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(0, 0))
        self.tabs.add(PLAY_TAB)
        self.tabs.add("Trainer")
        self.tabs.add("QSO Sim")
        self.tabs.add("Invaders")
        self.tabs.add("Profile")
        self.tabs.configure(command=self._on_tab_change)
        self._prev_tab = PLAY_TAB

        self._build_play(self.tabs.tab(PLAY_TAB))
        self._build_trainer(self.tabs.tab("Trainer"))
        self._build_qso(self.tabs.tab("QSO Sim"))
        self.game = invaders.InvadersGame(
            self.tabs.tab("Invaders"),
            profile_getter=lambda: self.profile,
            is_ready=lambda: self.running,
            get_wpm=lambda: int(self.speed_var.get()),
            get_best=lambda mix: self.settings.get("invaders_best",
                                                   {}).get(mix, 0),
            set_best=self._set_invaders_best,
            initial_mix=self.settings.get("inv_mix", "Ham Mix"))
        self._build_profile(self.tabs.tab("Profile"))

        # Shared status line at bottom
        self.status = ctk.CTkLabel(self, text="Idle.", text_color=T.MUTED,
                                   wraplength=480, justify="left")
        self.status.pack(side="bottom", fill="x", padx=16, pady=(4, 10))

        self._refresh_ports()
        self._auto_select_port()
        self._sync_enabled()
        self._next_prompt(reset_score=True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(self.POLL_MS, self._poll)

    # -- HEADER ------------------------------------------------------------
    def _build_header(self):
        """PADDLE / CAT wordmark, plus the WinKeyer connection -- shared by
        every tab, so it lives up here rather than on any one of them."""
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(10, 4))
        mark = ctk.CTkFont(family=T.HEADING_FAMILY, size=26, weight="bold")
        ctk.CTkLabel(head, text="PADDLE", font=mark,
                     text_color=T.MAGENTA).pack(side="left")
        ctk.CTkLabel(head, text="CAT", font=mark,
                     text_color=T.LIME).pack(side="left", padx=(6, 0))

        self.action_btn = ctk.CTkButton(
            head, text="Connect", width=104,
            font=ctk.CTkFont(weight="bold"), command=self._on_action)
        self.action_btn.pack(side="right")
        self.detect_btn = ctk.CTkButton(head, text="Detect", width=64,
                                        command=self._on_detect)
        self.detect_btn.pack(side="right", padx=(0, 6))
        self.port_var = ctk.StringVar(value="")
        self.port_menu = ctk.CTkOptionMenu(head, variable=self.port_var,
                                           values=["(none)"], width=112)
        self.port_menu.pack(side="right", padx=(0, 6))

    # -- PLAY ONLINE TAB ---------------------------------------------------
    def _build_play(self, p):
        pad = {"padx": 14, "pady": (10, 0)}

        ctk.CTkLabel(p, text="Use your paddle in browser CW games",
                     font=ctk.CTkFont(family=T.HEADING_FAMILY, size=18,
                                      weight="bold"),
                     anchor="w").pack(fill="x", padx=14, pady=(12, 0))

        # Game picker: one click sets the right mode and keys.
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", **pad)
        ctk.CTkLabel(row, text="Game").pack(side="left")
        self.game_var = ctk.StringVar(value=self._initial_game())
        self.game_menu = ctk.CTkOptionMenu(row, variable=self.game_var,
                                           values=GAME_LABELS, width=170,
                                           command=self._on_game)
        self.game_menu.pack(side="left", padx=8)
        self.open_btn = ctk.CTkButton(row, text="Open game ↗", width=120,
                                      command=self._open_game)
        self.open_btn.pack(side="left")

        self.play_steps = ctk.CTkLabel(p, text="", anchor="w", justify="left",
                                       wraplength=500, text_color=T.TEXT)
        self.play_steps.pack(fill="x", padx=14, pady=(12, 0))

        # Live state: is output on, and are the levers arriving?
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(12, 0))
        self.play_state = ctk.CTkLabel(row, text="")
        self.play_state.pack(side="left")
        self.dah_dot = ctk.CTkLabel(row, text="● dah", text_color=T.FAINT)
        self.dah_dot.pack(side="right")
        self.dit_dot = ctk.CTkLabel(row, text="● dit", text_color=T.FAINT)
        self.dit_dot.pack(side="right", padx=(0, 8))

        # Everything below is for tinkerers; the game picker covers most people.
        self.adv_btn = ctk.CTkButton(
            p, text="▸  Advanced settings", anchor="w", fg_color="transparent",
            border_width=0, hover_color=T.RAISED, text_color=T.MAGENTA_SOFT,
            command=self._toggle_advanced)
        self.adv_btn.pack(fill="x", padx=8, pady=(14, 0))
        holder = ctk.CTkFrame(p, fg_color="transparent")
        holder.pack(fill="x")
        a = self.adv = ctk.CTkFrame(holder, fg_color="transparent")

        # Mode
        row = ctk.CTkFrame(a, fg_color="transparent")
        row.pack(fill="x", **pad)
        ctk.CTkLabel(row, text="Mode").pack(side="left")
        self.mode_var = ctk.StringVar(value=self.settings.get("mode", "Paddle"))
        self.mode_seg = ctk.CTkSegmentedButton(
            row, values=["Paddle", "Keyer"], variable=self.mode_var,
            command=lambda _=None: self._on_mode())
        self.mode_seg.pack(side="left", padx=8)

        self.hint = ctk.CTkLabel(a, text="", text_color=T.MAGENTA_SOFT,
                                 wraplength=480, justify="left")
        self.hint.pack(fill="x", padx=14, pady=(4, 0))

        # Speed
        box = ctk.CTkFrame(a)
        box.pack(fill="x", **pad)
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(8, 0))
        ctk.CTkLabel(head, text="Speed").pack(side="left")
        wpm0 = int(self.settings.get("wpm", 20))
        self.speed_lbl = ctk.CTkLabel(head, text=f"{wpm0} WPM",
                                      font=ctk.CTkFont(weight="bold"))
        self.speed_lbl.pack(side="right")
        self.speed_var = ctk.IntVar(value=wpm0)
        self.speed = ctk.CTkSlider(box, from_=5, to=45, number_of_steps=40,
                                   variable=self.speed_var,
                                   command=self._on_speed)
        self.speed.pack(fill="x", padx=10, pady=4)
        self.follow_var = ctk.BooleanVar(
            value=bool(self.settings.get("follow_knob", False)))
        self.follow_chk = ctk.CTkCheckBox(box, text="Follow WinKeyer speed knob",
                                          variable=self.follow_var,
                                          command=self._on_follow)
        self.follow_chk.pack(anchor="w", padx=10, pady=(0, 8))

        # Keys
        box = ctk.CTkFrame(a)
        box.pack(fill="x", **pad)
        r1 = ctk.CTkFrame(box, fg_color="transparent")
        r1.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(r1, text="Dit key", width=70, anchor="w").pack(side="left")
        self.dit_var = ctk.StringVar(
            value=_label_for(self.settings.get("dit_key", "lbracket")))
        self.dit_menu = ctk.CTkOptionMenu(r1, variable=self.dit_var,
                                          values=KEY_LABELS, width=200,
                                          command=lambda _=None: self._on_keys())
        self.dit_menu.pack(side="left")
        r2 = ctk.CTkFrame(box, fg_color="transparent")
        r2.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(r2, text="Dah key", width=70, anchor="w").pack(side="left")
        self.dah_var = ctk.StringVar(
            value=_label_for(self.settings.get("dah_key", "rbracket")))
        self.dah_menu = ctk.CTkOptionMenu(r2, variable=self.dah_var,
                                          values=KEY_LABELS, width=200,
                                          command=lambda _=None: self._on_keys())
        self.dah_menu.pack(side="left")
        self.swap_var = ctk.BooleanVar(
            value=bool(self.settings.get("swap", False)))
        self.swap_chk = ctk.CTkCheckBox(box, text="Swap dit / dah",
                                        variable=self.swap_var,
                                        command=self._on_keys)
        self.swap_chk.pack(anchor="w", padx=10, pady=(2, 8))

        # Mute + sidetone
        self.mute_var = ctk.BooleanVar(
            value=bool(self.settings.get("mute", True)))
        self.mute_chk = ctk.CTkCheckBox(a, text="Mute WinKeyer sidetone",
                                        variable=self.mute_var,
                                        command=self._on_mute)
        self.mute_chk.pack(anchor="w", padx=14, pady=(12, 0))

        side = ctk.CTkFrame(a)
        side.pack(fill="x", padx=14, pady=(8, 0))
        trow = ctk.CTkFrame(side, fg_color="transparent")
        trow.pack(fill="x", padx=10, pady=(8, 0))
        ctk.CTkLabel(trow, text="Tone", width=70, anchor="w").pack(side="left")
        hz0 = int(self.settings.get("sidetone_hz", 800))
        self.tone_var = ctk.IntVar(value=hz0)
        self.tone_lbl = ctk.CTkLabel(trow, text=f"{hz0} Hz",
                                     font=ctk.CTkFont(weight="bold"))
        self.tone_lbl.pack(side="right")
        self.tone_slider = ctk.CTkSlider(side, from_=500, to=2000,
                                         number_of_steps=30,
                                         variable=self.tone_var,
                                         command=self._on_tone)
        self.tone_slider.pack(fill="x", padx=10, pady=(2, 6))
        vrow = ctk.CTkFrame(side, fg_color="transparent")
        vrow.pack(fill="x", padx=10, pady=(0, 8))
        ctk.CTkLabel(vrow, text="Volume", width=70, anchor="w").pack(side="left")
        self.vol_var = ctk.StringVar(
            value="Low" if self.settings.get("sidetone_vol", "high") == "low"
                  else "High")
        self.vol_seg = ctk.CTkSegmentedButton(
            vrow, values=["Low", "High"], variable=self.vol_var,
            command=self._on_vol)
        self.vol_seg.pack(side="left", padx=8)

        if self.settings.get("show_advanced"):
            self._toggle_advanced()

    def _initial_game(self):
        saved = self.settings.get("game")
        if saved in GAME_LABELS:
            return saved
        return "Morse Invaders"   # also matches the old [ ] default

    def _toggle_advanced(self):
        if self.adv.winfo_manager():
            self.adv.pack_forget()
            self.adv_btn.configure(text="▸  Advanced settings")
        else:
            self.adv.pack(fill="x")
            self.adv_btn.configure(text="▾  Advanced settings")

    def _on_game(self, choice=None):
        game = GAMES.get(self.game_var.get())
        if game:
            self.dit_var.set(_label_for(game["dit"]))
            self.dah_var.set(_label_for(game["dah"]))
            if not (self.running or self.busy):
                self.mode_var.set("Paddle")
            elif self.mode_var.get() != "Paddle":
                self._set_status("Disconnect and reconnect to switch to "
                                 "Paddle mode for this game.", T.WARN)
            self._apply_keys()
        self._sync_enabled()
        self._save_state()

    def _on_keys(self):
        """Keys edited by hand: apply live, and show which game (if any)
        they now match -- preferring the one already picked."""
        dit = KEY_CHOICES[self.dit_var.get()]
        dah = KEY_CHOICES[self.dah_var.get()]
        matches = [name for name, g in GAMES.items()
                   if g["dit"] == dit and g["dah"] == dah]
        if self.game_var.get() not in matches:
            self.game_var.set(matches[0] if matches else CUSTOM_GAME)
        self._apply_keys()
        self._update_play_steps()
        self._save_state()

    def _apply_keys(self):
        self.ctrl.set_keys(KEY_CHOICES[self.dit_var.get()],
                           KEY_CHOICES[self.dah_var.get()],
                           bool(self.swap_var.get()))

    def _on_mode(self):
        self._sync_enabled()
        self._save_state()

    def _open_game(self):
        game = GAMES.get(self.game_var.get())
        if game:
            webbrowser.open(game["url"])

    def _update_play_steps(self):
        name = self.game_var.get()
        keyer = self.mode_var.get() == "Keyer"
        game = GAMES.get(name)
        dit = self.dit_var.get().split("  ")[0]
        dah = self.dah_var.get().split("  ")[0]
        keys = f"the {dit} key" if keyer else f"the {dit} and {dah} keys"
        setting = ("straight key" if keyer else
                   game["setting"] if game else "paddle / iambic")
        where = (f"Click Open game, then set {name}'s" if game
                 else "Open your game and set its")
        self.play_steps.configure(text=(
            "1.  Click Connect at the top.\n"
            f"2.  {where} input to {setting}, using {keys}.\n"
            "3.  Click into the game and send with your paddle."))
        self.open_btn.configure(state="normal" if game else "disabled")
        if self.running:
            self.play_state.configure(
                text="● Keyboard output ON while this tab is open",
                text_color=T.LIME)
        else:
            self.play_state.configure(text="○ Not connected",
                                      text_color=T.MUTED)

    # -- TRAINER TAB -------------------------------------------------------
    def _build_trainer(self, p):
        # Mode + Adaptive speed row
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(row, text="Mode").pack(side="left")
        self.t_mode_var = ctk.StringVar(value=self.settings.get("t_mode", "Drill"))
        self.t_mode_seg = ctk.CTkSegmentedButton(
            row, values=["Drill", "Sprint"], variable=self.t_mode_var,
            command=lambda _=None: (self._on_trainer_mode(),
                                    self._save_state()))
        self.t_mode_seg.pack(side="left", padx=8)
        self.t_adaptive_var = ctk.BooleanVar(
            value=bool(self.settings.get("t_adaptive", False)))
        self.t_adaptive_chk = ctk.CTkCheckBox(
            row, text="Adaptive WPM", variable=self.t_adaptive_var,
            command=self._save_state)
        self.t_adaptive_chk.pack(side="right")

        # Category + score
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(4, 4))
        ctk.CTkLabel(row, text="Drill").pack(side="left")
        self.t_cat_var = ctk.StringVar(
            value=self.settings.get("t_category", next(iter(drills.CATEGORIES))))
        self.t_cat = ctk.CTkOptionMenu(
            row, variable=self.t_cat_var,
            values=list(drills.CATEGORIES), width=220,
            command=lambda _=None: self._on_category())
        self.t_cat.pack(side="left", padx=8)
        self.t_score_lbl = ctk.CTkLabel(row, text="0 / 0   streak 0",
                                        text_color=T.TEXT_SOFT)
        self.t_score_lbl.pack(side="right")

        # Sprint row (button + timer / best)
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(2, 4))
        self.t_sprint_btn = ctk.CTkButton(row, text="Start 60s Sprint",
                                          width=170,
                                          command=self._on_sprint_action)
        self.t_sprint_btn.pack(side="left")
        self.t_timer_lbl = ctk.CTkLabel(row, text="", text_color=T.TEXT_SOFT)
        self.t_timer_lbl.pack(side="right")

        # Sprint state
        self.t_sprint_running = False
        self.t_sprint_count = 0
        self.t_sprint_end_time = 0.0
        self._sprint_tick_id = None

        # Target -- the most important text in the app: big, auto-scaled to
        # the line length (see _trainer_font_size).
        ctk.CTkLabel(p, text="Send:", anchor="w",
                     text_color=T.TEXT_SOFT).pack(fill="x", padx=14, pady=(10, 0))
        self.t_target_font = ctk.CTkFont(family="Consolas", size=34,
                                         weight="bold")
        # A read-only textbox rather than a label so the matched part of the
        # line can light up lime as you send it.
        self.t_target_box = ctk.CTkTextbox(
            p, font=self.t_target_font, wrap="word", fg_color="transparent",
            border_width=0, border_spacing=0, corner_radius=0,
            activate_scrollbars=False, text_color=T.TEXT, height=44)
        self.t_target_box.pack(fill="x", padx=14)
        self.t_target_box.tag_config("hit", foreground=T.LIME)
        self.t_target_box._textbox.configure(
            cursor="arrow", takefocus=0, insertwidth=0, padx=0, pady=0)
        self.t_target_box.configure(state="disabled")
        self.t_hit = 0                  # chars of t_target matched so far

        # Your copy (RX) -- same size as the target
        ctk.CTkLabel(p, text="RX (your copy):", anchor="w",
                     text_color=T.TEXT_SOFT).pack(fill="x", padx=14, pady=(14, 0))
        self.t_copy_font = ctk.CTkFont(family="Consolas", size=34)
        self.t_copy_lbl = ctk.CTkLabel(
            p, text="", anchor="w", justify="left", wraplength=500,
            text_color=T.TEXT, font=self.t_copy_font)
        self.t_copy_lbl.pack(fill="x", padx=14)

        # How much of the target the buffer currently matches
        self.t_progress = ctk.CTkProgressBar(p, height=6)
        self.t_progress.set(0)
        self.t_progress.pack(fill="x", padx=14, pady=(10, 0))

        # Status
        self.t_status_lbl = ctk.CTkLabel(p, text="▶  Send the line above.",
                                         text_color=T.TEXT_SOFT,
                                         font=ctk.CTkFont(size=14))
        self.t_status_lbl.pack(fill="x", padx=14, pady=(14, 4))

        # Buttons
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(8, 6))
        self.t_retry_btn = ctk.CTkButton(row, text="Retry", width=110,
                                         command=self._trainer_retry)
        self.t_retry_btn.pack(side="left")
        self.t_skip_btn = ctk.CTkButton(row, text="Skip →", width=110,
                                        command=self._trainer_skip)
        self.t_skip_btn.pack(side="left", padx=(8, 0))

        # Connection hint
        self.t_conn_hint = ctk.CTkLabel(
            p, text="(Click Connect at the top to begin drilling.)",
            text_color=T.WARN, wraplength=480, justify="left")
        self.t_conn_hint.pack(fill="x", padx=14, pady=(8, 0))
        ctk.CTkLabel(
            p, text="Tips: uncheck 'Mute WinKeyer sidetone' (Play Online → Advanced) "
                    "so you can hear yourself. All prosigns count merged or "
                    "letter-spaced — including BK (-...-.-). Made a mistake? "
                    "Send HH (8 dits) to wipe the attempt, or just keep "
                    "going: it counts when the END of what you sent matches "
                    "the target.",
            text_color=T.DIM, wraplength=480, justify="left"
        ).pack(fill="x", padx=14, pady=(2, 0))

        self._on_trainer_mode()             # set initial enable state

    # -- QSO SIMULATOR TAB -------------------------------------------------
    def _build_qso(self, p):
        # Scenario + Start/Stop
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(row, text="QSO").pack(side="left")
        self.q_scn_var = ctk.StringVar(
            value=self.settings.get("q_scenario", next(iter(qsos.SCENARIOS))))
        self.q_scn = ctk.CTkOptionMenu(
            row, variable=self.q_scn_var,
            values=list(qsos.SCENARIOS), width=190,
            command=lambda _=None: self._save_state())
        self.q_scn.pack(side="left", padx=8)
        self.q_start_btn = ctk.CTkButton(row, text="Start QSO", width=110,
                                         command=self._qso_action)
        self.q_start_btn.pack(side="left", padx=(4, 0))

        # Status
        self.q_status_lbl = ctk.CTkLabel(
            p, text="Press Start to begin a QSO.", text_color=T.TEXT_SOFT,
            wraplength=480, justify="left",
            font=ctk.CTkFont(size=14))
        self.q_status_lbl.pack(fill="x", padx=14, pady=(8, 4))

        # Conversation log
        self.q_log = ctk.CTkTextbox(
            p, height=260,
            font=ctk.CTkFont(family="Consolas", size=15),
            wrap="word")
        self.q_log.pack(fill="x", padx=14, pady=(4, 6))
        self.q_log.configure(state="disabled")

        # Skip + mirrored mute checkbox
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(2, 4))
        self.q_skip_btn = ctk.CTkButton(row, text="Skip step", width=110,
                                        command=self._qso_skip)
        self.q_skip_btn.pack(side="left")
        ctk.CTkCheckBox(row, text="Mute WK sidetone",
                        variable=self.mute_var, command=self._on_mute
                        ).pack(side="right")

        # Connection hint
        ctk.CTkLabel(
            p, text="(Click Connect at the top first, and UNCHECK Mute "
                    "above so you can hear the other op.)",
            text_color=T.WARN, wraplength=480, justify="left"
        ).pack(fill="x", padx=14, pady=(4, 0))

        # State
        self.q_state = "idle"
        self.q_steps = []
        self.q_idx = 0
        self.q_buffer = ""
        self.q_theircall = ""
        self.q_seen_busy = False

    # -- PROFILE TAB -------------------------------------------------------
    def _build_profile(self, p):
        ctk.CTkLabel(
            p, text="Your details fill into the drills "
                    "(e.g. {CALL}, {NAME}, {QTH}).",
            text_color=T.TEXT_SOFT, wraplength=480, justify="left"
        ).pack(fill="x", padx=14, pady=(14, 6))

        self.profile_vars = {}
        fields = [
            ("CALL",  "Callsign",  "K1ABC"),
            ("NAME",  "Name",      "ALEX"),
            ("QTH",   "QTH",       "BOSTON"),
            ("STATE", "State",     "MA"),
            ("RIG",   "Rig",       "FLEX 8600"),
            ("ANT",   "Antenna",   "DIPOLE"),
        ]
        for key, label, placeholder in fields:
            row = ctk.CTkFrame(p, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=4)
            ctk.CTkLabel(row, text=label, width=90, anchor="w").pack(side="left")
            var = ctk.StringVar(value=self.profile.get(key, ""))
            self.profile_vars[key] = var
            ctk.CTkEntry(row, textvariable=var, width=300,
                         placeholder_text=placeholder).pack(side="left")

        btn = ctk.CTkButton(p, text="Save profile", command=self._on_save_profile)
        btn.pack(anchor="w", padx=14, pady=(14, 4))
        self.profile_status = ctk.CTkLabel(p, text="", text_color=T.MUTED)
        self.profile_status.pack(anchor="w", padx=14)

        ctk.CTkLabel(
            p, text=f"Saved to: {PROFILE_PATH}",
            text_color=T.DIM, wraplength=480, justify="left"
        ).pack(fill="x", padx=14, pady=(20, 0))

    def _on_save_profile(self):
        self.profile = {k: v.get().strip().upper()
                        for k, v in self.profile_vars.items()}
        ok = save_profile(self.profile)
        self.profile_status.configure(
            text="Saved." if ok else "Could not save profile.",
            text_color=T.LIME if ok else T.DANGER)
        # Re-render the current trainer prompt with new profile values.
        if self.t_template:
            self.t_target = drills.render(self.t_template, self.profile)
            self.t_buffer = ""
            self.t_hit = 0
            self._update_trainer_view()

    # -- enable/disable logic ---------------------------------------------
    def _sync_enabled(self):
        keyer = self.mode_var.get() == "Keyer"
        self.hint.configure(text=(
            "Keyer mode: the app does the iambic. "
            "Set the game to STRAIGHT KEY input on the Dit key."
            if keyer else
            "Paddle mode: the game does the iambic. "
            "Set the game to PADDLE / iambic input on the Dit & Dah keys."))

        # Port and mode are fixed while connected; keys apply live.
        locked = "disabled" if (self.running or self.busy) else "normal"
        for w in (self.port_menu, self.detect_btn, self.mode_seg):
            w.configure(state=locked)
        self.dah_menu.configure(state="disabled" if keyer else "normal")
        # Speed drives the WinKeyer's internal keyer (and the app's iambic, in
        # Keyer mode) -- useful in both modes. Slider is disabled only when
        # the WinKeyer knob is the source.
        self.speed.configure(
            state="disabled" if self.follow_var.get() else "normal")
        self.follow_chk.configure(state="normal")
        self.action_btn.configure(
            state="disabled" if self.busy else "normal",
            text="Disconnect" if self.running else "Connect",
            fg_color=T.STOP if self.running else T.GO)
        self._update_play_steps()
        # Trainer hint visibility
        if self.running:
            self.t_conn_hint.configure(
                text="Drilling on " + (self.port_var.get() or "WinKeyer"),
                text_color=T.LIME)
        else:
            self.t_conn_hint.configure(
                text="(Click Connect at the top to begin drilling.)",
                text_color=T.WARN)

    # -- tabs --------------------------------------------------------------
    def _on_tab_change(self):
        tab = self.tabs.get()
        # Abort any QSO if the user is leaving the QSO Sim tab.
        if self._prev_tab == "QSO Sim" and tab != "QSO Sim":
            self._qso_stop(silent=True)
        # Pause the game when leaving its tab.
        if self._prev_tab == "Invaders" and tab != "Invaders":
            self.game.on_tab_left()
        # Keyboard output goes off everywhere but the Play Online tab so the
        # synthesized keystrokes don't get typed into our own window.
        self.ctrl.output_enabled = (tab == PLAY_TAB)
        if self.running and self._prev_tab == PLAY_TAB and tab != PLAY_TAB:
            self._set_status("Keyboard output paused. Go back to Play Online "
                             "to use your paddle in games.", T.WARN)
        elif self.running and tab == PLAY_TAB:
            self._set_status("Keyboard output on. Click into your game and "
                             "send.", T.LIME)
        if tab == "Trainer":
            self.ctrl.decoder_reset()
        self._prev_tab = tab

    # -- event poll --------------------------------------------------------
    def _poll(self):
        try:
            while True:
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
        if self.running:
            self.ctrl.decoder_tick()
        self.after(self.POLL_MS, self._poll)

    def _handle_event(self, ev):
        tag = ev[0]
        if tag == "levers":
            _, dit, dah = ev
            self.dit_dot.configure(text_color=T.LIME if dit else T.FAINT)
            self.dah_dot.configure(text_color=T.LIME if dah else T.FAINT)
        elif tag == "echo":
            # The WinKeyer's own decode -- the primary character source for
            # Trainer, QSO Sim and Invaders. Its firmware samples the levers
            # directly (no USB latency/jitter), so it tracks the operator's
            # fist better than any app-side reconstruction can.
            if self.running:
                tab = self.tabs.get()
                if tab == "QSO Sim":
                    self._qso_handle_echo(ev[1])
                elif tab == "Trainer":
                    self._echo_char(ev[1])
                elif tab == "Invaders":
                    self.game.feed_char(ev[1])
        elif tag == "decode":
            # App-side decoder events supplement the WK echo with the two
            # things the WK cannot produce: merged BK and the HH erase.
            if self.running:
                tab = self.tabs.get()
                if tab == "Trainer":
                    self._trainer_decode(ev[1])
                elif tab == "Invaders":
                    self._invaders_decode(ev[1])
        elif tag == "busy":
            if self.running and self.tabs.get() == "QSO Sim":
                self._qso_handle_busy(ev[1])
        elif tag == "wpm":
            self.speed_var.set(ev[1])
            self.speed_lbl.configure(text=f"{ev[1]} WPM")
        elif tag == "started":
            self.running, self.busy = True, False
            self._sync_enabled()
            self._set_status(f"Running on {ev[1]}.", T.LIME)
            self._save_state()                # remember a port that actually worked
        elif tag == "startfail":
            self.running, self.busy = False, False
            self._sync_enabled()
            self._set_status(ev[1], T.DANGER)
        elif tag == "stopped":
            self.running, self.busy = False, False
            self._sync_enabled()
            self._set_status("Stopped — WinKeyer released.", T.MUTED)
            self.game.on_bridge_stopped()
        elif tag == "detected":
            self._detect_done(ev[1], ev[2])
        elif tag == "lost":
            if self.running and not self.busy:
                self.game.on_bridge_stopped()
                self._begin_stop()

    # -- bridge actions ---------------------------------------------------
    def _on_action(self):
        if self.busy:
            return
        if self.running:
            self._begin_stop()
        else:
            self._begin_start()

    def _begin_start(self):
        port = self.port_var.get()
        if not port or port.startswith("("):
            self._set_status("Pick a COM port first (try Detect).", T.DANGER)
            return
        c = self.ctrl
        c.mode = "keyer" if self.mode_var.get() == "Keyer" else "paddle"
        c.dit_key = KEY_CHOICES[self.dit_var.get()]
        c.dah_key = KEY_CHOICES[self.dah_var.get()]
        c.swap = self.swap_var.get()
        c.mute = self.mute_var.get()
        c.wpm = int(self.speed_var.get())
        c.follow_knob = self.follow_var.get()
        c.output_enabled = (self.tabs.get() == PLAY_TAB)
        c.sidetone_hz = int(self.tone_var.get())
        c.sidetone_vol = self.vol_var.get().lower()
        self.busy = True
        self._sync_enabled()
        self._set_status(f"Connecting to {port}…", T.MUTED)
        threading.Thread(target=self._start_worker, args=(port,),
                         daemon=True).start()

    def _start_worker(self, port):
        try:
            self.ctrl.start(port)
            self.events.put(("started", port))
        except serial.SerialException as exc:
            msg = ("Port is in use — close other software that has it "
                   "(a logger, rig control)." if "Access is denied" in str(exc)
                   else f"Could not open {port}: {exc}")
            self.events.put(("startfail", msg))
        except Exception as exc:                              # noqa: BLE001
            self.events.put(("startfail", f"Error: {exc}"))

    def _begin_stop(self):
        self.busy = True
        self._sync_enabled()
        self._set_status("Stopping…", T.MUTED)
        threading.Thread(target=self._stop_worker, daemon=True).start()

    def _stop_worker(self):
        try:
            self.ctrl.stop()
        finally:
            self.events.put(("stopped",))

    def _on_detect(self):
        if self.running or self.busy:
            return
        self.detect_btn.configure(state="disabled")
        self._set_status("Detecting WinKeyer…", T.MUTED)
        threading.Thread(target=self._detect_worker, daemon=True).start()

    def _detect_worker(self):
        found = None
        ports = [p.device for p in serial.tools.list_ports.comports()]
        for dev in ports:
            ver, _ = wk.probe(dev)
            if ver is not None and 0x09 <= ver <= 0x40:
                found = dev
                break
        self.events.put(("detected", found, ports))

    def _detect_done(self, found, ports):
        self._set_ports(ports)
        self.detect_btn.configure(state="normal")
        if found:
            self.port_var.set(found)
            self._set_status(f"Found a WinKeyer on {found}.", T.LIME)
        else:
            self._set_status("No WinKeyer found. Plugged in / free?", T.DANGER)

    # -- live setting handlers --------------------------------------------
    def _on_speed(self, value):
        wpm = int(round(float(value)))
        self.speed_lbl.configure(text=f"{wpm} WPM")
        self.ctrl.set_wpm(wpm)

    def _on_follow(self):
        self.ctrl.set_follow(self.follow_var.get())
        self._sync_enabled()

    def _on_mute(self):
        self.ctrl.set_mute(self.mute_var.get())

    def _on_tone(self, value):
        hz = max(500, min(2000, int(round(float(value) / 50)) * 50))  # snap to 50 Hz
        self.tone_var.set(hz)
        self.tone_lbl.configure(text=f"{hz} Hz")
        self.ctrl.set_sidetone_hz(hz)

    def _on_vol(self, value):
        self.ctrl.set_sidetone_vol(value.lower())

    # -- TRAINER logic ----------------------------------------------------
    def _next_prompt(self, reset_score=False):
        if self._auto_next_id is not None:
            try:
                self.after_cancel(self._auto_next_id)
            except Exception:
                pass
            self._auto_next_id = None
        cat = self.t_cat_var.get()
        target, template = drills.pick(cat, self.profile)
        self.t_template = template
        self.t_target = target
        self.t_buffer = ""
        self.t_hit = 0
        self.ctrl.decoder_reset()
        if reset_score:
            self.t_correct = self.t_attempted = self.t_streak = 0
        self.t_status_lbl.configure(text="▶  Send the line above.",
                                    text_color=T.TEXT_SOFT)
        self.t_progress.set(0)
        self._update_trainer_view()

    def _trainer_retry(self):
        if self._auto_next_id is not None:
            try:
                self.after_cancel(self._auto_next_id)
            except Exception:
                pass
            self._auto_next_id = None
        self.t_buffer = ""
        self.t_hit = 0
        self.ctrl.decoder_reset()
        self.t_status_lbl.configure(text="▶  Send the line above.",
                                    text_color=T.TEXT_SOFT)
        self.t_progress.set(0)
        self._update_trainer_view()

    def _trainer_skip(self):
        self.t_attempted += 1
        self.t_streak = 0
        # Adaptive WPM: ease off after a skip.
        if (self.t_mode_var.get() == "Drill"
                and self.t_adaptive_var.get()):
            new_wpm = max(5, int(self.speed_var.get()) - 1)
            if new_wpm != int(self.speed_var.get()):
                self.speed_var.set(new_wpm)
                self.speed_lbl.configure(text=f"{new_wpm} WPM")
                self.ctrl.set_wpm(new_wpm)
        self._next_prompt()

    # -- Sprint mode -------------------------------------------------------
    def _on_trainer_mode(self):
        sprint = self.t_mode_var.get() == "Sprint"
        self.t_sprint_btn.configure(state="normal" if sprint else "disabled")
        self.t_adaptive_chk.configure(state="disabled" if sprint else "normal")
        self._refresh_sprint_label()

    def _on_category(self):
        # Stop any sprint in progress when the category changes.
        if self.t_sprint_running:
            self._sprint_stop(silent=True)
        self._refresh_sprint_label()
        self._next_prompt(reset_score=True)
        self._save_state()

    def _refresh_sprint_label(self):
        if self.t_mode_var.get() != "Sprint" or self.t_sprint_running:
            if not self.t_sprint_running:
                self.t_timer_lbl.configure(text="")
            return
        cat = self.t_cat_var.get()
        best = self.settings.get("sprint_best", {}).get(cat, 0)
        self.t_timer_lbl.configure(text=f"Best: {best}" if best else "")

    def _on_sprint_action(self):
        if self.t_sprint_running:
            self._sprint_stop()
        else:
            self._sprint_start()

    def _sprint_start(self):
        if not self.running:
            self._set_status("Click Connect at the top first.", T.DANGER)
            return
        self.t_sprint_count = 0
        self.t_sprint_end_time = time.perf_counter() + 60.0
        self.t_sprint_running = True
        self.t_sprint_btn.configure(text="Stop Sprint",
                                    fg_color=T.STOP)
        self._next_prompt(reset_score=True)
        self._sprint_tick()

    def _sprint_tick(self):
        if not self.t_sprint_running:
            return
        left = self.t_sprint_end_time - time.perf_counter()
        if left <= 0:
            self._sprint_finish()
            return
        self.t_timer_lbl.configure(
            text=f"⏱ {int(left):d}s    ✓ {self.t_sprint_count}")
        self._sprint_tick_id = self.after(100, self._sprint_tick)

    def _sprint_stop(self, silent=False):
        if not self.t_sprint_running:
            return
        self.t_sprint_running = False
        if self._sprint_tick_id is not None:
            try:
                self.after_cancel(self._sprint_tick_id)
            except Exception:
                pass
            self._sprint_tick_id = None
        self.t_sprint_btn.configure(text="Start 60s Sprint",
                                    fg_color=T.GO)
        if not silent:
            self.t_status_lbl.configure(text="Sprint stopped.",
                                        text_color=T.MUTED)
        self._refresh_sprint_label()

    def _sprint_finish(self):
        self.t_sprint_running = False
        self._sprint_tick_id = None
        self.t_sprint_btn.configure(text="Start 60s Sprint",
                                    fg_color=T.GO)
        cat = self.t_cat_var.get()
        best_dict = dict(self.settings.get("sprint_best", {}))
        best = best_dict.get(cat, 0)
        if self.t_sprint_count > best:
            best_dict[cat] = self.t_sprint_count
            self.settings["sprint_best"] = best_dict
            save_settings(self.settings)
            msg = f"⏱ Time! {self.t_sprint_count} correct — NEW BEST!"
            color = T.LIME
        else:
            msg = (f"⏱ Time! {self.t_sprint_count} correct"
                   + (f" (best: {best})" if best else ""))
            color = T.TEXT_SOFT
        self.t_status_lbl.configure(text=msg, text_color=color)
        self.t_timer_lbl.configure(text=f"Best: {best_dict.get(cat, 0)}")

    def _invaders_decode(self, dev):
        """App-decoder supplements for the game: merged BK and HH erase.
        Regular characters (and word-gap spaces) arrive via the WinKeyer
        echo path above."""
        if dev[0] == "char" and dev[1] == "BK":
            self.game.feed_char("BK")
        elif dev[0] == "clear":
            self.game.feed_erase()

    def _set_invaders_best(self, mix, score):
        best = dict(self.settings.get("invaders_best", {}))
        best[mix] = score
        self.settings["invaders_best"] = best
        save_settings(self.settings)

    def _trainer_decode(self, dev):
        """App-decoder supplements on the Trainer tab. The WinKeyer echo is
        the character source; the app decoder contributes only what the WK
        can't produce: merged BK (not in its table) and the HH wipe."""
        kind = dev[0]
        if kind == "char":
            if dev[1] == "BK":
                self._echo_char("BK")
        elif kind == "clear":
            if self._auto_next_id is not None:
                return                 # already matched -- let it advance
            self.t_buffer = ""
            self.t_status_lbl.configure(
                text="✗  HH — copy cleared, send the line again.",
                text_color=T.WARN)
            self.t_progress.set(0)
            self.t_hit = 0
            self._update_trainer_view()

    @staticmethod
    def _match_progress(norm_b, norm_t):
        """Fraction of the target already matched: the longest prefix of the
        normalized target that the normalized buffer currently ends with."""
        if not norm_t:
            return 0.0
        for i in range(min(len(norm_b), len(norm_t)), 0, -1):
            if norm_b.endswith(norm_t[:i]):
                return i / len(norm_t)
        return 0.0

    def _echo_char(self, ch):
        self.t_buffer += ch
        norm_t = drills.normalize(self.t_target)
        norm_b = drills.normalize(self.t_buffer)
        # Forgiving match: if the END of what you've sent equals the target,
        # you've nailed it -- even if you sent "EEEE" or false-started first.
        # (HH / a string of Es is the real-world "ignore that" signal.)
        if norm_b.endswith(norm_t):
            self.t_correct += 1
            self.t_attempted += 1
            self.t_streak += 1
            # Adaptive WPM: bump up after every 3 correct in a row.
            if (self.t_mode_var.get() == "Drill"
                    and self.t_adaptive_var.get()
                    and self.t_streak >= 3):
                self.t_streak = 0
                new_wpm = min(45, int(self.speed_var.get()) + 1)
                if new_wpm != int(self.speed_var.get()):
                    self.speed_var.set(new_wpm)
                    self.speed_lbl.configure(text=f"{new_wpm} WPM")
                    self.ctrl.set_wpm(new_wpm)
            # Sprint: count it and advance fast.
            if self.t_sprint_running:
                self.t_sprint_count += 1
                delay = 80
            else:
                delay = self.AUTO_NEXT_MS
            self.t_copy_lbl.configure(text_color=T.LIME)
            self.t_status_lbl.configure(text="✓  Correct!",
                                        text_color=T.LIME)
            self.t_progress.set(1.0)
            self.t_hit = len(self.t_target)
            self._auto_next_id = self.after(delay, self._next_prompt)
        else:
            self.t_copy_lbl.configure(text_color=T.TEXT)
            self.t_status_lbl.configure(text="…sending…",
                                        text_color=T.TEXT_SOFT)
            frac = self._match_progress(norm_b, norm_t)
            self.t_progress.set(frac)
            self.t_hit = self._raw_prefix_len(self.t_target,
                                              round(frac * len(norm_t)))
        self._update_trainer_view()

    @staticmethod
    def _raw_prefix_len(target, norm_len):
        """How many characters of the displayed target cover the first
        `norm_len` characters of its normalized form (prosigns expand)."""
        if norm_len <= 0:
            return 0
        for j in range(1, len(target) + 1):
            if len(drills.normalize(target[:j])) >= norm_len:
                return j
        return len(target)

    def _render_target(self):
        """Draw the Send line: matched prefix in lime, the rest in white, and
        size the box to the wrapped line count."""
        box, t = self.t_target_box, self.t_target
        hit = min(self.t_hit, len(t))
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", t[:hit], "hit")
        box.insert("end", t[hit:])
        box.configure(state="disabled")
        # Word-wrap estimate at the old label's 500 px wraplength.
        font, width, lines, line = self.t_target_font, 490, 1, ""
        for word in t.split(" "):
            trial = f"{line} {word}" if line else word
            if line and font.measure(trial) > width:
                lines, line = lines + 1, word
            else:
                line = trial
        box.configure(height=lines * font.metrics("linespace") + 4)

    @staticmethod
    def _trainer_font_size(text):
        """Big for short lines (callsigns, Q-codes), stepped down so long
        rag-chew lines still fit the window."""
        n = len(text)
        if n <= 14:
            return 34
        if n <= 26:
            return 30
        if n <= 45:
            return 26
        if n <= 70:
            return 22
        return 18

    def _update_trainer_view(self):
        size = self._trainer_font_size(self.t_target)
        if self.t_target_font.cget("size") != size:
            self.t_target_font.configure(size=size)
            self.t_copy_font.configure(size=size)
        self._render_target()
        # Show the tail of long buffers; matching uses the full buffer.
        self.t_copy_lbl.configure(text=self.t_buffer[-90:] or " ")
        score = (f"{self.t_correct} / {self.t_attempted}   "
                 f"streak {self.t_streak}")
        mw = self.ctrl.decoder.measured_wpm if self.ctrl.decoder else None
        if mw:
            score += f"   ~{mw} WPM"
        self.t_score_lbl.configure(text=score)

    # -- QSO simulator -----------------------------------------------------
    def _qso_log_write(self, text):
        self.q_log.configure(state="normal")
        self.q_log.insert("end", text)
        self.q_log.see("end")
        self.q_log.configure(state="disabled")

    def _qso_status(self, text, color=T.TEXT_SOFT):
        self.q_status_lbl.configure(text=text, text_color=color)

    def _qso_action(self):
        if self.q_state in ("sending", "listening"):
            self._qso_stop()
        else:
            self._qso_start()

    def _qso_start(self):
        if not self.running:
            self._qso_status("Click Connect at the top first.", T.DANGER)
            return
        scn = qsos.SCENARIOS.get(self.q_scn_var.get())
        if not scn:
            return
        steps, theircall = qsos.render_scenario(scn, self.profile)
        self.q_steps = steps
        self.q_theircall = theircall
        self.q_idx = 0
        self.q_log.configure(state="normal")
        self.q_log.delete("1.0", "end")
        self.q_log.configure(state="disabled")
        self.q_start_btn.configure(text="Stop QSO", fg_color=T.STOP)
        self._qso_play_step()

    def _qso_play_step(self):
        step = self.q_steps[self.q_idx]
        self.q_state = "sending"
        self.q_seen_busy = False
        self.q_buffer = ""
        self._qso_log_write(f"🎧 {self.q_theircall}: {step['send']}\n")
        self._qso_status(f"🎧 Listening to {self.q_theircall}…", T.TEXT_SOFT)
        # Trailing space helps the WinKeyer cleanly end the last letter.
        self.ctrl.send_text(step["send"] + " ")

    def _qso_advance(self):
        self.q_idx += 1
        if self.q_idx >= len(self.q_steps):
            self._qso_complete()
        else:
            self._qso_play_step()

    def _qso_complete(self):
        self.q_state = "complete"
        self.q_start_btn.configure(text="Start QSO", fg_color=T.GO)
        self._qso_log_write("\n✓ QSO complete!\n")
        self._qso_status("✓ QSO complete! Press Start for another.", T.LIME)

    def _qso_skip(self):
        if self.q_state == "listening":
            self._qso_log_write(" [skipped]\n")
            self._qso_advance()
        elif self.q_state == "sending":
            # The WK is still keying -- abort the buffer and jump to listen.
            self.ctrl.abort_send()
            self._qso_log_write(" [skipped]\n")
            self._qso_advance()

    def _qso_stop(self, silent=False):
        was_active = self.q_state in ("sending", "listening")
        if was_active:
            self.ctrl.abort_send()
            if not silent:
                self._qso_log_write("\n— stopped —\n")
        self.q_state = "idle"
        self.q_start_btn.configure(text="Start QSO", fg_color=T.GO)
        if was_active and not silent:
            self._qso_status("Stopped. Press Start for another QSO.", T.MUTED)

    def _qso_handle_busy(self, busy):
        if self.q_state != "sending":
            return
        if busy:
            self.q_seen_busy = True
        elif self.q_seen_busy:
            # WinKeyer drained its buffer -- your turn.
            self.q_state = "listening"
            step = self.q_steps[self.q_idx]
            self._qso_log_write("📡 You: ")
            self._qso_status(f"📡 Your turn — {step['hint']}", T.MAGENTA_SOFT)

    def _qso_handle_echo(self, ch):
        if self.q_state != "listening":
            return
        self.q_buffer += ch
        self._qso_log_write(ch)
        step = self.q_steps[self.q_idx]
        if qsos.matches(self.q_buffer, step["expect"]):
            self._qso_log_write("  ✓\n")
            self._qso_advance()

    # -- helpers -----------------------------------------------------------
    def _refresh_ports(self):
        self._set_ports([p.device for p in serial.tools.list_ports.comports()])

    def _set_ports(self, ports):
        values = ports or ["(no ports)"]
        self.port_menu.configure(values=values)
        if self.port_var.get() not in values:
            self.port_var.set(values[0])

    def _auto_select_port(self):
        """On launch, prefer the port we used last time if it's still here;
        otherwise probe the bus to find a WinKeyer automatically."""
        ports = [p.device for p in serial.tools.list_ports.comports()]
        last = self.settings.get("last_port", "")
        if last and last in ports:
            self.port_var.set(last)
            self._set_status(f"Ready — last used {last}. Click Connect.", T.MUTED)
        elif ports:
            self._set_status("Looking for a WinKeyer…", T.MUTED)
            self.detect_btn.configure(state="disabled")
            threading.Thread(target=self._detect_worker, daemon=True).start()
        else:
            self._set_status("No serial ports found. Plug in the WinKeyer.",
                             T.WARN)

    def _current_settings(self):
        port = self.port_var.get()
        return {
            "last_port":   "" if (not port or port.startswith("(")) else port,
            "game":        self.game_var.get(),
            "show_advanced": bool(self.adv.winfo_manager()),
            "mode":        self.mode_var.get(),
            "dit_key":     KEY_CHOICES.get(self.dit_var.get(), "lbracket"),
            "dah_key":     KEY_CHOICES.get(self.dah_var.get(), "rbracket"),
            "swap":        bool(self.swap_var.get()),
            "mute":        bool(self.mute_var.get()),
            "wpm":         int(self.speed_var.get()),
            "follow_knob": bool(self.follow_var.get()),
            "sidetone_hz": int(self.tone_var.get()),
            "sidetone_vol": self.vol_var.get().lower(),
            "t_mode":      self.t_mode_var.get(),
            "t_category":  self.t_cat_var.get(),
            "t_adaptive":  bool(self.t_adaptive_var.get()),
            "q_scenario":  self.q_scn_var.get(),
            "sprint_best": self.settings.get("sprint_best", {}),
            "inv_mix":     self.game.get_mix(),
            "invaders_best": self.settings.get("invaders_best", {}),
        }

    def _save_state(self):
        self.settings = self._current_settings()
        save_settings(self.settings)

    def _set_status(self, text, color=T.MUTED):
        self.status.configure(text=text, text_color=color)

    def _on_close(self):
        try:
            self._save_state()
        except Exception:
            pass
        # Always release the WinKeyer cleanly -- even if we're mid-start or
        # the 'running' flag is stale. ctrl.stop() is a safe no-op when
        # there's no open connection, and otherwise it sends Host-Close
        # (returns the keyer to standalone) and closes the COM port.
        try:
            self.ctrl.stop()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
