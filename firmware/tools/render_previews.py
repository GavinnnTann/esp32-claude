#!/usr/bin/env python3
"""Render the crab moods to PNG/GIF for the README.

    pip install Pillow
    python tools/make_crab_lottie.py --json /tmp/moods
    python tools/render_previews.py /tmp/moods ../assets

IMPORTANT: this is NOT a Lottie renderer. It re-interprets the specific
rect/fill/transform shapes that make_crab_lottie.py emits, and nothing else.
Its purpose is documentation images and catching layout mistakes before
spending a flash cycle — it caught both the inverted z-order and the
backwards guitar rotation. ThorVG on the device is the real renderer.

Draws back-to-front (reversed shape list), matching Lottie's convention that
index 0 is topmost.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

MOODS = ["happy", "focused", "chill", "working", "sleepy", "asleep", "rocking"]
SCALE = 3
STAGE_BG = (46, 24, 81, 255)
STAGE_GRID = (152, 80, 229)


def val(prop, frame):
    if prop["a"] == 0:
        return prop["k"]
    kfs = prop["k"]
    for i in range(len(kfs) - 1):
        t0, t1 = kfs[i]["t"], kfs[i + 1]["t"]
        if t0 <= frame <= t1:
            s0, s1 = kfs[i]["s"], kfs[i + 1]["s"]
            u = 0 if t1 == t0 else (frame - t0) / (t1 - t0)
            u = u * u * (3 - 2 * u)  # approximates the ease in the keyframes
            return [a + (b - a) * u for a, b in zip(s0, s1)]
    return kfs[-1]["s"]


def render(doc, frame, stage=False):
    w, h = doc["w"], doc["h"]
    img = Image.new("RGBA", (w * SCALE, h * SCALE), (0, 0, 0, 255))

    if stage:
        # Mirrors the native LVGL stage drawn in ui.cpp, so the README shows
        # what the device shows rather than a bare animation on black.
        img.paste(Image.new("RGBA", img.size, STAGE_BG), (0, 0))
        dr = ImageDraw.Draw(img, "RGBA")
        pulse = 0.9 if (frame % 15) < 7 else 0.25
        a = int(255 * pulse)
        for i in range(4):
            p = int(w * SCALE * (i + 1) / 5)
            dr.rectangle([p, 0, p + 2 * SCALE, h * SCALE], fill=STAGE_GRID + (a,))
            dr.rectangle([0, p, w * SCALE, p + 2 * SCALE], fill=STAGE_GRID + (a,))

    for g in reversed(doc["layers"][0]["shapes"]):
        rc = next(i for i in g["it"] if i["ty"] == "rc")
        fl = next(i for i in g["it"] if i["ty"] == "fl")
        tr = next(i for i in g["it"] if i["ty"] == "tr")
        p, anchor = val(tr["p"], frame), val(tr["a"], frame)
        r = val(tr["r"], frame)
        rot = math.radians(r[0] if isinstance(r, list) else r)
        off, sz = val(rc["p"], frame), val(rc["s"], frame)
        rad = val(rc["r"], frame)
        rad = rad[0] if isinstance(rad, list) else rad
        op = val(fl["o"], frame)
        op = op[0] if isinstance(op, list) else op
        if sz[0] <= 0 or sz[1] <= 0:
            continue
        ox, oy = off[0] - anchor[0], off[1] - anchor[1]
        cx = p[0] + ox * math.cos(rot) - oy * math.sin(rot)
        cy = p[1] + ox * math.sin(rot) + oy * math.cos(rot)
        col = tuple(int(c * 255) for c in fl["c"]["k"][:3]) + (int(255 * op / 100),)
        lay = Image.new("RGBA", (int(sz[0] * SCALE) + 2, int(sz[1] * SCALE) + 2), (0, 0, 0, 0))
        ImageDraw.Draw(lay).rounded_rectangle(
            [1, 1, sz[0] * SCALE, sz[1] * SCALE], radius=max(1, rad * SCALE), fill=col)
        if abs(rot) > 1e-6:
            lay = lay.rotate(-math.degrees(rot), expand=True, resample=Image.BICUBIC)
        img.alpha_composite(lay, (int(cx * SCALE - lay.width / 2), int(cy * SCALE - lay.height / 2)))
    return img


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, out = Path(argv[1]), Path(argv[2])
    out.mkdir(parents=True, exist_ok=True)

    docs = {m: json.load(open(src / f"crab_{m}.json")) for m in MOODS}

    # Per-mood animated GIFs.
    for m, doc in docs.items():
        stage = m == "rocking"
        frames = [render(doc, f, stage).convert("P", palette=Image.ADAPTIVE)
                  for f in range(0, doc["op"], 2)]
        frames[0].save(out / f"crab-{m}.gif", save_all=True, append_images=frames[1:],
                       duration=int(2000 / len(frames)), loop=0, disposal=2)
        print(f"  crab-{m}.gif")

    # Contact sheet of all moods, for the README table's header image.
    w = docs["happy"]["w"] * SCALE
    sheet = Image.new("RGBA", (w * len(MOODS), w), (10, 10, 10, 255))
    for i, m in enumerate(MOODS):
        sheet.paste(render(docs[m], 4, m == "rocking"), (i * w, 0))
    sheet.save(out / "crab-moods.png")
    print("  crab-moods.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
