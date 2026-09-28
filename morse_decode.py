"""Streaming Morse decoder for the WinKeyer CW Trainer.

Consumes raw key-envelope edges (down/up with timestamps) from the app's
iambic keyer -- or a shadow keyer in paddle mode -- and emits decoded text
app-side, so Trainer scoring no longer depends on the WinKeyer's echo
decoder. That's what lets merged prosigns the WK doesn't know (BK) count,
and turns a burst of dits (HH) into an explicit "wipe the attempt" signal.

Events are delivered through the `notify` callable:

    ("char", s)    decoded character; prosigns the WK also knows come out as
                   the WK's ASCII ("=", "+", ">", ...) so drills.normalize
                   treats app decode and WK echo identically; BK comes out
                   as the literal letter pair "BK"
    ("space",)     word gap
    ("clear",)     CLEAR_MIN_DITS+ consecutive dits -- the HH error signal

Edges may arrive from any thread; `tick()` is called from the GUI poll loop
to flush the trailing character/word when keying stops. Both take an
explicit time `t` for testability (defaulting to time.perf_counter()).
"""

import threading
import time
from collections import deque


# ITU Morse -> ASCII, including the WK3 merged-prosign assignments (emitting
# the same ASCII the WinKeyer would echo) plus BK, which the WK can't decode.
MORSE = {
    ".-": "A",    "-...": "B",  "-.-.": "C",  "-..": "D",   ".": "E",
    "..-.": "F",  "--.": "G",   "....": "H",  "..": "I",    ".---": "J",
    "-.-": "K",   ".-..": "L",  "--": "M",    "-.": "N",    "---": "O",
    ".--.": "P",  "--.-": "Q",  ".-.": "R",   "...": "S",   "-": "T",
    "..-": "U",   "...-": "V",  ".--": "W",   "-..-": "X",  "-.--": "Y",
    "--..": "Z",
    "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
    ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
    "..--..": "?", ".-.-.-": ".", "--..--": ",",
    "-...-": "=",     # BT  separator
    ".-.-.": "+",     # AR  end of message
    "...-.-": ">",    # SK  end of work
    "-.--.": "(",     # KN  over to specific station
    "-.--.-": ")",    # KK
    ".-...": "[",     # AS  wait
    ".-.-": ";",      # AA  new line
    "-..-.": "/",     # DN  slash
    "-....-": "-",    # DU  hyphen
    ".--.-.": "@",    # AC
    ".-..-.": '"',    # RR
    "...-..-": "$",   # SX
    ".----.": "'",    # WG
    "-...-.-": "BK",  # break -- the one the WinKeyer can't do merged
}

# This many dits or more = HH -> ("clear",). 8 is the real HH error signal;
# anything lower collides with "5" (5 dits) plus one overshoot/jitter dit.
CLEAR_MIN_DITS = 8
UNKNOWN = "*"


class MorseDecoder:
    """Streaming key-envelope -> text decoder with gap-based segmentation."""

    CHAR_GAP_UNITS = 2.2  # key-up this long (in dit units) ends the character
    WORD_GAP_UNITS = 5.0  # ...and this long ends the word

    def __init__(self, wpm=20, notify=None):
        self._lock = threading.Lock()
        self._notify = notify or (lambda ev: None)
        self._wpm = max(5, int(wpm))
        self._down = False
        self._down_t = 0.0
        self._down_unit = 1.2 / self._wpm  # unit captured at the down edge
        self._up_t = None                  # last key-up; None = nothing keyed
        self._pattern = ""
        self._word_open = False            # a char was emitted since a space
        self._unit_est = deque(maxlen=10)  # element-derived dit estimates

    # -- live settings ------------------------------------------------------
    def set_wpm(self, wpm):
        with self._lock:
            self._wpm = max(5, min(99, int(wpm)))

    @property
    def measured_wpm(self):
        """The user's actual sending speed, from recent element lengths."""
        with self._lock:
            if len(self._unit_est) < 4:
                return None
            unit = sorted(self._unit_est)[len(self._unit_est) // 2]
        return max(1, round(1.2 / unit))

    # -- input --------------------------------------------------------------
    def key(self, down, t=None):
        """Feed one key edge. Duplicate edges are ignored."""
        t = time.perf_counter() if t is None else t
        events = []
        with self._lock:
            if down == self._down:
                return
            self._down = down
            if down:
                unit = 1.2 / self._wpm
                if self._up_t is not None:
                    gap = t - self._up_t
                    if self._pattern and gap >= self.CHAR_GAP_UNITS * unit:
                        events += self._finalize_locked()
                    if self._word_open and gap >= self.WORD_GAP_UNITS * unit:
                        events.append(("space",))
                        self._word_open = False
                self._down_t = t
                self._down_unit = unit
            else:
                dur = t - self._down_t
                is_dah = dur >= 2.0 * self._down_unit
                self._pattern += "-" if is_dah else "."
                self._unit_est.append(dur / (3.0 if is_dah else 1.0))
                self._up_t = t
        for ev in events:
            self._notify(ev)

    def tick(self, t=None):
        """Flush the trailing char/word once the key has stayed up long
        enough. Call from a periodic UI loop (~30 ms is plenty)."""
        t = time.perf_counter() if t is None else t
        events = []
        with self._lock:
            if not self._down and self._up_t is not None:
                gap = t - self._up_t
                unit = 1.2 / self._wpm
                if self._pattern and gap >= self.CHAR_GAP_UNITS * unit:
                    events += self._finalize_locked()
                if (not self._pattern and self._word_open
                        and gap >= self.WORD_GAP_UNITS * unit):
                    events.append(("space",))
                    self._word_open = False
        for ev in events:
            self._notify(ev)

    def reset(self):
        """Drop any partial character and word state (new drill prompt)."""
        with self._lock:
            self._pattern = ""
            self._word_open = False
            self._up_t = None

    # -- internals ----------------------------------------------------------
    def _finalize_locked(self):
        pattern, self._pattern = self._pattern, ""
        if len(pattern) >= CLEAR_MIN_DITS and set(pattern) == {"."}:
            self._word_open = False
            return [("clear",)]
        self._word_open = True
        return [("char", MORSE.get(pattern, UNKNOWN))]


# ---------------------------------------------------------------------------
# Self-test: drive the decoder with a synthetic clock -- no hardware, no GUI.
# ---------------------------------------------------------------------------
def _selftest():
    _REV = {}
    for pat, ch in MORSE.items():          # char -> pattern, first wins
        _REV.setdefault(ch, pat)

    class Sender:
        """Feeds perfectly timed edges; t advances like a real fist would."""
        def __init__(self, dec, wpm):
            self.dec, self.t = dec, 0.0
            self.set_wpm(wpm)

        def set_wpm(self, wpm):
            self.unit = 1.2 / wpm

        def pattern(self, pat):
            for el in pat:
                self.dec.key(True, self.t)
                self.t += self.unit * (3 if el == "-" else 1)
                self.dec.key(False, self.t)
                self.t += self.unit           # inter-element gap
            self.t += self.unit * 2           # complete the 3-unit char gap

        def text(self, s):
            for ch in s:
                if ch == " ":
                    self.t += self.unit * 4   # complete the 7-unit word gap
                else:
                    self.pattern(_REV[ch])

        def flush(self):
            # pattern() leaves t three units past the last key-up: already
            # beyond the char gap, still short of the word gap -- the
            # trailing character comes out without a trailing space event.
            self.dec.tick(self.t)

    def run(wpm, feed):
        out = []
        dec = MorseDecoder(wpm=wpm, notify=out.append)
        s = Sender(dec, wpm)
        feed(s, dec)
        s.flush()
        return "".join(ev[1] if ev[0] == "char" else
                       " " if ev[0] == "space" else "#" for ev in out)

    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  {name:24} -> {got!r}"
              + ("" if good else f"  (want {want!r})"))

    check("round trip", run(20, lambda s, d: s.text("PARIS PARIS")),
          "PARIS PARIS")
    check("merged BK", run(20, lambda s, d: s.pattern("-...-.-")), "BK")
    check("letter-spaced BK", run(20, lambda s, d: s.text("BK")), "BK")
    check("BT separator", run(20, lambda s, d: s.text("UR 599 = TU")),
          "UR 599 = TU")
    check("HH clears", run(20, lambda s, d: s.pattern("." * 8)), "#")
    check("digit 5 is not HH", run(20, lambda s, d: s.text("5")), "5")
    check("5 + 1 overshoot not HH", run(20, lambda s, d: s.pattern("." * 6)),
          UNKNOWN)
    check("5 + 2 overshoot not HH", run(20, lambda s, d: s.pattern("." * 7)),
          UNKNOWN)
    check("5NN", run(20, lambda s, d: s.text("5NN")), "5NN")
    check("unknown pattern", run(20, lambda s, d: s.pattern("---------")),
          UNKNOWN)

    def wpm_change(s, d):
        s.text("CQ ")
        d.set_wpm(35)
        s.set_wpm(35)
        s.text("DE W1AW")
    check("WPM change mid-line", run(20, wpm_change), "CQ DE W1AW")

    def measured(s, d):
        s.text("PARIS")
        got = d.measured_wpm
        print(f"        measured_wpm at 25 -> {got}")
        nonlocal ok
        ok &= got is not None and 24 <= got <= 26
    run(25, measured)

    print("SELF-TEST:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
