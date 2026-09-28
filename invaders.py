"""PaddleCAT Invaders -- a falling-words CW game.

Ham-relevant strings (callsigns, Q-codes, abbreviations, words, numbers)
fall from the top of a canvas. Key one on your paddle and the turret fires
a bullet that destroys it. A target reaching the bottom costs a life.

The game consumes decoded characters via feed_char / feed_gap / feed_erase;
it never talks to the WinKeyer itself. The host app feeds it the WinKeyer's
own paddle echo (the highest-fidelity decode of the user's fist), with the
app-side decoder supplying merged BK and the HH erase signal. Matching
mirrors the Trainer's forgiving style: the normalized buffer just has to
END with a target's normalized text, so false starts cost nothing.

Run this file directly for a standalone, keyboard-driven test harness:
    python invaders.py
In the full app, set WKB_DEBUG=1 to enable the same keyboard input.
"""

import math
import os
import random
import time
import tkinter as tk
import tkinter.font as tkfont

import drills
import theme as T

DEBUG = os.environ.get("WKB_DEBUG") == "1"

# -- tuning -----------------------------------------------------------------
TICK_MS        = 33          # ~30 FPS
CANVAS_W       = 500         # fits inside the tab (520 clipped the HUD)
CANVAS_H       = 440
LIVES          = 3
BASE_FALL      = 16.0        # px/s at wave 1
FALL_GROWTH    = 1.12        # per wave
FALL_CAP       = 55.0
BASE_SPAWN     = 4.5         # s between spawns at wave 1
SPAWN_GROWTH   = 0.93        # per wave
SPAWN_MIN      = 2.0
KILLS_PER_WAVE = 8
BULLET_S       = 0.12        # bullet flight time
EXPLOSION_S    = 0.30
BUFFER_CAP     = 24          # decoded chars kept for matching
TURRET_LINE    = CANVAS_H - 46   # a target this low has landed

# -- colors -----------------------------------------------------------------
C_BG      = "#0d0418"         # a shade deeper than the window, like the icon
C_STAR    = "#2a1840"
C_SCAN    = "#170a26"         # faint CRT scanlines, as on the icon
C_TURRET  = T.MAGENTA
C_TEXT    = T.TEXT
C_WARN    = T.WARN
C_DANGER  = T.DANGER
C_MATCH   = T.LIME
C_BULLET  = T.LIME
C_BOOM    = (T.LIME, T.MAGENTA)
C_DIM     = T.DIM
C_GLOW    = "#5a1257"         # dim magenta halo drawn under neon text

# Content mixes: (drills category, weight). Single tokens are pulled out of
# the rendered drill lines.
MIXES = {
    "Ham Mix":       [("Common Abbreviations", 30), ("English Words", 25),
                      ("Random Callsigns", 20), ("Q-Codes & Phrases", 15),
                      ("Number Groups", 10)],
    "Words":         [("English Words", 1)],
    "Callsigns":     [("Random Callsigns", 1)],
    "Abbreviations": [("Common Abbreviations", 1)],
    "Numbers":       [("Number Groups", 1)],
    "Q-Codes":       [("Q-Codes & Phrases", 1)],
    "Park IDs":      [("POTA Park IDs", 1)],
}


def _norm_map(text):
    """Cumulative normalized length after each raw char -- maps a matched
    normalized-prefix length back to whole raw characters (prosign chars
    like the Park-ID hyphen normalize to two letters)."""
    out, total = [], 0
    for ch in text.upper():
        total += len(drills._PROSIGN_EXPAND.get(ch, ch))
        out.append(total)
    return out


class Target:
    __slots__ = ("text", "norm", "nmap", "x", "y", "matched",
                 "id_hit", "id_rest")

    def __init__(self, text, x, y):
        self.text = text
        self.norm = drills.normalize(text)
        self.nmap = _norm_map(text)
        self.x, self.y = x, y
        self.matched = 0
        self.id_hit = self.id_rest = None


class InvadersGame:
    """The whole game: widgets, loop, and input. Coupled to the host app
    only through the five callables passed to __init__."""

    def __init__(self, parent, *, profile_getter, is_ready, get_wpm,
                 get_best, set_best, initial_mix="Ham Mix",
                 debug_keys=False):
        self.profile_getter = profile_getter
        self.is_ready = is_ready
        self.get_wpm = get_wpm
        self.get_best = get_best
        self.set_best = set_best
        self.debug_keys = debug_keys or DEBUG

        self.state = "idle"      # idle | playing | paused | gameover
        self.targets = []
        self.bullets = []        # {"target","x0","y0","t0","item"}
        self.particles = []      # {"item","x","y","dx","dy","t0","fade"}
        self.buffer = ""
        self.score = 0
        self.lives = LIVES
        self.wave = 1
        self.kills = 0
        self.streak = 0
        self._after_id = None
        self._last_tick = 0.0
        self._last_char_t = 0.0
        self._next_spawn = 0.0
        self._wave_flash = None  # (canvas item, expiry)

        import customtkinter as ctk
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(10, 4))
        self.btn = ctk.CTkButton(row, text="Start", width=110,
                                 command=self._on_button)
        self.btn.pack(side="left")
        self.mix_var = ctk.StringVar(
            value=initial_mix if initial_mix in MIXES else "Ham Mix")
        self.mix_menu = ctk.CTkOptionMenu(row, variable=self.mix_var,
                                          values=list(MIXES), width=150,
                                          command=self._on_mix)
        self.mix_menu.pack(side="left", padx=8)
        self.best_lbl = ctk.CTkLabel(row, text="", text_color=T.TEXT_SOFT)
        self.best_lbl.pack(side="right")

        self.canvas = tk.Canvas(parent, width=CANVAS_W, height=CANVAS_H,
                                bg=C_BG, highlightthickness=0)
        self.canvas.pack(padx=14, pady=(2, 2))

        self.buf_lbl = ctk.CTkLabel(
            parent, text="", text_color=C_DIM,
            font=ctk.CTkFont(family=T.MONO_FAMILY, size=18, weight="bold"))
        self.buf_lbl.pack(fill="x", padx=14, pady=(2, 0))

        self.font = tkfont.Font(family=T.MONO_FAMILY, size=16, weight="bold")
        self.char_w = self.font.measure("0")

        if self.debug_keys:
            self.canvas.bind("<Key>", self._on_key)
            self.canvas.bind("<Button-1>", lambda e: self.canvas.focus_set())

        self._draw_stars()
        self._refresh_best()
        self._show_idle()

    # -- public: lifecycle --------------------------------------------------
    def get_mix(self):
        return self.mix_var.get()

    def on_tab_left(self):
        if self.state == "playing":
            self.pause(auto=True)

    def on_bridge_stopped(self):
        if self.state == "playing":
            self.pause(auto=True, reason="WinKeyer disconnected")

    def _on_button(self):
        if self.state == "playing":
            self.pause()
        elif self.state == "paused":
            self.resume()
        else:
            self.start()

    def _on_mix(self, _=None):
        self._refresh_best()

    def start(self):
        if not (self.is_ready() or self.debug_keys):
            self._show_idle(warn=True)
            return
        self._clear_field()
        self.state = "playing"
        self.score, self.lives, self.wave = 0, LIVES, 1
        self.kills, self.streak = 0, 0
        self.buffer = ""
        now = time.perf_counter()
        self._last_tick = now
        self._last_char_t = now
        self._next_spawn = now + 1.0
        self.btn.configure(text="Pause", fg_color=T.STOP)
        self.mix_menu.configure(state="disabled")
        self._draw_hud()
        self._flash_text("GET READY", 1.0)
        self._update_buffer_label()
        if self.debug_keys:
            self.canvas.focus_set()
        self._schedule()

    def pause(self, auto=False, reason=""):
        if self.state != "playing":
            return
        self.state = "paused"
        self._cancel()
        self.btn.configure(text="Resume", fg_color=T.GO)
        sub = reason or ("Switched tab" if auto else "")
        self._show_overlay("PAUSED", sub + ("\n" if sub else "")
                           + "Press Resume to continue")

    def resume(self):
        if self.state != "paused":
            return
        if not (self.is_ready() or self.debug_keys):
            self._show_overlay("PAUSED", "Click Connect at the top first")
            return
        self._hide_overlay()
        self.state = "playing"
        self._last_tick = time.perf_counter()
        self.btn.configure(text="Pause", fg_color=T.STOP)
        if self.debug_keys:
            self.canvas.focus_set()
        self._schedule()

    def stop(self):
        self._cancel()
        self.state = "idle"
        self._clear_field()
        self.btn.configure(text="Start", fg_color=T.GO)
        self.mix_menu.configure(state="normal")
        self._show_idle()

    # -- public: decoded input ---------------------------------------------
    def feed_char(self, ch):
        if self.state != "playing":
            return
        if ch == " ":
            self.feed_gap()
            return
        self.buffer = (self.buffer + ch)[-BUFFER_CAP:]
        self._last_char_t = time.perf_counter()
        self._update_buffer_label()
        self._try_fire()
        self._update_highlights()

    def feed_gap(self):
        if self.state != "playing" or not self.buffer:
            return
        self.buffer = ""
        self._update_buffer_label()
        self._update_highlights()

    def feed_erase(self):
        if self.state != "playing":
            return
        self.buffer = ""
        self._update_buffer_label(flash="CLEARED")
        self._update_highlights()

    # -- game loop ----------------------------------------------------------
    def _schedule(self):
        self._after_id = self.canvas.after(TICK_MS, self._tick)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.canvas.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _tick(self):
        self._after_id = None
        if self.state != "playing":
            return
        try:
            if not (self.is_ready() or self.debug_keys):
                self.pause(auto=True, reason="WinKeyer disconnected")
                return
            now = time.perf_counter()
            dt = min(now - self._last_tick, 0.1)
            self._last_tick = now

            self._move_targets(dt)
            self._update_bullets(now)
            self._update_particles(now)
            if now >= self._next_spawn:
                self._spawn(now)
            # Fallback word-gap: pausing the key clears stale buffer.
            if self.buffer and (now - self._last_char_t) > self._gap_timeout():
                self.feed_gap()
            if self._wave_flash and now >= self._wave_flash[1]:
                self.canvas.delete(self._wave_flash[0])
                self._wave_flash = None
            if self.state == "playing":
                self._schedule()
        except tk.TclError:
            return                      # window is being torn down

    def _gap_timeout(self):
        wpm = max(5, int(self.get_wpm() or 20))
        return max(0.8, 1.5 * 7 * 1.2 / wpm)

    def _max_targets(self):
        return 3 if self.wave <= 2 else (4 if self.wave <= 5 else 5)

    def _fall_speed(self):
        return min(FALL_CAP, BASE_FALL * (FALL_GROWTH ** (self.wave - 1)))

    def _spawn_interval(self):
        return max(SPAWN_MIN, BASE_SPAWN * (SPAWN_GROWTH ** (self.wave - 1)))

    # -- targets ------------------------------------------------------------
    def _pick_token(self):
        cats = MIXES[self.mix_var.get()]
        names = [c for c, _ in cats]
        weights = [w for _, w in cats]
        for _ in range(8):
            cat = random.choices(names, weights)[0]
            text, _tpl = drills.pick(cat, self.profile_getter())
            tokens = [w for w in text.split() if 2 <= len(w) <= 10]
            if not tokens:
                continue
            tok = random.choice(tokens)
            norm = drills.normalize(tok)
            if any(t.norm == norm for t in self.targets):
                continue
            return tok
        return "TU"

    def _spawn(self, now):
        if len(self.targets) >= self._max_targets():
            self._next_spawn = now + 0.5
            return
        tok = self._pick_token()
        w = len(tok) * self.char_w
        x = None
        for _ in range(5):
            cand = random.uniform(8, CANVAS_W - w - 8)
            high = [t for t in self.targets if t.y < CANVAS_H * 0.25]
            if all(cand + w < t.x or cand > t.x + len(t.text) * self.char_w
                   for t in high):
                x = cand
                break
        if x is None:
            x = random.uniform(8, CANVAS_W - w - 8)
        t = Target(tok, x, 26)
        t.id_hit = self.canvas.create_text(x, t.y, text="", anchor="nw",
                                           font=self.font, fill=C_MATCH)
        t.id_rest = self.canvas.create_text(x, t.y, text=tok, anchor="nw",
                                            font=self.font, fill=C_TEXT)
        self.targets.append(t)
        self._next_spawn = now + self._spawn_interval()

    def _move_targets(self, dt):
        dy = self._fall_speed() * dt
        for t in list(self.targets):
            t.y += dy
            if t.y >= TURRET_LINE:
                self._land(t)
                continue
            frac = t.y / TURRET_LINE
            color = (C_DANGER if frac > 0.9 else
                     C_WARN if frac > 0.75 else C_TEXT)
            self.canvas.coords(t.id_hit, t.x, t.y)
            self.canvas.coords(t.id_rest, t.x + t.matched * self.char_w, t.y)
            self.canvas.itemconfigure(t.id_rest, fill=color)

    def _land(self, t):
        self._remove_target(t)
        self._explode(t.x + len(t.text) * self.char_w / 2, TURRET_LINE,
                      color=C_DANGER)
        self.lives -= 1
        self.streak = 0
        self._draw_hud()
        if self.lives <= 0:
            self._game_over()

    def _remove_target(self, t):
        if t in self.targets:
            self.targets.remove(t)
        self.canvas.delete(t.id_hit)
        self.canvas.delete(t.id_rest)

    # -- shooting -----------------------------------------------------------
    def _try_fire(self):
        nb = drills.normalize(self.buffer)
        if not nb:
            return
        hits = [t for t in self.targets if nb.endswith(t.norm)]
        if not hits:
            return
        # Longest match wins; tie goes to the target closest to the ground.
        t = max(hits, key=lambda t: (len(t.norm), t.y))
        self.buffer = ""
        self._update_buffer_label(flash=t.text)
        self.score += len(t.norm) * 10 * self.wave + 5 * self.streak
        self.streak += 1
        self.kills += 1
        self.targets.remove(t)              # stops falling; visuals stay
        self.bullets.append({
            "target": t,
            "x0": CANVAS_W / 2, "y0": CANVAS_H - 18,
            "t0": time.perf_counter(),
            "item": self.canvas.create_line(0, 0, 0, 0, fill=C_BULLET,
                                            width=3),
        })
        self._draw_hud()
        if self.kills % KILLS_PER_WAVE == 0:
            self.wave += 1
            self._draw_hud()
            self._flash_text(f"WAVE {self.wave}", 1.2)

    def _update_bullets(self, now):
        for b in list(self.bullets):
            t = b["target"]
            p = (now - b["t0"]) / BULLET_S
            tx = t.x + len(t.text) * self.char_w / 2
            ty = t.y + 8
            if p >= 1.0:
                self.canvas.delete(b["item"])
                self.bullets.remove(b)
                self.canvas.delete(t.id_hit)
                self.canvas.delete(t.id_rest)
                self._explode(tx, ty)
            else:
                x = b["x0"] + (tx - b["x0"]) * p
                y = b["y0"] + (ty - b["y0"]) * p
                self.canvas.coords(b["item"], x, y + 10, x, y)

    # -- effects ------------------------------------------------------------
    def _explode(self, x, y, color=None):
        now = time.perf_counter()
        for _ in range(10):
            ang = random.uniform(0, 6.283)
            speed = random.uniform(40, 140)
            c = color or random.choice(C_BOOM)
            item = self.canvas.create_oval(x - 2, y - 2, x + 2, y + 2,
                                           fill=c, outline="")
            self.particles.append({
                "item": item, "x": x, "y": y, "t0": now,
                "dx": speed * math.cos(ang),
                "dy": speed * math.sin(ang),
            })

    def _update_particles(self, now):
        for p in list(self.particles):
            age = now - p["t0"]
            if age >= EXPLOSION_S:
                self.canvas.delete(p["item"])
                self.particles.remove(p)
                continue
            x = p["x"] + p["dx"] * age
            y = p["y"] + p["dy"] * age
            self.canvas.coords(p["item"], x - 2, y - 2, x + 2, y + 2)

    def _flash_text(self, text, secs):
        if self._wave_flash:
            self.canvas.delete(self._wave_flash[0])
        item = self.canvas.create_text(
            CANVAS_W / 2, CANVAS_H / 2, text=text, fill=C_MATCH,
            font=(T.HEADING_FAMILY, 28, "bold"))
        self._wave_flash = (item, time.perf_counter() + secs)

    # -- highlight / buffer row --------------------------------------------
    def _update_highlights(self):
        nb = drills.normalize(self.buffer)
        for t in self.targets:
            k = 0
            for i in range(min(len(nb), len(t.norm)), 0, -1):
                if nb.endswith(t.norm[:i]):
                    k = i
                    break
            m = 0
            while m < len(t.nmap) and t.nmap[m] <= k:
                m += 1
            if m != t.matched:
                t.matched = m
                self.canvas.itemconfigure(t.id_hit, text=t.text[:m])
                self.canvas.itemconfigure(t.id_rest, text=t.text[m:])
                self.canvas.coords(t.id_rest,
                                   t.x + m * self.char_w, t.y)

    def _update_buffer_label(self, flash=None):
        if flash:
            self.buf_lbl.configure(text=flash, text_color=C_MATCH)
        elif self.buffer:
            self.buf_lbl.configure(text=self.buffer, text_color=T.TEXT)
        else:
            self.buf_lbl.configure(text="KEY A FALLING WORD TO FIRE",
                                   text_color=C_DIM)

    # -- drawing ------------------------------------------------------------
    def _draw_stars(self):
        for y in range(0, CANVAS_H, 3):
            self.canvas.create_line(0, y, CANVAS_W, y, fill=C_SCAN,
                                    tags="scan")
        for _ in range(40):
            x = random.uniform(0, CANVAS_W)
            y = random.uniform(0, CANVAS_H)
            self.canvas.create_oval(x, y, x + 1.5, y + 1.5,
                                    fill=C_STAR, outline="", tags="star")

    def _draw_turret(self):
        cx = CANVAS_W / 2
        self.canvas.create_polygon(
            cx - 14, CANVAS_H - 6, cx + 14, CANVAS_H - 6, cx, CANVAS_H - 26,
            fill=C_TURRET, outline="", tags="turret")

    def _draw_hud(self):
        self.canvas.delete("hud")
        self.canvas.create_text(10, 10, text=f"SCORE {self.score}",
                                anchor="nw", fill=C_TEXT,
                                font=(T.MONO_FAMILY, 12, "bold"), tags="hud")
        self.canvas.create_text(CANVAS_W - 10, 10, text=f"WAVE {self.wave}",
                                anchor="ne", fill=C_TEXT,
                                font=(T.MONO_FAMILY, 12, "bold"), tags="hud")
        self.canvas.create_text(CANVAS_W / 2, 10,
                                text="▲ " * max(0, self.lives),
                                anchor="n", fill=C_TURRET,
                                font=(T.MONO_FAMILY, 12), tags="hud")

    def _clear_field(self):
        for t in list(self.targets):
            self._remove_target(t)
        for b in self.bullets:
            self.canvas.delete(b["item"])
            self.canvas.delete(b["target"].id_hit)
            self.canvas.delete(b["target"].id_rest)
        for p in self.particles:
            self.canvas.delete(p["item"])
        self.bullets, self.particles = [], []
        if self._wave_flash:
            self.canvas.delete(self._wave_flash[0])
            self._wave_flash = None
        self.canvas.delete("hud")
        self.canvas.delete("overlay")
        self.canvas.delete("turret")
        self._draw_turret()

    # -- overlays / end states ---------------------------------------------
    def _show_overlay(self, title, subtitle=""):
        self.canvas.delete("overlay")
        self.canvas.create_rectangle(0, 0, CANVAS_W, CANVAS_H, fill=C_BG,
                                     stipple="gray50", outline="",
                                     tags="overlay")
        ty = CANVAS_H / 2 - (70 if subtitle else 30)
        self._neon_text(CANVAS_W / 2, ty, title, T.MAGENTA,
                        (T.HEADING_FAMILY, 32 if len(title) <= 14 else 28, "bold"),
                        tags="overlay")
        if subtitle:
            self.canvas.create_text(CANVAS_W / 2, CANVAS_H / 2 - 36,
                                    text=subtitle, fill=T.TEXT_SOFT,
                                    justify="center", anchor="n",
                                    font=(T.MONO_FAMILY, 13), tags="overlay")

    def _neon_text(self, x, y, text, color, font, **kw):
        """Text with a soft halo underneath, echoing the icon's glow."""
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            self.canvas.create_text(x + dx, y + dy, text=text, fill=C_GLOW,
                                    font=font, **kw)
        return self.canvas.create_text(x, y, text=text, fill=color,
                                       font=font, **kw)

    def _hide_overlay(self):
        self.canvas.delete("overlay")

    def _show_idle(self, warn=False):
        self._clear_field()
        sub = ("Falling words, Q-codes and callsigns.\n"
               "Key one on your paddle to shoot it down.\n"
               "A word gap clears your buffer; HH erases.")
        if warn or not (self.is_ready() or self.debug_keys):
            sub += "\n\nClick Connect at the top first"
        self._show_overlay("PADDLECAT INVADERS", sub)

    def _game_over(self):
        self.state = "gameover"
        self._cancel()
        mix = self.mix_var.get()
        best = self.get_best(mix)
        new_best = self.score > best
        if new_best:
            self.set_best(mix, self.score)
        self.btn.configure(text="Start", fg_color=T.GO)
        self.mix_menu.configure(state="normal")
        self._refresh_best()
        self._show_overlay(
            "GAME OVER",
            f"Score {self.score}"
            + ("  —  NEW BEST!" if new_best else f"   (best {best})")
            + f"\nWave {self.wave}")

    def _refresh_best(self):
        best = self.get_best(self.mix_var.get())
        self.best_lbl.configure(text=f"Best: {best}" if best else "")

    # -- debug keyboard input ----------------------------------------------
    def _on_key(self, ev):
        if ev.keysym == "space":
            self.feed_gap()
        elif ev.keysym == "BackSpace":
            self.feed_erase()
        elif len(ev.char) == 1 and ev.char.isprintable():
            self.feed_char(ev.char.upper())


# ---------------------------------------------------------------------------
# Standalone harness: python invaders.py  (keyboard input, no WinKeyer)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import customtkinter as ctk

    T.apply()
    root = ctk.CTk()
    root.title("PaddleCAT Invaders — standalone test")
    root.geometry("560x580")
    best = {}
    game = InvadersGame(
        root,
        profile_getter=lambda: {},
        is_ready=lambda: True,
        get_wpm=lambda: 20,
        get_best=lambda mix: best.get(mix, 0),
        set_best=lambda mix, s: best.__setitem__(mix, s),
        debug_keys=True)
    print("Standalone mode: click the canvas, then type letters to 'key'.")
    print("Space = word gap, Backspace = HH erase.")
    root.mainloop()
