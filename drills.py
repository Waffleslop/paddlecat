"""Drill content for the WinKeyer CW Trainer trainer.

Each category is a list of template strings. Placeholders such as {CALL},
{NAME}, {QTH}, {STATE}, {RIG}, {ANT} are filled from the user profile.
{THEIRCALL} and {THEIRSTATE} get a random fake value each time so the same
template doesn't drill the same partner over and over.

The "=" inside rag-chew templates is the BT prosign (separator), which the
WinKeyer's paddle echo decodes back to "=" -- so it round-trips cleanly.
"""

import random
from collections import deque


DEFAULT_PROFILE = {
    "CALL":  "W1AW",
    "NAME":  "DAN",
    "QTH":   "BOSTON",
    "STATE": "MA",
    "RIG":   "K3",
    "ANT":   "DIPOLE",
}

# Callsign generator -- realistic-shaped random calls for {THEIRCALL}, so
# callsign drills draw from the whole callsign space instead of a short list.
_US_SHAPES = [            # (prefix letters, suffix letters), realistic weights
    ((1, 2), 10),         # 1x2   W4QX
    ((1, 3), 30),         # 1x3   K5JKL
    ((2, 1), 6),          # 2x1   KX9A
    ((2, 2), 22),         # 2x2   KD9AB
    ((2, 3), 32),         # 2x3   WB6FOO
]
_DX_PREFIXES = ["VE", "G", "DL", "JA", "F", "EA", "VK", "SM", "OH", "PY"]
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def random_callsign(dx_chance=0.12):
    """A plausible random amateur callsign, e.g. 'W4QX', 'KD9AB', 'DL3KF'.

    Mostly US formats (K/N/W/A prefixes, shape weights above), with an
    occasional common DX prefix for flavor."""
    if random.random() < dx_chance:
        prefix = random.choice(_DX_PREFIXES)
        suffix_len = random.choice((2, 3))
    else:
        (plen, suffix_len) = random.choices(
            [shape for shape, _ in _US_SHAPES],
            [w for _, w in _US_SHAPES])[0]
        if plen == 1:
            prefix = random.choice("KNW")
        else:
            first = random.choice("AKNW")
            # US two-letter A-prefixes only run AA..AL.
            second = random.choice(_LETTERS[:12] if first == "A" else _LETTERS)
            prefix = first + second
    return (prefix + str(random.randint(0, 9))
            + "".join(random.choice(_LETTERS) for _ in range(suffix_len)))

_FAKE_STATES = [
    "NY", "CA", "TX", "FL", "WA", "CO", "MI", "OH", "PA", "VA", "NC",
    "GA", "IL", "AZ", "MN", "WI", "OR", "NJ", "MD", "MA", "TN", "IN",
    "MO", "KY", "AL", "ON", "BC", "QC",
]


CATEGORIES = {
    "Calling CQ": [
        "CQ CQ CQ DE {CALL} {CALL} K",
        "CQ DE {CALL} K",
        "CQ CQ DE {CALL} {CALL}",
        "QRZ DE {CALL} K",
        "CQ TEST DE {CALL} {CALL}",
        "CQ POTA DE {CALL} {CALL} K",
        "CQ DX CQ DX DE {CALL} {CALL} K",
    ],

    "Contest Exchange": [
        "{THEIRCALL} 5NN {STATE}",
        "5NN {STATE} {STATE}",
        "TU 5NN {STATE}",
        "{THEIRCALL} TU 5NN {STATE}",
        "AGN? {THEIRCALL}",
        "{CALL} 5NN {STATE}",
        "TU {THEIRCALL}",
        "{THEIRCALL} 599 {STATE}",
    ],

    "POTA": [
        "CQ POTA DE {CALL} {CALL} K",
        "{THEIRCALL} UR 599 599 {STATE} {STATE}",
        "BK UR 599 {STATE} BK",
        "{THEIRCALL} 599 {STATE} BK",
        "TU {THEIRSTATE} 73 EE",
        "QRZ POTA DE {CALL}",
        "5NN {STATE}",
    ],

    "Rag Chew": [
        "{THEIRCALL} DE {CALL} = GM TU FER CALL = UR RST 599 599 = NAME HR IS {NAME} {NAME} = QTH {QTH} {QTH} = HW? {THEIRCALL} DE {CALL} K",
        "BK TU GA {THEIRCALL} = NAME IS {NAME} {NAME} = QTH {QTH} {QTH} BK",
        "= RIG IS {RIG} {RIG} ES ANT IS {ANT} = HW CPY? {THEIRCALL} DE {CALL} K",
        "= TU FER NICE QSO {NAME} = 73 ES GL = {THEIRCALL} DE {CALL} SK",
        "{THEIRCALL} DE {CALL} GA = UR RST 599 599 = NAME {NAME} QTH {QTH} = HW? {THEIRCALL} DE {CALL} KN",
        "= WX HR IS FB ES TEMP 70 = HW? {THEIRCALL} DE {CALL} BK",
        "= AGE 45 ES BEEN HAM 20 YRS = {THEIRCALL} DE {CALL} BK",
        "= SRI QSB = PSE AGN? {THEIRCALL} DE {CALL} K",
        "{THEIRCALL} DE {CALL} = TNX FER QSO = 73 ES GL = SK",
        "= OP HR IS {NAME} {NAME} = QTH IS {QTH} {QTH} = HW CPY? K",
    ],

    "Q-Codes & Phrases": [
        "QRZ?", "QRL?", "QRS PSE", "QRQ?", "QRT NW 73", "QRV?", "QRX 5",
        "QSL?", "QSL TU", "QTH?", "QSY UP", "QSY DN", "QSY {FREQ}",
        "QRM QSY {FREQ}", "QRP {PWR}W", "QRO {PWR}W", "QSB BAD", "QRM HVY",
        "QRN HVY", "PSE QSY", "RPT PSE", "AGN PSE", "UR RST {RST} {RST}",
        "RST {RST}", "FREQ {FREQ}", "PWR {PWR}W", "WX HR FB", "WX HR RAIN",
        "WX SUNNY ES WARM", "TEMP {NUM2}F", "TEMP {NUM2}C",
        "RIG IS {RIG}", "ANT IS {ANT}", "NAME IS {NAME}", "QTH IS {QTH}",
        "OP IS {NAME}",
    ],

    "Common Abbreviations": [
        "TU", "73", "88", "FB", "GM", "GA", "GE", "ES", "BK", "K", "KN",
        "SK", "AR", "AGN", "PSE", "RR", "HW", "CPY", "OM", "YL", "XYL",
        "WX", "TEMP", "ANT", "RIG", "QSL", "TNX", "GL", "DE", "CQ", "UR",
        "RST", "NAME", "QTH",
    ],

    "Random Callsigns": [
        "{THEIRCALL}",
    ],

    "Number Groups": [
        "{NUM3}",
        "{NUM5}",
        "5NN {NUM3}",
        "599 {NUM3}",
        "OP {NUM2}",                        # operator age
        "AGE {NUM2}",
        "{NUM2} YRS",                       # years a ham
        "TEMP {NUM2}F",
        "{NUM4} W",                         # power
    ],

    "POTA Park IDs": [
        "K-{PARK4}",
        "VE-{PARK4}",
        "JA-{PARK4}",
        "G-{PARK4}",
        "DL-{PARK4}",
        "POTA K-{PARK4}",
    ],

    "English Words": [
        "HELLO", "WORLD", "QUICK", "BROWN", "FOX", "JUMP", "OVER", "LAZY",
        "DOG", "RADIO", "MORSE", "CODE", "PADDLE", "KEYER", "STATION",
        "ANTENNA", "POWER", "BAND", "FREQUENCY", "SIGNAL", "REPORT",
        "CONTEST", "PARK", "ACTIVATE", "HUNT", "RAGCHEW", "FRIEND",
        "FINE", "BUSINESS", "GOOD", "DAY", "MORNING", "EVENING", "NIGHT",
        "WEATHER", "RAIN", "SUN", "WIND", "TEMPERATURE", "AGE", "YEAR",
        "HOUSE", "TREE", "WATER", "COFFEE", "TIME", "PEOPLE", "FAMILY",
        "WORK", "PLAY", "MUSIC", "BOOK", "READ", "WRITE", "BUILD",
        "LEARN", "TEACH", "SLEEP", "WAKE", "WALK", "RUN", "DRIVE",
    ],

    "Pangrams & Practice Lines": [
        "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG",
        "PACK MY BOX WITH FIVE DOZEN LIQUOR JUGS",
        "HOW VEXINGLY QUICK DAFT ZEBRAS JUMP",
        "AMAZINGLY FEW DISCOTHEQUES PROVIDE JUKEBOXES",
        "SPHINX OF BLACK QUARTZ JUDGE MY VOW",
        "JACKDAWS LOVE MY BIG SPHINX OF QUARTZ",
        "A WIZARDS JOB IS TO VEX CHUMPS QUICKLY IN FOG",
    ],
}


class _Defaulting(dict):
    """A dict that leaves unknown placeholders alone instead of raising."""
    def __missing__(self, key):
        return "{" + key + "}"


# CW band segments (kHz) that {FREQ} draws from -- the usual watering holes.
_CW_FREQS = [
    (1800, 1840), (3500, 3600), (7000, 7060), (10100, 10130),
    (14000, 14070), (18068, 18095), (21000, 21070), (24890, 24915),
    (28000, 28070),
]


def _random_fillers():
    """Random placeholder values rolled fresh for each prompt."""
    lo, hi = random.choice(_CW_FREQS)
    return {
        "THEIRCALL":  random_callsign(),
        "THEIRSTATE": random.choice(_FAKE_STATES),
        "NUM2":       f"{random.randint(0, 99):02d}",
        "NUM3":       f"{random.randint(1, 999):03d}",
        "NUM4":       f"{random.randint(1, 9999)}",
        "NUM5":       f"{random.randint(0, 99999):05d}",
        "PARK4":      f"{random.randint(1, 9999)}",
        "FREQ":       str(random.randint(lo, hi)),
        "RST":        random.choice(["599", "579", "559", "539", "449"]),
        "PWR":        str(random.choice([5, 10, 25, 50, 100, 500])),
    }


def render(text, profile):
    """Fill {CALL}, {NAME}, etc. from `profile`; missing keys fall back to
    DEFAULT_PROFILE. {THEIRCALL}, {THEIRSTATE}, {NUM2/3/4/5}, {PARK4} are
    randomized per call."""
    vals = dict(DEFAULT_PROFILE)
    vals.update(profile or {})
    vals.update(_random_fillers())
    try:
        return text.format_map(_Defaulting(vals))
    except Exception:
        return text


class DrillPicker:
    """Deals prompts from a per-category shuffle bag: every template comes up
    once per cycle, with no repeat across the reshuffle boundary. Templates
    with random fillers additionally re-roll to dodge recently served lines --
    that's what keeps one-template categories like Random Callsigns fresh."""

    RECENT_N = 10          # remembered rendered lines per category
    MAX_REROLLS = 6        # filler re-rolls before accepting a repeat

    def __init__(self):
        self._decks = {}   # category -> templates not yet dealt this cycle
        self._last = {}    # category -> template dealt most recently
        self._recent = {}  # category -> deque of normalize(rendered)

    def pick(self, category, profile):
        pool = CATEGORIES.get(category, [])
        if not pool:
            return "", None
        deck = self._decks.get(category)
        if not deck:
            deck = pool[:]
            random.shuffle(deck)
            if len(deck) > 1 and deck[-1] == self._last.get(category):
                i = random.randrange(len(deck) - 1)
                deck[-1], deck[i] = deck[i], deck[-1]
            self._decks[category] = deck
        template = deck.pop()
        self._last[category] = template
        recent = self._recent.setdefault(category,
                                         deque(maxlen=self.RECENT_N))
        target = render(template, profile)
        if "{" in template:
            for _ in range(self.MAX_REROLLS):
                if normalize(target) not in recent:
                    break
                target = render(template, profile)
        recent.append(normalize(target))
        return target, template


_PICKER = DrillPicker()


def pick(category, profile, avoid=None):
    """Pick a rendered prompt from `category`; returns (target, template).
    `avoid` is kept for backwards compatibility but unused -- repeats are
    prevented by the shuffle bag and recent-line memory in DrillPicker."""
    return _PICKER.pick(category, profile)


# WinKeyer's Morse <-> ASCII table maps merged prosigns to a single ASCII char
# (per WK3 datasheet, "Prosign / Abbreviation Assignments"). When you send a
# prosign MERGED, the WK echoes that single char; when you send it as two
# letter-spaced letters, the echo is the letter pair. Expanding the single
# chars to letter pairs on both sides of the comparison lets either way match.
#
# Note: "BK", "HH" and the like are *operator shorthands*, not real WK
# prosigns -- they aren't in this table and must be sent letter-spaced.
_PROSIGN_EXPAND = {
    "=":  "BT",   # rag-chew separator (dash dit dit dit dash)
    "+":  "AR",   # end of message
    "<":  "AR",
    ">":  "SK",   # end of work
    ";":  "AA",   # new line
    "(":  "KN",   # over, to specific station
    ":":  "KN",
    "]":  "KN",
    "[":  "AS",   # wait / stand-by
    ")":  "KK",
    "$":  "SX",
    "@":  "AC",
    '"':  "RR",
    "'":  "WG",
    "-":  "DU",
    "/":  "DN",   # slash (e.g. K1ABC/P portable) -- merged or letter-spaced both OK
    "\\": "DN",
}


def normalize(s):
    """Canonical form for comparing target text with the WinKeyer's decode:
    uppercase, single-spaced, trimmed, with merged-prosign ASCII characters
    expanded to their letter-pair equivalents."""
    out = []
    for ch in s.upper():
        out.append(_PROSIGN_EXPAND.get(ch, ch))
    return " ".join("".join(out).split())
