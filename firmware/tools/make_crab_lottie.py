#!/usr/bin/env python3
"""Generate the crab mascot's expressions as Lottie animations + a C asset file.

    python tools/make_crab_lottie.py            # writes src/crab_assets.{c,h}
    python tools/make_crab_lottie.py --json out # also dump the .json files

Written from scratch rather than sourced online: Anthropic's "Clawd" mascot is
their IP with no free licence, and the community re-creations carry
non-commercial terms. This is a generic crab, so there's nothing to attribute.

Deliberately blocky — every shape is a rounded rect, filled, never stroked.
ThorVG rasterises vector paths in software on this board and already sits near
90% CPU while animating, so shape count and path complexity are what matter.
Curves and gradients would cost far more than they add at 80x80.

NO STROKES. Lottie strokes were tried for a heavy-outline art style and ThorVG
has to build RLE spans for the stroke geometry: ten stroked shapes killed the
device with `_horizLine: Asserted at expression: rle->spans != NULL (Out of
memory)` (tvgSwRle.cpp:363) before it finished one mood cycle. Faking an
outline with a larger rect behind each shape works but costs a whole group per
shape, and overlapping fills are already the most expensive thing here.

Moods are chosen by the firmware from model / effort / session quota; see
CrabMood in src/crab_assets.h.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

W = H = 80
FPS = 30
DUR = 60  # frames — 2s loop

SHELL = [0.878, 0.396, 0.267]
SHELL_DARK = [0.729, 0.298, 0.196]
EYE_WHITE = [1.0, 1.0, 1.0]
EYE_DARK = [0.106, 0.098, 0.094]

# name -> knobs. Kept as plain data so a new mood is one line, not new code.
#
# Each mood must differ by a *categorical* feature, not by a few pixels. An
# earlier version varied only eye height (10/7/5px) and mouth width, and on a
# 112px round display focused/sleepy were indistinguishable and happy read as
# neutral. Brows, eye shape and props do the work now; size alone does not.
#
#   eye      : "open" | "wide" | "squint" | "narrow" | "happy" (^^ arcs)
#              | "droop" | "closed"
#   brow     : None | "angry" (inner ends down) | "furrow" (a mild angry)
#              | "tired" (outer ends down) | "raised"
#   pupil_dx : pupil offset, for looking off to one side
#   mouth    : "grin" | "flat" | "small" | "open" | "smile"
#   wave     : claw swing in degrees (0 = arms down)
#   bob      : body bob in px
#   speed    : layer time stretch (>1 = slower)
#   loop     : loop length in frames (default DUR). Only worth raising for a
#              mood with an occasional one-off action, so it does not repeat
#              every two seconds.
#   sip      : desk scene only - lift the mug to the face once per loop
#   blush    : cheek patches
#   zzz      : floating sleep marks
#   dots     : "waiting" dots above the head, lighting in sequence
#   knight   : chibi-knight build - the helm REPLACES the head (Fable)
#   dragon   : sword + dragon, for the Fable fight
MOODS: dict[str, dict] = {
    # Eyes shut in happy arcs + big grin + blush + a lively bounce. Nothing
    # else uses arc eyes, so this is unmistakable at a glance.
    "happy":   dict(eye="happy",  brow=None,    pupil_dx=0, mouth="grin",  wave=18, bob=3,
                    speed=0.9, blush=True),
    # Hard angry-angled brows over narrow slits, and almost no movement — the
    # stillness is itself a signal next to happy's bounce.
    "focused": dict(eye="narrow", brow="angry", pupil_dx=0, mouth="flat",  wave=6,  bob=1,
                    speed=0.7),
    # Wide eyes glancing aside under raised brows, easy sway.
    "chill":   dict(eye="open",   brow="raised", pupil_dx=3, mouth="smile", wave=12, bob=2,
                    speed=1.4),
    # Heavy lids covering the top half of each eye + outward-drooping brows +
    # one drifting z. Reads as drowsy rather than merely squinting.
    "sleepy":  dict(eye="droop",  brow="tired", pupil_dx=0, mouth="small", wave=4,  bob=1,
                    speed=2.2, zzz=1),
    # Fully shut, claws tucked, three z's — clearly out cold, not just resting.
    "asleep":  dict(eye="closed", brow="tired", pupil_dx=0, mouth="small", wave=0,  bob=2,
                    speed=3.0, zzz=3, bed=True),
    # Reserved for xhigh effort, echoing the guitar-strumming Clawd that Claude
    # Code itself shows at that level. One claw pins the neck, the other strums.
    "rocking": dict(eye="narrow", brow="angry", pupil_dx=0, mouth="open",  wave=0,  bob=2,
                    speed=0.8, guitar=True),
    # Waiting on you. Three dots lighting in sequence above the head - the same
    # staggered-opacity trick as the zzz marks, which is the cheapest kind of
    # animation this generator has. The dots are what make it categorical:
    # without them this is chill's face, and chill is the mood it would
    # otherwise be confused with. Gaze is straight ahead (pupil_dx=0) where
    # chill glances aside.
    "idle":    dict(eye="open",   brow="raised", pupil_dx=0, mouth="smile", wave=7,  bob=2,
                    speed=1.6, dots=3),
    # Fable, low/medium effort: armoured and idling. Deliberately brow=None -
    # the helmet rim sits exactly where brows would be, so drawing both put two
    # overlapping fills in the same place for no visible gain, and overlapping
    # fills are the expensive kind here.
    "fable_calm":  dict(eye="wide",   brow=None,   pupil_dx=0, mouth="smile", wave=9, bob=0,
                        speed=1.3, knight=True),
    # Fable, high/xhigh: sword drawn, swinging at a dragon. The red fire stage
    # behind it lives in ui.cpp so it can fill the panel; its pulse is locked to
    # FABLE_STRIKES. The dragon itself is deliberately static - the sword and
    # the stage carry the motion, and keyframes are what cost heap.
    "fable_fight": dict(eye="wide",   brow=None,   pupil_dx=0, mouth="open",  wave=0, bob=0,
                        speed=0.8, knight=True, dragon=True),
    # Heads-down at a desk: laptop open, claws typing, coffee steaming. The
    # crab is raised (body_dy) so the desk and laptop occupy the lower third.
    # Hunched down behind the screen: body_dy is low enough that the laptop lid
    # covers the mouth entirely, so the expression is carried by squinted eyes
    # and lightly furrowed brows. The mouth is still drawn (it sits behind the
    # lid in the z-order) but deliberately kept to the cheapest "flat" shape,
    # since nothing that is never seen should cost heap on this board.
    # bob=0 deliberately: the typing claws and the steam already carry the
    # motion, and the body bob is what makes an asset expensive (see below).
    "working": dict(eye="squint", brow="furrow", pupil_dx=0, mouth="flat",  wave=0,  bob=0,
                    speed=0.9, desk=True, body_dy=14, loop=180, sip=True),
}

ZZZ_COLOR = [0.949, 0.949, 0.980]
PILLOW = [0.925, 0.906, 0.859]
BLANKET = [0.259, 0.408, 0.729]
BLANKET_DARK = [0.192, 0.310, 0.588]

# Desk scene. Cool greys and a teal screen glow so the laptop separates from
# the orange crab, the same reasoning that drove the guitar's colour.
DESK = [0.298, 0.204, 0.157]
DESK_EDGE = [0.216, 0.145, 0.110]
LAPTOP = [0.400, 0.435, 0.478]
LAPTOP_DARK = [0.267, 0.298, 0.341]
SCREEN_GLOW = [0.400, 0.855, 0.855]
MUG = [0.949, 0.949, 0.965]
COFFEE = [0.322, 0.192, 0.129]
STEAM = [0.878, 0.902, 0.925]

# Teal body, cream neck. Wood-brown was the obvious choice and the wrong one:
# against an orange crab it read as another shade of crab. Teal is orange's
# complement so it separates from the body, and cream keeps the neck legible
# where it crosses the dark purple backdrop.
GUITAR_BODY = [0.106, 0.737, 0.706]
GUITAR_NECK = [0.949, 0.898, 0.780]

# NOTE: the rocking mood's purple stage lives in ui.cpp (see kStageBg /
# kStageGrid) so it can cover the full panel, not just this 80x80 canvas.
# Lottie rotation is clockwise (screen y grows downward), so a positive tilt
# swings the neck up and to the right. 75 is deliberately shallow: at a
# steeper, more natural-looking angle the neck runs straight through the right
# eye, and the eyes are what carry the expression.
GUITAR_TILT = 75

# Fable's medieval kit. Steel is a cold grey-blue so it separates from the
# orange shell, the same complement logic that drove the guitar's teal.
STEEL = [0.769, 0.804, 0.847]
STEEL_DARK = [0.478, 0.525, 0.596]
STEEL_SHADE = [0.196, 0.227, 0.286]
# Every knight shape carries this outline. Not pure black - a very dark warm
# brown sits better against both the steel and the orange claws.
OUTLINE = [0.129, 0.094, 0.086]
VISOR = [0.106, 0.086, 0.082]
PLUME = [0.780, 0.220, 0.239]
BLADE = [0.988, 0.867, 0.541]
FLAME = [0.902, 0.412, 0.153]
DRAGON = [0.176, 0.353, 0.259]
DRAGON_DARK = [0.106, 0.239, 0.176]
DRAGON_EYE = [0.980, 0.749, 0.204]

# Sword strikes per loop. The fire stage in ui.cpp pulses once per strike, so
# this number and kFirePulseMs there are two halves of one decision - the brief
# was that the pulse tempo matches the sword hitting the dragon. Change one and
# you must change the other. Four strikes over the 2s loop = 500ms per strike =
# 250ms each way, which is exactly what the rocking stage already uses.
FABLE_STRIKES = 4


def rgb(c):
    return {"a": 0, "k": list(c) + [1]}


def static(v):
    return {"a": 0, "k": v}


def anim(frames):
    out = []
    for i, (t, v) in enumerate(frames):
        kf = {"t": round(t), "s": v}
        if i < len(frames) - 1:
            kf["i"] = {"x": [0.5], "y": [1]}
            kf["o"] = {"x": [0.5], "y": [0]}
        out.append(kf)
    return {"a": 1, "k": out}


def rect(w, h, roundness, offset=(0.0, 0.0)):
    return {"ty": "rc", "p": static([offset[0], offset[1]]), "s": static([w, h]),
            "r": static(roundness), "nm": "rc"}


def fill(color, opacity=None):
    return {"ty": "fl", "c": rgb(color),
            "o": opacity if opacity is not None else static(100), "r": 1, "nm": "fl"}


def group(name, shapes, pos, rot=None, offset=(0.0, 0.0)):
    """A shape group. `pos` is the pivot; `rot` (if given) turns about it."""
    return {"ty": "gr", "nm": name, "it": shapes + [{
        "ty": "tr", "p": static(list(pos)), "a": static(list(offset)),
        "s": static([100, 100]), "r": rot if rot is not None else static(0),
        "o": static(100), "sk": static(0), "sa": static(0)}]}


def zzz_groups(count: int, dur: float) -> list:
    """Floating "z" marks at fixed positions, fading in and out in sequence.

    A Z needs a rotated diagonal, and rotation in Lottie is per-group, so each
    letter costs three groups (top bar, diagonal, bottom bar).

    Deliberately OPACITY-ONLY. A first version also animated position so the
    letters drifted upward, but five position keyframes per group tripled the
    asleep animation's size (16.7KB) and left only 15KB of heap free on the
    device once ThorVG had parsed it - close enough to exhaustion to risk a
    failed allocation. Staggered fading reads as snoozing just as well and
    costs a fraction as much: the letters sit still, but they still blink on
    in sequence, which is what carries the effect.
    """
    out = []
    for i in range(count):
        # Bigger and higher as the sequence progresses, so the static letters
        # still imply upward drift.
        size = 5 + i * 2.5
        x, y = 58 + i * 5, 30 - i * 9

        # One short "on" window per letter, offset so they light in turn.
        slot = dur / max(1, count)
        t_on = i * slot
        pts = [(0, [0])] if t_on > 0 else []
        pts += [(t_on, [0]), (t_on + slot * 0.35, [100]),
                (t_on + slot * 0.9, [0])]
        if pts[-1][0] < dur:
            pts.append((dur, [0]))
        o = anim(pts)

        half = size / 2
        out.append(group(f"z{i}t", [rect(size, 1.8, 0), fill(ZZZ_COLOR, o)], (x, y - half)))
        out.append(group(f"z{i}d", [rect(1.8, size * 1.3, 0), fill(ZZZ_COLOR, o)],
                         (x, y), static(45)))
        out.append(group(f"z{i}b", [rect(size, 1.8, 0), fill(ZZZ_COLOR, o)], (x, y + half)))
    return out


def build(mood: str) -> dict:
    m = MOODS[mood]
    cx = W / 2
    # Shifts the whole crab up so a desk scene can occupy the lower third.
    # Applied at construction rather than as a layer transform so the bob
    # animation, which is built from these same base positions, follows it.
    dy = m.get("body_dy", 0)
    dur = m.get("loop", DUR)
    q = [0, dur * 0.25, dur * 0.5, dur * 0.75, dur]

    def swing(sign):
        a = m["wave"] * sign
        return anim([(q[0], [-a]), (q[1], [a]), (q[2], [-a]), (q[3], [a]), (q[4], [-a])])

    def bob_for(base):
        x, y = base
        d = m["bob"]
        return anim([(q[0], [x, y]), (q[1], [x, y - d]), (q[2], [x, y]),
                     (q[3], [x, y - d]), (q[4], [x, y])])

    # Face, built front-to-back. Each eye style is a different *shape*, not the
    # same shape at a different size — that was the readability problem.
    eye = m["eye"]
    face = []

    if eye == "happy":
        # Two angled bars per eye forming a "^". No other mood uses arcs, so
        # this alone identifies happy even at a glance.
        for dx in (-10, 10):
            for side, tilt in ((-1, -32), (1, 32)):
                face.append(group(f"arc{dx}{side}", [rect(7, 3, 1), fill(EYE_DARK)],
                                  (cx + dx + side * 2.6, 42 - dy), static(tilt)))
    elif eye == "closed":
        for dx in (-10, 10):
            face.append(group(f"lid{dx}", [rect(12, 3, 1), fill(EYE_DARK)], (cx + dx, 43 - dy)))
    else:
        # "squint" is a short eye with a WIDE pupil. The width matters: a short
        # eye with the standard 5px pupil is still a small dark dot swimming in
        # white, which reads as a stare, not as half-closed.
        #
        # The obvious alternative - a shell-coloured lid over a full-height eye,
        # the way "droop" does it - is far too expensive here. Those lids
        # overlap the eye whites, and ThorVG composites overlapping fills: four
        # extra shapes cost ~17KB of heap at parse time and reliably exhausted
        # the device on the desk scene. Compare "chill", which adds raised brows
        # (no overlap) for almost nothing. Avoid overlapping fills on any mood
        # that also carries a prop scene.
        eye_h = {"open": 11, "squint": 7, "narrow": 6, "droop": 9, "wide": 14}[eye]
        pupil_w = {"squint": 7, "wide": 7}.get(eye, 5)
        pupil_h = 7 if eye == "wide" else min(5, eye_h - 1)
        eye_w = 14 if eye == "wide" else 11
        pdx = m["pupil_dx"]
        # Droop keeps a full-height eye but covers the top half with a lid, so
        # you read "eyelid", not "small eye". It can afford the overlap because
        # it has no prop scene competing for heap.
        pupil_y = 43 if eye == "droop" else 41
        for dx in (-10, 10):
            face.append(group(f"pupil{dx}", [rect(pupil_w, pupil_h, 3), fill(EYE_DARK)],
                              (cx + dx + pdx, pupil_y - dy)))
        if eye == "droop":
            for dx in (-10, 10):
                face.append(group(f"droop{dx}", [rect(12, 5, 2), fill(SHELL)], (cx + dx, 38 - dy)))
        for dx in (-10, 10):
            face.append(group(f"eye{dx}", [rect(eye_w, eye_h, 5), fill(EYE_WHITE)], (cx + dx, 41 - dy)))

    # Brows carry more expression than eye size does, especially at this size.
    #
    # Sign matters and is easy to get backwards: Lottie rotates clockwise, so
    # for "angry" (inner ends DOWN) the left brow must rotate positively and
    # the right negatively — hence the negative base tilt against the -1/+1
    # side multiplier below. Inverted, angry reads as worried and tired reads
    # as angry, which is exactly how focused and sleepy first came out.
    brow = m.get("brow")
    if brow:
        tilt = {"angry": -22, "furrow": -10, "tired": 22, "raised": 0}[brow]
        brow_y = {"angry": 33, "furrow": 32, "tired": 32, "raised": 30}[brow]
        for side, dx in ((-1, -10), (1, 10)):
            face.append(group(f"brow{dx}", [rect(12, 3, 1), fill(SHELL_DARK)],
                              (cx + dx, brow_y - dy), static(tilt * side)))

    if m.get("blush"):
        for dx in (-18, 18):
            face.append(group(f"blush{dx}", [rect(7, 4, 2), fill(SHELL_DARK)], (cx + dx, 48 - dy)))

    if m["mouth"] == "smile":
        # Two bars angled up at the outer ends, not one wide rect. A rect only
        # ever reads as a neutral bar however thin it is, so "smile" and "flat"
        # were telling the same story and a smiling crab looked expressionless.
        # Sign is the same trap as the brows. Rotation is clockwise, so +angle
        # drives a bar's RIGHT end down: the left bar therefore needs +16 to
        # lift its outer end and the right bar -16. Inverted, this is a frown -
        # compare the happy arc eyes, which use the opposite sign to make "^^".
        mouths = [group(f"smile{side}", [rect(8, 3, 1), fill(SHELL_DARK)],
                        (cx + side * 3.2, 51 - dy), static(-side * 16))
                  for side in (-1, 1)]
    else:
        mouth_shape = {
            "grin":  (18, 7, 3, 52),   # wide and tall - pairs with the arc eyes
            "flat":  (11, 3, 1, 51),
            "small": (7, 3, 1, 51),
            "open":  (9, 7, 3, 51),
        }[m["mouth"]]
        mouths = [group("mouth", [rect(*mouth_shape[:3]), fill(SHELL_DARK)],
                        (cx, mouth_shape[3] - dy))]

    legs = [group(f"leg{i}", [rect(5, 7, 2), fill(SHELL_DARK)], (cx + dx, 61 - dy))
            for i, dx in enumerate((-14, -5, 5, 14))]

    shell = group("shell", [rect(44, 32, 11), fill(SHELL)], (cx, 44 - dy))

    if m.get("guitar"):
        # Strum runs at 4x the loop rate — a slow strum reads as waving, which
        # is the one thing it must not look like.
        strum = anim([(q[0], [-26]), (dur * 0.125, [14]), (q[1], [-26]),
                      (dur * 0.375, [14]), (q[2], [-26]), (dur * 0.625, [14]),
                      (q[3], [-26]), (dur * 0.875, [14]), (q[4], [-26])])
        # Guitar rides low, below the mouth line — high enough and the mouth
        # ends up sitting on top of the guitar body, which reads as a smudge.
        gx, gy = cx - 7, 63
        limbs = [
            # Strumming claw sweeps over the body, hinged at the shoulder. Kept
            # small so it doesn't swallow the neck it's meant to be strumming.
            group("claw_strum", [rect(11, 9, 4, offset=(-6, 0)), fill(SHELL_DARK)],
                  (cx + 2, 60), strum),
            # Fretting claw pins the far end of the neck, held still.
            group("claw_hold", [rect(10, 9, 4), fill(SHELL_DARK)], (cx + 20, 55)),
            group("neck", [rect(5, 26, 2, offset=(0, -17)), fill(GUITAR_NECK)],
                  (gx, gy), static(GUITAR_TILT)),
            group("gbody", [rect(19, 16, 7), fill(GUITAR_BODY)],
                  (gx, gy), static(GUITAR_TILT)),
        ]
    elif m.get("dragon"):
        # One strike per FABLE_STRIKES of the loop. The blade is hinged at the
        # claw and swings down and to the right, into the dragon.
        step = dur / FABLE_STRIKES
        pts = []
        for i in range(FABLE_STRIKES):
            pts += [(step * i, [-12]), (step * (i + 0.45), [52])]
        pts.append((dur, [-12]))
        swing_sword = anim(pts)
        sx, sy = cx + 17, 49 - dy
        limbs = [
            # Sword: blade and crossguard share the claw as their pivot, so they
            # swing as one piece. Same rigid-body rule as the coffee mug.
            group("blade", [rect(7, 31, 3, offset=(0, -18)), fill(FLAME)], (sx, sy), swing_sword),
            group("guard", [rect(14, 5, 2, offset=(0, -3)), fill(STEEL_DARK)], (sx, sy),
                  swing_sword),
            group("claw_sword", [rect(11, 9, 4), fill(SHELL_DARK)], (sx, sy), swing_sword),
        ]
    else:
        # Asleep tucks the claws down against the body instead of holding them out.
        claw_y = 50 if m["wave"] == 0 else 44
        limbs = [
            group("claw_l", [rect(16, 14, 5, offset=(-8, 0)), fill(SHELL_DARK)],
                  (cx - 18, claw_y - dy), None if m["wave"] == 0 else swing(1)),
            group("claw_r", [rect(16, 14, 5, offset=(8, 0)), fill(SHELL_DARK)],
                  (cx + 18, claw_y - dy), None if m["wave"] == 0 else swing(-1)),
        ]

    # Medieval kit. Helmet and breastplate necessarily overlap the shell, so
    # this is kept to four rects and no brows - overlapping fills are what
    # ThorVG charges most for.
    # Chibi-knight build. This REPLACES the head rather than layering plates
    # over it: in the reference art the helmet IS the head, with a single dark
    # visor slit for a face and no features underneath. That is both truer to
    # the style and cheaper - it drops the eyes, pupils, mouth, nasal and cheek
    # plates entirely, which is five overlapping groups gone.
    #
    # Proportions are deliberately top-heavy: an oversized helm, stubby legs,
    # and the crab's own claws left orange at the sides so it still reads as
    # this project's crab wearing a helm rather than a different character.
    knight = []
    if m.get("knight"):
        # FULL helm - the helmet is the head, not a plate worn over it. An
        # open-faced version was tried and lost the silhouette that makes this
        # read as a knight at 96px through a round bezel.
        #
        # The visor is NOT a plain dark slit. On its own it was exactly that: a
        # black rectangle with nothing in it. The slit now frames a strip of the
        # crab's own orange with two eyes on it, so the character still shows
        # through the armour - which is the whole point of it being Clawd in a
        # helm rather than a generic knight.
        sway = anim([(q[0], [-7]), (q[1], [7]), (q[2], [-7]), (q[3], [7]), (q[4], [-7])])
        rivets = [
            group(f"rivet{i}", [rect(4, 4, 2), fill(STEEL_SHADE)], (cx - 15 + i * 10, 21))
            for i in range(4)
        ]
        eye_dx = 9
        knight = [
            *[group(f"kpupil{d}", [rect(5, 6, 2), fill(EYE_DARK)], (cx + d, 40))
              for d in (-eye_dx, eye_dx)],
            *[group(f"keye{d}", [rect(10, 8, 4), fill(EYE_WHITE)], (cx + d, 40))
              for d in (-eye_dx, eye_dx)],
            group("kface", [rect(32, 10, 3), fill(SHELL)], (cx, 40)),
            group("visor", [rect(37, 14, 4), fill(VISOR)], (cx, 40)),
            *rivets,
            group("plume", [rect(9, 14, 4, offset=(0, -6)), fill(PLUME)], (cx, 16), sway),
            group("helm", [rect(50, 44, 13), fill(STEEL)], (cx, 35)),
            group("boot_l", [rect(13, 11, 5), fill(STEEL_DARK)], (cx - 11, 60)),
            group("boot_r", [rect(13, 11, 5), fill(STEEL_DARK)], (cx + 11, 60)),
        ]

    armour = []
    if m.get("armour"):
        # A first version was a flat slab across the forehead plus a wide plate
        # over the chest, which read as a bandage and a bib. What makes a helm
        # legible at this size is not the dome - it is the DARK VISOR SLIT sitting
        # right on the brow line, with cheek plates framing the face. The dome
        # then stops just above the eyes rather than crossing them, because the
        # eyes still have to carry the expression.
        #
        # The plume sways on its own so the mood has some life. The body cannot
        # bob here: armour is rigid, and it is not in the bob list, so a bobbing
        # head slid straight out from under a stationary helmet.
        sway = anim([(q[0], [-7]), (q[1], [7]), (q[2], [-7]), (q[3], [7]), (q[4], [-7])])
        armour = [
            group("nasal", [rect(5, 11, 2), fill(STEEL_DARK)], (cx, 41 - dy)),
            group("visor", [rect(41, 5, 2), fill(STEEL_SHADE)], (cx, 34 - dy)),
            group("plume", [rect(6, 15, 3, offset=(0, -7)), fill(PLUME)], (cx, 19 - dy), sway),
            group("dome", [rect(44, 17, 8), fill(STEEL)], (cx, 26 - dy)),
            group("cheek_l", [rect(7, 13, 3), fill(STEEL)], (cx - 17, 42 - dy)),
            group("cheek_r", [rect(7, 13, 3), fill(STEEL)], (cx + 17, 42 - dy)),
            group("gorget", [rect(28, 8, 4), fill(STEEL_DARK)], (cx, 57 - dy)),
        ]

    # The dragon looms in from the upper right, where the sword swings. Static
    # by design; the blade and the fire stage carry the motion.
    dragon = []
    if m.get("dragon"):
        dragon = [
            group("dreye", [rect(7, 7, 4), fill(DRAGON_EYE)], (65, 13)),
            group("drsnout", [rect(16, 9, 4), fill(DRAGON_DARK)], (52, 22)),
            group("drhead", [rect(26, 20, 9), fill(DRAGON)], (65, 16)),
        ]

    # NOTE: the purple stage and its pulsing grid are NOT drawn here. They live
    # in ui.cpp as plain LVGL objects so they can fill the whole 240x240 panel —
    # a Lottie canvas that size would need 230KB (4 bytes/px) against ~44KB of
    # free heap. Drawing them natively is also far cheaper than making ThorVG
    # rasterise them, and keeps this animation's shape count down.

    zzz = zzz_groups(m["zzz"], dur) if m.get("zzz") else []

    # Waiting dots. Same staggered opacity as zzz_groups but round and in a row
    # above the head, so it reads as "thinking of you" rather than "asleep".
    dots = []
    for i in range(m.get("dots", 0)):
        slot = dur / max(1, m["dots"])
        t_on = i * slot
        pts = [(0, [25])] if t_on > 0 else []
        pts += [(t_on, [25]), (t_on + slot * 0.35, [100]), (t_on + slot * 0.9, [25])]
        if pts[-1][0] < dur:
            pts.append((dur, [25]))
        dots.append(group(f"dot{i}", [rect(6, 6, 3), fill(ZZZ_COLOR, anim(pts))],
                          (cx - 10 + i * 10, 16 - dy)))

    # Desk scene: laptop lid in front of the raised body, claws tapping on the
    # base, a steaming mug beside it.
    desk_front, desk_back = [], []
    if m.get("desk"):
        # Claws alternate so it reads as typing rather than one arm waving.
        # Eight strokes per loop, not four: at four the rise and fall are slow
        # enough to read as a wave, which is what "rocking" already looks like.
        taps = max(8, round(8 * dur / DUR))
        tap_a = anim([(dur * i / taps, [0 if i % 2 == 0 else -4])
                      for i in range(taps + 1)])
        tap_b = anim([(dur * i / taps, [-4 if i % 2 == 0 else 0])
                      for i in range(taps + 1)])

        def tap(base_x, base_y, track):
            g = group(f"typeclaw{base_x}", [rect(9, 8, 3), fill(SHELL_DARK)], (base_x, base_y))
            g["it"][-1]["p"] = anim([(t, [base_x, base_y + v[0]])
                                     for (t, v) in zip([k["t"] for k in track["k"]],
                                                       [k["s"] for k in track["k"]])])
            return g

        # Steam: two short marks fading in turn above the mug.
        def puff(i):
            slot = dur / 2
            t_on = i * slot
            pts = [(0, [0])] if t_on > 0 else []
            pts += [(t_on, [0]), (t_on + slot * 0.4, [85]), (t_on + slot * 0.95, [0])]
            if pts[-1][0] < dur:
                pts.append((dur, [0]))
            return anim(pts)

        # The occasional coffee break. Phases as fractions of the loop: type,
        # lift, drink, lower, type. The long typing stretch before the lift is
        # the whole point - a sip every loop reads as a nervous tic, so the
        # mood carries a longer `loop` and the sip occupies a slice of it.
        #
        # Deliberately only three moving groups (the mug parts) plus a
        # re-pointed claw. Position keyframes are the expensive kind, so the
        # steam and the right claw are left alone to carry on regardless.
        sip = m.get("sip")
        t_lift, t_up, t_down, t_rest = (dur * f for f in (0.70, 0.76, 0.88, 0.94))

        def sip_track(rest, up):
            """Hold at rest, lift, hold at the face, lower, hold at rest."""
            (rx, ry), (ux, uy) = rest, up
            return anim([(0, [rx, ry]), (t_lift, [rx, ry]), (t_up, [ux, uy]),
                         (t_down, [ux, uy]), (t_rest, [rx, ry]), (dur, [rx, ry])])

        # Every mug part shares ONE pivot and carries its own offset from it,
        # so a single rotation tips the whole cup as a rigid body. Rotating
        # each part about its own centre instead makes the handle spin off the
        # mug - Lottie rotates a group about its own transform position, so a
        # common pivot is the only way to keep an assembly together.
        mug_rest, mug_up = (12, 56), (24, 38)
        hold = t_down - t_up
        # Clockwise is positive and the face is to the RIGHT of the raised mug,
        # so a positive angle tips the rim towards the mouth. Negative would
        # pour it down the crab's back.
        tilt = anim([(0, [0]), (t_up, [0]), (t_up + hold * 0.3, [26]),
                     (t_up + hold * 0.75, [26]), (t_down, [0]), (dur, [0])])

        def mug_part(name, shape_args, color, off):
            w, h, r = shape_args
            g = group(name, [rect(w, h, r, offset=off), fill(color)], mug_rest)
            if sip:
                g["it"][-1]["p"] = sip_track(mug_rest, mug_up)
                g["it"][-1]["r"] = tilt
            return g

        def sip_claw(base_x, base_y, track, up):
            """Left claw: taps, then leaves the keyboard to hold the mug."""
            if not sip:
                return tap(base_x, base_y, track)
            g = group("sipclaw", [rect(9, 8, 3), fill(SHELL_DARK)], (base_x, base_y))
            pts = [(t, [base_x, base_y + v[0]])
                   for (t, v) in zip([k["t"] for k in track["k"]],
                                     [k["s"] for k in track["k"]])
                   if t < t_lift]
            pts += [(t_lift, [base_x, base_y]), (t_up, list(up)), (t_down, list(up)),
                    (t_rest, [base_x, base_y]), (dur, [base_x, base_y])]
            g["it"][-1]["p"] = anim(pts)
            return g

        # Front to back. Claws must come BEFORE the lid and base or they
        # render on top of the screen instead of resting on the keyboard.
        # The laptop is centred on cx and its lid is narrower than the 44px
        # shell, so a few px of crab shows either side. Off-centre or full
        # width, the head reads as balanced ON the laptop rather than behind it.
        #
        # The raised position stops just BELOW the eyes. Higher and the mug
        # covers them, and the eyes are what carry the expression - the same
        # reason the guitar neck is kept shallow.
        desk_front = [
            mug_part("coffee", (9, 3, 1), COFFEE, (0, -3)),
            mug_part("mug", (12, 11, 3), MUG, (0, 0)),
            mug_part("handle", (4, 6, 2), MUG, (8, 0)),
            sip_claw(cx - 12, 59, tap_a, (26, 47)),
            tap(cx + 12, 59, tap_b),
            group("lapbase", [rect(40, 5, 2), fill(LAPTOP)], (cx, 60)),
            group("lapglow", [rect(28, 2, 1), fill(SCREEN_GLOW)], (cx, 43)),
            group("lapdid", [rect(34, 24, 3), fill(LAPTOP_DARK)], (cx, 46)),
        ]
        desk_back = [
            group("steam1", [rect(3, 7, 1), fill(STEAM, puff(0))], (9, 44)),
            group("steam2", [rect(3, 8, 1), fill(STEAM, puff(1))], (15, 41)),
            group("desk", [rect(80, 16, 0), fill(DESK)], (cx, 72)),
            group("deskedge", [rect(80, 3, 0), fill(DESK_EDGE)], (cx, 63)),
        ]

    # Bed scene: blanket in front of the body, pillow behind the head. Turns
    # "eyes shut" into "gone to bed", which is the point — this is the state
    # where the session is spent and you are waiting out the reset.
    bed_front, bed_back = [], []
    if m.get("bed"):
        bed_front = [
            group("blanket_trim", [rect(60, 5, 2), fill(BLANKET_DARK)], (cx, 54)),
            group("blanket", [rect(60, 22, 5), fill(BLANKET)], (cx, 64)),
        ]
        bed_back = [
            group("pillow", [rect(54, 16, 7), fill(PILLOW)], (cx, 40)),
            group("bedbase", [rect(70, 12, 4), fill(BLANKET_DARK)], (cx, 70)),
        ]

    # Lottie draws index 0 LAST, i.e. on top — the opposite of a painter's
    # algorithm. This list therefore runs front to back. A held guitar belongs
    # in front of the shell; bare claws tuck behind it. The blanket covers the
    # body but not the face; the pillow sits behind everything but the base.
    if m.get("knight") and m.get("dragon"):
        # Sword frontmost, then the dragon leaning in, then the helm over the
        # crab's own head.
        shapes = [*limbs, *dragon, *knight]
    elif m.get("knight"):
        shapes = [*knight, *limbs]
    elif m.get("guitar"):
        shapes = [*zzz, *dots, *mouths, *face, *limbs, shell, *legs]
    elif m.get("bed"):
        shapes = [*zzz, *mouths, *face, *bed_front, shell, *limbs, *legs, *bed_back]
    elif m.get("desk"):
        # Eyes above the lid, but the MOUTH behind it - that is what lets the
        # lowered head read as hunched down behind the screen. Putting mouths
        # in front (as every other mood does) drew it straight across the
        # laptop as a stray bar.
        shapes = [*face, *desk_front, *mouths, shell, *legs, *desk_back]
    else:
        shapes = [*zzz, *dots, *mouths, *face, shell, *limbs, *legs]

    # Face and shell bob together - animating only the shell would leave the
    # face hanging. Selected by identity rather than by slice index, since the
    # orderings above put them in different places.
    #
    # bob=0 skips the keyframes entirely rather than emitting a flat animation.
    # This is the single most expensive thing in an asset: every face group
    # carries its own 5-keyframe position track, so each extra brow or eyelid
    # costs ~670B, not the ~350B the shapes themselves take. Dropping the bob
    # took the desk scene from 15,317B (which exhausted the heap and wedged the
    # device) to well under the bed scene.
    if m["bob"]:
        for g in [*mouths, *face, shell]:
            g["it"][-1]["p"] = bob_for(g["it"][-1]["p"]["k"])

    layer = {
        "ddd": 0, "ind": 1, "ty": 4, "nm": f"crab_{mood}", "sr": m["speed"],
        "ks": {"o": static(100), "r": static(0), "p": static([0, 0, 0]),
               "a": static([0, 0, 0]), "s": static([100, 100, 100])},
        "ao": 0, "shapes": shapes, "ip": 0, "op": dur, "st": 0, "bm": 0,
    }
    return {"v": "5.7.4", "fr": FPS, "ip": 0, "op": dur, "w": W, "h": H,
            "nm": f"crab_{mood}", "ddd": 0, "assets": [], "layers": [layer]}


def emit_c(blobs: dict[str, bytes], out_c: Path, out_h: Path) -> None:
    names = list(blobs)

    h = ["/* Generated by tools/make_crab_lottie.py - do not edit. */\n",
         "#pragma once\n\n#include <stddef.h>\n#include <stdint.h>\n\n",
         "#ifdef __cplusplus\nextern \"C\" {\n#endif\n\n",
         "// Ordered worst-to-best mood is NOT meaningful here; the firmware\n",
         "// picks by name via crab_mood_asset().\n",
         "typedef enum {\n"]
    for n in names:
        h.append(f"    CRAB_{n.upper()},\n")
    h.append("    CRAB_MOOD_COUNT,\n} CrabMood;\n\n")
    h.append("typedef struct {\n    const uint8_t *data;\n    size_t size;\n} CrabAsset;\n\n")
    h.append("// Indexed by CrabMood.\nextern const CrabAsset crab_mood_assets[CRAB_MOOD_COUNT];\n\n")
    h.append("#ifdef __cplusplus\n}\n#endif\n")
    out_h.write_text("".join(h), encoding="utf-8")

    c = ["/* Generated by tools/make_crab_lottie.py - do not edit.\n",
         " * Regenerate with: python tools/make_crab_lottie.py\n */\n\n",
         f'#include "{out_h.name}"\n\n']
    for n, data in blobs.items():
        c.append(f"static const uint8_t crab_{n}[] = {{\n")
        for i in range(0, len(data), 16):
            c.append("    " + ", ".join(f"0x{b:02x}" for b in data[i:i + 16]) + ",\n")
        c.append("};\n\n")
    c.append("const CrabAsset crab_mood_assets[CRAB_MOOD_COUNT] = {\n")
    for n in names:
        c.append(f"    [CRAB_{n.upper()}] = {{ crab_{n}, sizeof(crab_{n}) }},\n")
    c.append("};\n")
    out_c.write_text("".join(c), encoding="utf-8")


def main(argv: list[str]) -> int:
    here = Path(__file__).resolve().parent.parent  # firmware/
    dump_json = None
    if "--json" in argv:
        dump_json = Path(argv[argv.index("--json") + 1])
        dump_json.mkdir(parents=True, exist_ok=True)

    blobs: dict[str, bytes] = {}
    for mood in MOODS:
        data = json.dumps(build(mood), separators=(",", ":")).encode("utf-8")
        json.loads(data)  # fail here rather than silently rendering nothing on device
        blobs[mood] = data
        if dump_json:
            (dump_json / f"crab_{mood}.json").write_bytes(data)
        print(f"  {mood:<8} {len(data):>6,} B")

    emit_c(blobs, here / "src" / "crab_assets.c", here / "src" / "crab_assets.h")
    total = sum(len(b) for b in blobs.values())
    print(f"\n{len(blobs)} moods, {total:,} B total in flash "
          f"(app partition has ~4.8MB free, so size is not the constraint)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
