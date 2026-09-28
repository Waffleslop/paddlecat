"""Software iambic keyer.

Reads two paddle-lever booleans and drives a single on/off output with
correct CW element timing. This lets a WinKeyer paddle feed a *straight-key*
game input while the app -- not the game -- owns the sending speed.

Timing follows the PARIS standard: one dit-unit = 1200 / WPM milliseconds.
A dit is 1 unit key-down; a dah is 3 units key-down; every element is
followed by 1 unit of key-up (inter-element space).
"""

import threading
import time

DIT = 1     # element length in dit-units
DAH = 3


def _timer_resolution(enable):
    """Request 1 ms timer resolution on Windows so time.sleep -- and thus
    element timing -- is accurate to a millisecond or two."""
    try:
        import ctypes
        winmm = ctypes.WinDLL("winmm")
        (winmm.timeBeginPeriod if enable else winmm.timeEndPeriod)(1)
    except Exception:
        pass


class IambicKeyer:
    """An iambic keyer running on its own thread.

    key_down / key_up are callables invoked to assert/release the output.
    Lever state is delivered with feed(); speed is set live via .wpm.
    """

    def __init__(self, key_down, key_up, wpm=20):
        self._key_down = key_down
        self._key_up = key_up
        self.wpm = wpm
        self._dit = False          # current lever state
        self._dah = False
        self._dit_mem = False      # latched on a fresh tap so it is never lost
        self._dah_mem = False
        self._last = None          # last element sent (for squeeze alternation)
        self._running = False
        self._thread = None

    # -- called from the serial-reader thread ------------------------------
    def feed(self, dit, dah):
        """Update lever state. A fresh press (edge) latches a memory bit so a
        quick tap still produces an element even if the keyer was mid-element."""
        if dit and not self._dit:
            self._dit_mem = True
        if dah and not self._dah:
            self._dah_mem = True
        self._dit = dit
        self._dah = dah

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if self._running:
            return
        _timer_resolution(True)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._key_up()
        _timer_resolution(False)

    # -- keyer core --------------------------------------------------------
    def _unit(self):
        return 1.2 / max(self.wpm, 1)        # seconds; dit = 1200/wpm ms

    def _next(self):
        """Decide the next element, or None when idle."""
        if self._dit and self._dah:          # squeeze -> alternate
            return DAH if self._last == DIT else DIT
        if self._dah_mem:
            return DAH
        if self._dit_mem:
            return DIT
        if self._dah:
            return DAH
        if self._dit:
            return DIT
        return None

    def _loop(self):
        while self._running:
            elem = self._next()
            if elem is None:
                time.sleep(0.001)
                continue
            if elem == DIT:
                self._dit_mem = False
            else:
                self._dah_mem = False
            self._last = elem
            u = self._unit()
            self._key_down()
            self._sleep(u * elem)            # 1 unit (dit) or 3 units (dah)
            self._key_up()
            self._sleep(u)                   # inter-element space
        self._key_up()

    def _sleep(self, duration):
        """Sleep `duration` seconds with good accuracy, returning early if the
        keyer is stopped. Coarse-sleeps most of the interval, then busy-waits
        the final few milliseconds so element timing doesn't drift with the
        OS timer granularity."""
        end = time.perf_counter() + duration
        while self._running:
            left = end - time.perf_counter()
            if left <= 0:
                return
            if left > 0.006:
                time.sleep(left - 0.005)        # coarse sleep, leave a margin
            # else: spin out the last few ms for timing accuracy


# ---------------------------------------------------------------------------
# Self-test: exercise the keyer with no hardware and print the element pattern.
# ---------------------------------------------------------------------------
def _selftest():
    events = []                              # (timestamp, is_down)
    t0 = time.perf_counter()
    k = IambicKeyer(lambda: events.append((time.perf_counter() - t0, True)),
                    lambda: events.append((time.perf_counter() - t0, False)),
                    wpm=60)                  # fast, so the test runs quickly
    unit = 1.2 / 60                          # 20 ms

    def pulses():
        """Return key-down pulse lengths in units, then clear the log."""
        out = []
        downs = [t for t, d in events if d]
        ups = [t for t, d in events if not d]
        for i, dn in enumerate(downs):
            up = next((u for u in ups if u > dn), None)
            if up is not None:
                out.append(round((up - dn) / unit, 1))
        events.clear()
        return out

    def classify(ps):
        return "".join("." if p < 2 else "-" for p in ps)

    k.start()
    ok = True

    k.feed(True, False); time.sleep(unit * 7); k.feed(False, False)
    time.sleep(unit * 3)
    p = pulses()
    print(f"hold dit  -> pulses {p}  = {classify(p)!r}")
    ok &= len(p) >= 2 and all(x < 2 for x in p)

    k.feed(False, True); time.sleep(unit * 12); k.feed(False, False)
    time.sleep(unit * 3)
    p = pulses()
    print(f"hold dah  -> pulses {p}  = {classify(p)!r}")
    ok &= len(p) >= 2 and all(x >= 2 for x in p)

    k.feed(True, True); time.sleep(unit * 10); k.feed(False, False)
    time.sleep(unit * 4)
    p = pulses()
    print(f"squeeze   -> pulses {p}  = {classify(p)!r}")
    ok &= classify(p).startswith(".-") or classify(p).startswith("-.")

    k.feed(True, False); time.sleep(0.001); k.feed(False, False)
    time.sleep(unit * 4)
    p = pulses()
    print(f"quick tap -> pulses {p}  = {classify(p)!r}")
    ok &= len(p) == 1

    k.stop()
    print("SELF-TEST:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
