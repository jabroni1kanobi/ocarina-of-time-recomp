"""Frame-rate summary of a run log (OOT_TRACE=perf lines): per 60-frame window from a frame on, the interval average
and max, and over all those windows the frames counted, the mean interval, the worst max and the windows whose max
is 20 ms or more; then the slow-frame lines (perf: slow frame) in the range.

    python tools/dev/perf_summary.py build/logs/<run>.log [--from 3300] [--to 99999]
"""
import argparse
import re

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument('log')
ap.add_argument('--from', dest='lo', type=int, default=0)
ap.add_argument('--to', dest='hi', type=int, default=1 << 40)
args = ap.parse_args()
iv = re.compile(r'perf: frame (\d+): interval avg ([\d.]+) ms max ([\d.]+) ms .*?replay avg ([\d.]+) ms')
gp = re.compile(r'perf: frame (\d+): gpu avg ([\d.]+) ms.*?vram (\d+) of (\d+) MiB.*bound: (\w+)')
sl = re.compile(r'perf: slow frame (\d+): ([\d.]+) ms')
wins, gpus, slow = [], {}, []
for line in open(args.log, encoding='utf-8', errors='replace'):
    m = iv.search(line)
    if m and args.lo <= int(m[1]) <= args.hi:
        wins.append((int(m[1]), float(m[2]), float(m[3]), float(m[4])))
    m = gp.search(line)
    if m: gpus[int(m[1])] = (float(m[2]), int(m[3]), int(m[4]), m[5])
    m = sl.search(line)
    if m and args.lo <= int(m[1]) <= args.hi: slow.append((int(m[1]), float(m[2])))
if not wins:
    raise SystemExit('no perf windows in range')
n = 60 * len(wins)
mean = sum(w[1] for w in wins) / len(wins)
worst = max(wins, key=lambda w: w[2])
over = [w for w in wins if w[2] >= 20.0]
g = [gpus[w[0]] for w in wins if w[0] in gpus]
cpu = sum(1 for x in g if x[3] == 'cpu')
print(f'frames {wins[0][0] - 60}..{wins[-1][0]} ({n}): interval mean {mean:.2f} ms ({1000 / mean:.1f} fps), worst window '
      f'max {worst[2]:.1f} ms at {worst[0]}, windows with a frame >= 20 ms: {len(over)} of {len(wins)}')
if g:
    print(f'gpu mean {sum(x[0] for x in g) / len(g):.2f} ms, replay mean {sum(w[3] for w in wins) / len(wins):.2f} ms, '
          f'vram max {max(x[1] for x in g)} of {min(x[2] for x in g)} MiB, bound cpu in {cpu} of {len(g)} windows')
for w in over[:12]:
    print(f'  window {w[0]}: avg {w[1]:.2f} max {w[2]:.1f}')
for f, ms in slow[:12]:
    print(f'  slow frame {f}: {ms:.1f} ms')
