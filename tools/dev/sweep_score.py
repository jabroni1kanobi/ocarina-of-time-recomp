"""Score a camera sweep (build/logs/continue_sweep.txt + OOT_SHOTS) for blown-out frames.

    python tools/dev/sweep_score.py <shots dir> [--sheet out.png] [--limit 0.10]

Per shot: the share of pixels whose every channel is >= 245 (blown out) and the mean brightness. Exit 1 when any shot's
blown-out share exceeds --limit (default 10 %): the pass condition for lighting fixes is every angle of the sweep.
"""
import argparse
import sys
from pathlib import Path

from PIL import Image


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--sheet")
    ap.add_argument("--limit", type=float, default=0.10)
    a = ap.parse_args()
    shots = sorted(Path(a.dir).glob("f*.png"))
    worst, rows, thumbs = 0.0, [], []
    for p in shots:
        im = Image.open(p).convert("RGB")
        px = list(im.getdata())
        n = len(px)
        white = sum(1 for r, g, b in px if r >= 245 and g >= 245 and b >= 245) / n
        mean = sum(r + g + b for r, g, b in px) / (3 * n)
        rows.append((p.name, white, mean))
        worst = max(worst, white)
        if a.sheet:
            thumbs.append(im.resize((240, 135)))
    bad = [r for r in rows if r[1] > a.limit]
    if not rows:   # no shots: the run did not reach the sweep (a crash), which is a failure
        print("no shots: FAIL")
        return 1
    for name, white, mean in rows:
        print(f"{name}  blown {white * 100:5.1f} %  mean {mean:5.1f}{'  <-- FAIL' if white > a.limit else ''}")
    print(f"{len(rows)} shots, worst blown {worst * 100:.1f} %, {len(bad)} over {a.limit * 100:.0f} %: {'FAIL' if bad else 'PASS'}")
    if a.sheet and thumbs:
        cols = 8
        sheet = Image.new("RGB", (cols * 240, ((len(thumbs) + cols - 1) // cols) * 135))
        for i, t in enumerate(thumbs):
            sheet.paste(t, ((i % cols) * 240, (i // cols) * 135))
        sheet.save(a.sheet)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
