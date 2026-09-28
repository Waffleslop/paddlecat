# WinKeyer CW Trainer

A free Windows app for practising Morse code with a **K1EL WinKeyer** and your
own paddle. It scores your sending, runs drills and simulated QSOs, includes a
falling-words game, and turns your paddle into a keyboard so you can play
browser CW games like [Vail](https://vail.woozle.org) and
[VBand](https://hamradio.solutions/vband/) without a USB adapter dongle.

> **Requires a WinKeyer 3** (WK3 / WKUSB with WK3 firmware) and Windows.
> Tested on a K1EL WK3.1.

## What's included

**Trainer.** The app shows you a line of CW to send, you send it on your
paddle, and it scores you. It reads the WinKeyer's own decode, so the score
matches what you hear in the sidetone.
- Drill categories: Calling CQ, Contest Exchange, POTA, Rag Chew, Q-Codes &
  Phrases, Common Abbreviations, callsigns, numbers, park IDs, English words.
- Drill mode (lines advance automatically once you send them correctly) and
  Sprint mode (60-second timer with a best score per category).
- Adaptive WPM, a live streak counter, and your measured sending speed.
- Prosigns count whether you send them merged or letter-spaced, including BK.
  Send **HH** (8 dits) to wipe a botched attempt.

**QSO Simulator.** The WinKeyer sends the other station's side through its
sidetone, and you answer on your paddle. Scenarios include short and full rag
chews, POTA hunter and activator, and a contest search-and-pounce QSO.

**Invaders.** Words, Q-codes, callsigns and numbers fall from the sky, and you
shoot one down by keying it. Seven content mixes, each with its own high score.

**Bridge.** Turns your paddle into keystrokes so you can play browser CW games.
- *Paddle mode:* the two levers become two keys, and the game does the iambic
  timing.
- *Keyer mode:* the app does the iambic timing on one key, with speed set by a
  slider or the WinKeyer's speed knob. Set the game to straight-key input.

**Profile.** Enter your callsign, name, QTH, state, rig and antenna, and the
drills fill them in for you.

## Getting started

```
python -m pip install -r requirements.txt
python winkeyer_app.py
```

1. Plug in the WinKeyer and click **Detect** to find its COM port.
2. Fill in the **Profile** tab.
3. Pick a tab and start sending.

To build a standalone `.exe` that runs without Python, run `build.bat`. It
writes `dist\WinKeyerCWTrainer.exe`. The exe is unsigned, so Windows
SmartScreen warns on first run: click *More info → Run anyway*.

**Troubleshooting:** if the app reports "Port is in use", close your logger or
rig-control software, which is holding the WinKeyer. A CH340-based WinKeyer
can occasionally need an unplug and replug.

## Your next steps

- **Drill anywhere:** take your practice to your phone with
  [MorseCAT](https://potacat.com/morsecat), which is built for short CW drills
  whenever you have a spare minute.
- **Get on the air:** once your fist is ready, [POTACAT](https://potacat.com)
  lets you operate your own radio remotely, from anywhere.

Both are made by the same author as this trainer.

## How it works

The app puts the WinKeyer into WK3 mode and turns on its *Paddle Status*
report (X2MODE bit 7). The keyer then reports raw lever state over USB (1200
baud), and the app injects keystrokes through the Windows `SendInput` API.
Scoring uses the WinKeyer's paddle echo, which is decoded in firmware. An
app-side decoder (`morse_decode.py`) adds merged BK, the HH wipe and a
measured-WPM estimate. Sending is comfortable up to about 20–25 WPM.

`winkeyer_vail.py` is also a command-line diagnostic tool:

```
python winkeyer_vail.py detect
python winkeyer_vail.py monitor --port COM3
```

73!
