"""Lower every census program (build/census/shaders) with build/sm86_lower_main.exe and group the failures.

    python tools/dev/census_lower.py <repo root> <sm86_lower_main.exe> <out dir>

A regression check for runtime/sm86_lower.c changes: the count of programs that lower, by failure message.
"""
import json, subprocess, sys, collections, os
root = sys.argv[1]; exe = sys.argv[2]; out = sys.argv[3]
sh = os.path.join(root, 'build/census/shaders')
progs = json.load(open(os.path.join(sh, 'programs.json')))
os.makedirs(out, exist_ok=True)
lines = []
for key, p in progs.items():
    st = p.get('stage', '?') if isinstance(p, dict) else '?'
    lines.append(f"{key} {st} {sh}/ctl/{key}.ctl {sh}/code/{key}.code {out}/{key}.code {out}/{key}.c1\n")
r = subprocess.run([exe, '--batch'], input=''.join(lines), capture_output=True, text=True)
fails = collections.Counter(); ex = {}
ok = 0
for l in r.stdout.splitlines():
    if l.startswith('ok'): ok += 1
    elif l.startswith('fail'):
        parts = l.split(' ', 3)
        msg = parts[3] if len(parts) > 3 else '?'
        fails[msg] += 1; ex.setdefault(msg, parts[1])
print('ok', ok, 'failed', sum(fails.values()))
for m, n in fails.most_common(30): print(n, m, ex[m][:16])
