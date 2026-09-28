"""QSO simulator scenarios for the WinKeyer CW Trainer.

Each scenario is an ordered list of steps. A step has:

    send    - template the app sends *over the wire* via the WinKeyer (audible
              as Morse on the WK sidetone). Filled with {CALL}/{NAME}/{QTH}/...
              from the user profile plus a *pinned* {THEIRCALL} / {THEIRNAME} /
              {THEIRQTH} / {THEIRSTATE} that stay the same for the whole QSO.

    expect  - list of token-alternatives. The user's paddle response is checked
              against each inner list: the response must contain at least one
              alt from every inner list (after normalization). Order doesn't
              matter; extra wording is fine.

    hint    - one-line description shown when it's the user's turn.

Render the scenario once with `render_scenario(steps, profile)`; that pins the
fake op's identity so the conversation stays internally consistent.
"""

import random

from drills import (DEFAULT_PROFILE, _FAKE_STATES, _Defaulting, normalize,
                    random_callsign)


_FAKE_NAMES = ["DAN", "JIM", "MIKE", "TOM", "STEVE", "BOB", "BILL", "DAVE",
               "JOHN", "MARY", "ANN", "PAUL", "DOUG", "RICK", "PETE", "GARY",
               "FRANK", "ED", "AL", "LARRY"]

_FAKE_QTHS = ["DAYTON", "AUSTIN", "DENVER", "TAMPA", "BOSTON", "SEATTLE",
              "PORTLAND", "RALEIGH", "PHOENIX", "ATLANTA", "DETROIT",
              "MEMPHIS", "TUCSON", "ORLANDO", "RICHMOND", "OMAHA"]

_FAKE_RIGS = ["K3", "FT-991A", "IC-7300", "FTDX10", "KX3", "TS-590", "FLEX 6400"]


def render_scenario(steps, profile):
    """Render all templates in a scenario with a *single* pinned identity for
    the fake operator -- THEIRCALL / THEIRNAME / THEIRQTH stay constant across
    the whole conversation."""
    vals = dict(DEFAULT_PROFILE)
    vals.update(profile or {})
    vals["THEIRCALL"]  = random_callsign(dx_chance=0.0)
    vals["THEIRSTATE"] = random.choice(_FAKE_STATES)
    vals["THEIRNAME"]  = random.choice(_FAKE_NAMES)
    vals["THEIRQTH"]   = random.choice(_FAKE_QTHS)
    vals["THEIRRIG"]   = random.choice(_FAKE_RIGS)

    rendered = []
    for step in steps:
        send = step["send"].format_map(_Defaulting(vals))
        expect = [[alt.format_map(_Defaulting(vals)) for alt in alts]
                  for alts in step["expect"]]
        rendered.append({
            "send":   send,
            "expect": expect,
            "hint":   step.get("hint", ""),
        })
    return rendered, vals["THEIRCALL"]


def matches(buffer, expectations):
    """True if `buffer` (normalized) contains at least one alt from every
    expectation. Order doesn't matter; extra content is fine."""
    nb = normalize(buffer)
    return all(any(normalize(alt) in nb for alt in alts)
               for alts in expectations)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
SCENARIOS = {

    "Short rag chew": [
        {
            "send":   "CQ CQ CQ DE {THEIRCALL} {THEIRCALL} K",
            "expect": [["{THEIRCALL}"], ["DE"], ["{CALL}"]],
            "hint":   "Answer the CQ — their call, DE, your call.",
        },
        {
            "send":   "{CALL} DE {THEIRCALL} = GA TU FER CALL = UR RST 599 599 "
                      "= NAME HR IS {THEIRNAME} {THEIRNAME} = QTH {THEIRQTH} "
                      "{THEIRQTH} = HW? {CALL} DE {THEIRCALL} K",
            "expect": [["{THEIRCALL}"], ["{CALL}"], ["599", "5NN"],
                       ["{NAME}"], ["{QTH}"]],
            "hint":   "Their call DE yours, then your RST, name, QTH.",
        },
        {
            "send":   "{CALL} DE {THEIRCALL} = FB {NAME} TU FER INFO "
                      "= 73 ES GL = {CALL} DE {THEIRCALL} SK",
            "expect": [["73"], ["SK"]],
            "hint":   "Say 73 and sign with SK.",
        },
    ],

    "Full rag chew": [
        {
            "send":   "CQ CQ CQ DE {THEIRCALL} {THEIRCALL} K",
            "expect": [["{THEIRCALL}"], ["DE"], ["{CALL}"]],
            "hint":   "Answer the CQ — their call, DE, your call, K.",
        },
        {
            "send":   "{CALL} DE {THEIRCALL} = GA TU FER CALL = UR RST 599 599 "
                      "= NAME HR IS {THEIRNAME} {THEIRNAME} = QTH {THEIRQTH} "
                      "{THEIRQTH} = HW? {CALL} DE {THEIRCALL} K",
            "expect": [["{THEIRCALL}"], ["{CALL}"], ["599", "5NN"],
                       ["{NAME}"], ["{QTH}"]],
            "hint":   "Their call, your call, your RST, name, QTH.",
        },
        {
            "send":   "{CALL} DE {THEIRCALL} = FB {NAME} = RIG HR IS "
                      "{THEIRRIG} {THEIRRIG} = ANT IS DIPOLE = WX FB ES "
                      "TEMP 70F = HW? {CALL} DE {THEIRCALL} K",
            "expect": [["{RIG}"], ["{ANT}"], ["{THEIRCALL}"], ["{CALL}"]],
            "hint":   "Tell them about your rig and antenna.",
        },
        {
            "send":   "{CALL} DE {THEIRCALL} = TU OM FER NICE QSO = 73 ES "
                      "GL = HPE CUAGN = {CALL} DE {THEIRCALL} SK",
            "expect": [["73"], ["SK"]],
            "hint":   "Wrap up the QSO — 73 and SK.",
        },
    ],

    "POTA hunter": [
        {
            "send":   "CQ POTA CQ POTA DE {THEIRCALL} {THEIRCALL} K",
            "expect": [["{CALL}"]],
            "hint":   "You're hunting — send your call once.",
        },
        {
            "send":   "{CALL} UR 599 599 {THEIRSTATE} {THEIRSTATE} BK",
            "expect": [["{THEIRCALL}"], ["599", "5NN"], ["{STATE}"]],
            "hint":   "Reply: their call, RST, your state.",
        },
        {
            "send":   "TU {STATE} 73 EE",
            "expect": [["73"]],
            "hint":   "Wrap up — say 73.",
        },
    ],

    "POTA activator": [
        {
            "send":   "{CALL}",
            "expect": [["{THEIRCALL}"], ["599", "5NN"], ["{STATE}"]],
            "hint":   "You're activating. A hunter just sent your call — give "
                      "them their call, 599, your state.",
        },
        {
            "send":   "5NN {THEIRSTATE} BK",
            "expect": [["TU"], ["73"]],
            "hint":   "They gave their state — say TU 73.",
        },
        {
            "send":   "CQ POTA DE {CALL} {CALL} K",
            "expect": [["CQ"], ["POTA"], ["{CALL}"]],
            "hint":   "Back to calling CQ POTA.",
        },
    ],

    "Contest sprint (S&P)": [
        {
            "send":   "CQ TEST DE {THEIRCALL} {THEIRCALL}",
            "expect": [["{CALL}"]],
            "hint":   "Search-and-pounce: send your call once.",
        },
        {
            "send":   "{CALL} 5NN {THEIRSTATE}",
            "expect": [["5NN", "599"], ["{STATE}"]],
            "hint":   "Send 5NN and your state.",
        },
        {
            "send":   "TU {THEIRCALL}",
            "expect": [["TU"]],
            "hint":   "Acknowledge with TU.",
        },
    ],
}
