# Names the imports behind lifted PLT stubs: python -I tools/dev/stubnames.py . build/extracted/v131072/exefs/main generated/v131072/main 7003c44d90,7003c449c0
# (repo root for recomp/nso.py, the main module, the lifted C folder, stub addresses without 0x; scans main_0*.c).
import sys,re,glob
sys.path.insert(0, sys.argv[1])
from recomp.nso import Nso
n = Nso(sys.argv[2])
BASE = 0x7000000000
slot = {}
for off, typ, sym, add in n.relocs:
    if sym: slot[off] = n.syms[sym][0]
stub = {}
pat = re.compile(r'void f_([0-9a-f]+)\(Cpu \*c\) \{')
for fn in sorted(glob.glob(sys.argv[3] + '/main_0*.c')):
    lines = open(fn, encoding='utf-8', errors='replace').read().split('\n')
    for i, l in enumerate(lines):
        m = pat.match(l)
        if not m or not m.group(1).startswith('7003c4'): continue
        body = '\n'.join(lines[i:i+7])
        a = re.search(r'x16 = \(uint64_t\)\(0x([0-9a-f]+)ull\);\s*\{ uint64_t ea = x16 \+ 0x([0-9a-f]+)ull', body)
        if a: stub[m.group(1)] = slot.get(int(a.group(1), 16) + int(a.group(2), 16) - BASE, '?')
for k in sys.argv[4].split(','):
    print(k, stub.get(k, 'not a stub'))
