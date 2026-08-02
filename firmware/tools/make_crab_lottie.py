#!/usr/bin/env python3
"""Generate the crab mascot's expressions as Lottie animations + a C asset file.

    python tools/make_crab_lottie.py            # writes src/crab_assets.{c,h}
    python tools/make_crab_lottie.py --json out # also dump the .json files

Written from scratch rather than sourced online: Anthropic's "Clawd" mascot is
their IP with no free licence, and the community re-creations carry
non-commercial terms. This is a generic crab, so there's nothing to attribute.

Deliberately blocky — every shape is a rounded rect. ThorVG rasterises vector
paths in software on this board and already sits near 90% CPU while animating,
so shape count and path complexity are what matter. Curves and gradients would
cost far more than they add at 80x80.

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
#   eye      : "open" | "narrow" | "half" | "closed"
#   pupil_dx : pupil offset, for looking off to one side
#   mouth    : "smile" | "flat" | "small" | "open"
#   wave     : claw swing in degrees (0 = arms down, asleep)
#   bob      : body bob in px
#   speed    : loop length multiplier (>1 = slower)
MOODS: dict[str, dict] = {
    "happy":   dict(eye="open",   pupil_dx=0,  mouth="smile", wave=16, bob=2, speed=1.0),
    "focused": dict(eye="narrow", pupil_dx=0,  mouth="flat",  wave=10, bob=1, speed=0.7),
    "chill":   dict(eye="open",   pupil_dx=2,  mouth="small", wave=12, bob=2, speed=1.4),
    "sleepy":  dict(eye="half",   pupil_dx=0,  mouth="small", wave=5,  bob=1, speed=2.2),
    "asleep":  dict(eye="closed", pupil_dx=0,  mouth="small", wave=0,  bob=2, speed=3.0),
    # Reserved for xhigh effort, echoing the guitar-strumming Clawd that Claude
    # Code itself shows at that level. One claw pins the neck, the other strums.
    "rocking": dict(eye="narrow", pupil_dx=0,  mouth="open",  wave=0,  bob=2, speed=0.8,
                    guitar=True),
}

GUITAR_BODY = [0.451, 0.271, 0.153]
GUITAR_NECK = [0.290, 0.180, 0.106]
# Lottie rotation is clockwise (screen y grows downward), so a positive tilt
# swings the neck up and to the right. 75 is deliberately shallow: at a
# steeper, more natural-looking angle the neck runs straight through the right
# eye, and the eyes are what carry the expression.
GUITAR_TILT = 75


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


def fill(color):
    return {"ty": "fl", "c": rgb(color), "o": static(100), "r": 1, "nm": "fl"}


def group(name, shapes, pos, rot=None, offset=(0.0, 0.0)):
    """A shape group. `pos` is the pivot; `rot` (if given) turns about it."""
    return {"ty": "gr", "nm": name, "it": shapes + [{
        "ty": "tr", "p": static(list(pos)), "a": static(list(offset)),
        "s": static([100, 100]), "r": rot if rot is not None else static(0),
        "o": static(100), "sk": static(0), "sa": static(0)}]}


def build(mood: str) -> dict:
    m = MOODS[mood]
    cx = W / 2
    dur = DUR
    q = [0, dur * 0.25, dur * 0.5, dur * 0.75, dur]

    def swing(sign):
        a = m["wave"] * sign
        return anim([(q[0], [-a]), (q[1], [a]), (q[2], [-a]), (q[3], [a]), (q[4], [-a])])

    def bob_for(base):
        x, y = base
        d = m["bob"]
        return anim([(q[0], [x, y]), (q[1], [x, y - d]), (q[2], [x, y]),
                     (q[3], [x, y - d]), (q[4], [x, y])])

    # Eyes. A closed eye is a dark bar; a half-lidded one is a short white
    # slit with the pupil pushed down, which reads as drowsy at this size.
    eye = m["eye"]
    eye_h = {"open": 10, "narrow": 7, "half": 5, "closed": 0}[eye]
    face = []
    if eye == "closed":
        for dx in (-9, 9):
            face.append(group(f"lid{dx}", [rect(11, 3, 1), fill(EYE_DARK)], (cx + dx, 42)))
    else:
        pdx = m["pupil_dx"]
        pupil_y = 42 + (1.5 if eye == "half" else 0)
        for dx in (-9, 9):
            face.append(group(f"pupil{dx}", [rect(4, min(4, eye_h - 1), 2), fill(EYE_DARK)],
                              (cx + dx + pdx, pupil_y)))
        for dx in (-9, 9):
            face.append(group(f"eye{dx}", [rect(10, eye_h, 3), fill(EYE_WHITE)], (cx + dx, 41)))

    mouth_shape = {
        "smile": (13, 4, 2, 51),
        "flat":  (11, 3, 1, 51),
        "small": (7, 3, 1, 51),
        "open":  (9, 7, 3, 51),
    }[m["mouth"]]
    mouth = group("mouth", [rect(*mouth_shape[:3]), fill(SHELL_DARK)], (cx, mouth_shape[3]))

    legs = [group(f"leg{i}", [rect(5, 7, 2), fill(SHELL_DARK)], (cx + dx, 61))
            for i, dx in enumerate((-14, -5, 5, 14))]

    shell = group("shell", [rect(44, 32, 11), fill(SHELL)], (cx, 44))

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
    else:
        # Asleep tucks the claws down against the body instead of holding them out.
        claw_y = 50 if m["wave"] == 0 else 44
        limbs = [
            group("claw_l", [rect(16, 14, 5, offset=(-8, 0)), fill(SHELL_DARK)],
                  (cx - 18, claw_y), None if m["wave"] == 0 else swing(1)),
            group("claw_r", [rect(16, 14, 5, offset=(8, 0)), fill(SHELL_DARK)],
                  (cx + 18, claw_y), None if m["wave"] == 0 else swing(-1)),
        ]

    # Lottie draws index 0 LAST, i.e. on top — the opposite of a painter's
    # algorithm. This list therefore runs front to back. A held guitar belongs
    # in front of the shell; bare claws tuck behind it.
    if m.get("guitar"):
        shapes = [mouth, *face, *limbs, shell, *legs]
    else:
        shapes = [mouth, *face, shell, *limbs, *legs]

    # Face and shell bob together — animating only the shell would leave the
    # face hanging. Selected by identity rather than by slice index, since the
    # two orderings above put them in different places.
    for g in [mouth, *face, shell]:
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
