"""Check the tensor-core lowerings of runtime/sm86_lower.c (HMMA, LDSM, MOVM, and the HFMA2.RELU, HMNMX2, F2FP and
BAR forms the game's upscaler uses) by running their Maxwell output on a 32-lane model and comparing with references
computed from the instructions' definitions. Exit code 0 and a final PASS line when every check holds.

    .venv-win/Scripts/python tests/sm86_tensor_check.py [--exe build/tools/sm86_lower.exe] [--seed N]

The lowering CLI (tools/sm86_lower_main.c, built with -DSM86_TOOL) lowers a synthetic compute program; its output is
decoded with the opcode patterns of the vendored Ryujinx InstTable.cs, so an instruction the translator would read as
something else fails here too, and executed lane by lane for the field meanings of Ryujinx's InstDecoders.cs. HMMA is
checked twice: bit for bit against the same FP32 sequence in the expansion's order (layout, operands, rounding
points), and within one half ulp against exact sums rounded at the same points. The run is repeated with SM86_LEAN, the
form lower_hmma takes where 15 scratch registers are not free.

Standard library and numpy.
"""
import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / 'tools' / 'vendor' / 'ryujinx' / 'src' / 'Ryujinx.Graphics.Shader' / 'Decoders' / 'InstTable.cs'
RZ, PT = 255, 7
NEED = {'S2r', 'Lop32i', 'ShlI', 'ShrI', 'ShrR', 'Iadd32i', 'IaddR', 'Shfl', 'F2fR', 'FfmaR', 'PrmtI', 'Mov32i', 'MovR',
        'Lds', 'FmnmxR', 'Hfma2R', 'Bar', 'Exit'}


def patterns():
    """Ryujinx's (mask, value, emitter) rows for the emitters the expansions use."""
    rows = []
    for m in re.finditer(r'Add\("([01x]{64})",\s*InstName\.\w+,\s*InstEmit\.(\w+)', TABLE.read_text(encoding='utf-8')):
        bits, name = m.group(1), m.group(2)
        mask = value = 0
        for i, ch in enumerate(bits):
            if ch != 'x':
                mask |= 1 << (63 - i)
                value |= (ch == '1') << (63 - i)
        rows.append((mask, value, name))
    return rows


def decode(rows, w):
    hits = [(bin(mask).count('1'), name) for mask, value, name in rows if w & mask == value]
    if not hits:
        raise AssertionError(f'{w:016x} matches no Ryujinx instruction')
    return max(hits)[1]


def f(w, lo, n):
    return (w >> lo) & ((1 << n) - 1)


def h2f(h):
    return float(np.array([h & 0xffff], dtype=np.uint16).view(np.float16)[0])


def f2h(x):
    return int(np.array([x], dtype=np.float32).astype(np.float16).view(np.uint16)[0])   # round to nearest even


def f32(x):
    return float(np.float32(x))


def u2f(u):
    return struct.unpack('<f', struct.pack('<I', u & 0xffffffff))[0]


def f2u(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


def fma32(a, b, c):
    # a * b is exact in a double; one rounding to double, then to float (double rounding is possible but both the model
    # and the reference below use this same function, so the bit-exact comparison is like for like)
    return f32(a * b + c)


class Warp:
    def __init__(self, regs, shared):
        self.r = [list(lane) for lane in regs]     # 32 lanes x 256 registers (R255 reads as zero)
        self.shared = shared

    def get(self, lane, reg):
        return 0 if reg == RZ else self.r[lane][reg]

    def put(self, lane, reg, value):
        if reg != RZ:
            self.r[lane][reg] = value & 0xffffffff

    def run(self, rows, words):
        for k, w in enumerate(words):
            if k % 4 == 0:
                continue                           # scheduling word
            name = decode(rows, w)
            if name not in NEED:
                raise AssertionError(f'{w:016x}: {name} is not modelled')
            assert f(w, 16, 3) == PT and not f(w, 19, 1), f'{w:016x}: predicated'
            if name == 'Exit':
                return
            if name == 'Bar':
                assert f(w, 32, 3) == 0, 'BAR is not .SYNC'
                continue
            rd, ra, rb, rc = f(w, 0, 8), f(w, 8, 8), f(w, 20, 8), f(w, 39, 8)
            imm20 = f(w, 20, 19) | (f(w, 56, 1) << 19)
            if imm20 & 0x80000:
                imm20 -= 1 << 20
            old = [list(lane) for lane in self.r]  # every lane reads the values before the instruction
            g = lambda lane, reg: 0 if reg == RZ else old[lane][reg]
            for t in range(32):
                if name == 'S2r':
                    assert f(w, 20, 8) == 0, 'S2R of a register other than SR_LANEID'
                    v = t
                elif name == 'Lop32i':
                    assert not f(w, 55, 2) and not f(w, 52, 1), 'LOP32I modifiers'
                    a, i, op = g(t, ra), f(w, 20, 32), f(w, 53, 2)
                    v = a & i if op == 0 else a | i if op == 1 else a ^ i
                elif name == 'ShlI':
                    v = g(t, ra) << (imm20 & 0xff) if (imm20 & 0xff) < 32 else 0
                elif name in ('ShrI', 'ShrR'):
                    assert not f(w, 48, 1) and not f(w, 40, 1), 'SHR signed or reversed'
                    n = imm20 & 0xff if name == 'ShrI' else g(t, rb) & 0xff
                    v = g(t, ra) >> n if n < 32 else 0
                elif name == 'Iadd32i':
                    v = g(t, ra) + f(w, 20, 32)
                elif name == 'IaddR':
                    assert not f(w, 48, 2) and not f(w, 50, 1), 'IADD modifiers'
                    v = g(t, ra) + g(t, rb)
                elif name == 'Shfl':
                    assert f(w, 30, 2) == 0 and not f(w, 28, 1) and f(w, 29, 1), 'SHFL other than IDX, Rb, immediate c'
                    c = f(w, 34, 13)
                    seg, clamp = (c >> 8) & 0x1f, c & 0x1f
                    lo, hi = t & seg, (t & seg) | (clamp & ~seg)
                    src = lo | (g(t, rb) & ~seg & 0x1f)
                    v = g(src if src <= hi else t, ra)
                elif name == 'F2fR':
                    dst, src = f(w, 8, 2), f(w, 10, 2)
                    assert not f(w, 39, 2) and not f(w, 42, 1) and not f(w, 50, 1), 'F2F rounding or saturation'
                    b = g(t, rb)
                    if src == 1:
                        x = h2f(b >> 16 if f(w, 41, 1) else b)
                    else:
                        assert src == 2, 'F2F source format'
                        x = u2f(b)
                    if f(w, 49, 1):
                        x = abs(x)
                    if f(w, 45, 1):
                        x = -x
                    v = f2u(f32(x)) if dst == 2 else f2h(x)
                    assert dst in (1, 2), 'F2F destination format'
                elif name == 'FfmaR':
                    assert not f(w, 48, 2) and not f(w, 50, 1) and not f(w, 51, 4), 'FFMA modifiers'
                    v = f2u(fma32(u2f(g(t, ra)), u2f(g(t, rb)), u2f(g(t, rc))))
                elif name == 'PrmtI':
                    assert not f(w, 48, 4), 'PRMT mode'
                    src = struct.pack('<II', g(t, ra), g(t, rc))
                    v = sum(src[(imm20 >> (4 * i)) & 7] << (8 * i) for i in range(4))
                elif name == 'Mov32i':
                    v = f(w, 20, 32)
                elif name == 'MovR':
                    v = g(t, rb)
                elif name == 'Lds':
                    size, off = f(w, 48, 3), f(w, 20, 24)
                    off -= (off & 0x800000) << 1
                    at = g(t, ra) + off
                    assert size in (2, 4), 'LDS size'
                    v = int.from_bytes(self.shared[at:at + (2 if size == 2 else 4)], 'little')
                elif name == 'FmnmxR':
                    assert not f(w, 44, 6), 'FMNMX modifiers'
                    a, b = u2f(g(t, ra)), u2f(g(t, rb))
                    pick_min = (f(w, 39, 3) == PT) != bool(f(w, 42, 1))
                    v = f2u(min(a, b) if pick_min else max(a, b))
                elif name == 'Hfma2R':
                    assert not f(w, 32, 1) and not f(w, 37, 2) and not f(w, 49, 2), 'HFMA2 modifiers'

                    def halves(reg, swz):
                        x = g(t, reg)
                        return {0: (x, x >> 16), 2: (x, x), 3: (x >> 16, x >> 16)}[swz]
                    a, b, c = halves(ra, f(w, 47, 2)), halves(rb, f(w, 28, 2)), halves(rc, f(w, 35, 2))
                    out = []
                    for i in range(2):
                        p = Fraction(h2f(a[i])) * Fraction(h2f(b[i])) * (-1 if f(w, 31, 1) else 1)
                        s = p + Fraction(h2f(c[i])) * (-1 if f(w, 30, 1) else 1)
                        out.append(f2h(float(s)))
                    v = out[0] | out[1] << 16
                self.put(t, rd, v)
        raise AssertionError('no EXIT')


def control_for(n):
    """A compute control section for n SM86 instructions and no jump tables (sm86_control_parse's fields)."""
    c = bytearray(0xe0)
    struct.pack_into('<I', c, 0, 0x98761234)
    struct.pack_into('<IIII', c, 0x38, 16 * n, 0, 16 * n, 16 * n)
    c[0x56] = 5
    struct.pack_into('<III', c, 0x58, 32, 1, 1)
    struct.pack_into('<I', c, 0xd4, 0xe0)
    return bytes(c)


def insn(w0, w1=0, w2=0, w3=0):
    return struct.pack('<4I', w0, w1, w2, w3)


EXIT = insn(0x0000794d, 0, 0x03800000, 0x000fea00)


def program():
    """The test program, its registers' roles and shared memory, built from one random generator."""
    code = b''.join([
        insn(0x23c | 0x7000 | 0 << 16 | 8 << 24, 12, 16 | 0x800),                # HMMA.16816.F16 R0, R8, R12, R16
        insn(0x23c | 0x7000 | 40 << 16 | 8 << 24, 12, RZ | 0x800),               # HMMA ... R40, R8, R12, RZ
        insn(0x00007b1d),                                                        # BAR.SYNC 0
        insn(0x83b | 0x7000 | 20 << 16 | 30 << 24, 0x10 << 8, 2 << 8),           # LDSM.16.M88.4 R20, [R30 + 0x10]
        insn(0x83b | 0x7000 | 24 << 16 | 30 << 24, 0, 1 << 8 | 1 << 14),         # LDSM.16.MT88.2 R24, [R30]
        insn(0x83b | 0x7000 | 30 << 16 | 30 << 24, 0x20 << 8, 0),                # LDSM.16.M88 R30, [R30 + 0x20]
        insn(0x23a | 0x7000 | 28 << 16 | 4 << 24),                               # MOVM.16.MT88 R28, R4
        insn(0x23e | 0x7000 | 29 << 16 | 2 << 24, 3, RZ),                        # F2FP.PACK_AB R29, R2, R3
        insn(0x831 | 0x7000 | 31 << 16 | 5 << 24, 0x3c003c00, 6 | 1 << 15 | 7 << 23),   # HFMA2.RELU R31, R5, 1 1, R6
        insn(0x240 | 0x7000 | 32 << 16 | 7 << 24, RZ, 7 << 23 | 1 << 26),        # HMNMX2 R32, R7, RZ, !PT (max)
        EXIT])
    return code


def check(exe, rows, seed, lean):
    rng = np.random.default_rng(seed)
    regs = [[int(x) for x in rng.integers(0, 1 << 32, 256, dtype=np.uint64)] for _ in range(32)]
    half = lambda: f2h(float(rng.uniform(-2, 2)))
    for t in range(32):
        for r in (8, 9, 10, 11, 12, 13, 16, 17, 4, 5, 6, 7):
            regs[t][r] = half() | half() << 16
        regs[t][2], regs[t][3] = f2u(f32(rng.uniform(-100, 100))), f2u(f32(rng.uniform(-100, 100)))
    shared = bytes(int(x) for x in rng.integers(0, 256, 4096))
    rows_at = rng.permutation(64)[:32] * 32                 # lane 8m + r: the address of row r of matrix m
    for t in range(32):
        regs[t][30] = int(rows_at[t])
    before = [list(lane) for lane in regs]
    code = program()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'p.ctl').write_bytes(control_for(len(code) // 16))
        (tmp / 'p.code').write_bytes(code)
        env = dict(os.environ)
        if lean:
            env['SM86_LEAN'] = '1'
        r = subprocess.run([exe, str(tmp / 'p.ctl'), str(tmp / 'p.code'), str(tmp / 'o.code'), str(tmp / 'o.c1')],
                           capture_output=True, text=True, env=env)
        if r.returncode:
            raise AssertionError(f'lowering failed: {(r.stdout + r.stderr).strip()}')
        out = (tmp / 'o.code').read_bytes()
    words = struct.unpack(f'<{len(out) // 8}Q', out)
    warp = Warp(regs, shared)
    warp.run(rows, words)
    res = warp.r
    lo, hi = lambda v: v & 0xffff, lambda v: v >> 16

    # HMMA: fragments of lane t = 4g + i
    def frag_a(m, k):
        g, kk = m % 8, k % 8
        reg = 8 + (m >= 8) + 2 * (k >= 8)
        word = before[4 * g + kk // 2][reg]
        return h2f(hi(word) if kk % 2 else lo(word))

    def frag_b(k, n):
        kk = k % 8
        word = before[4 * n + kk // 2][12 + (k >= 8)]
        return h2f(hi(word) if kk % 2 else lo(word))

    def frag_c(m, n, creg):
        if creg == RZ:
            return 0.0
        word = before[4 * (m % 8) + n // 2][creg + (m >= 8)]
        return h2f(hi(word) if n % 2 else lo(word))

    def out_d(m, n, dreg):
        word = res[4 * (m % 8) + n // 2][dreg + (m >= 8)]
        return hi(word) if n % 2 else lo(word)

    worst, off_by_one, outputs = 0.0, 0, 0
    for dreg, creg in ((0, 16), (40, RZ)):
        for m in range(16):
            for n in range(8):
                acc, exact = f32(frag_c(m, n, creg)), Fraction(frag_c(m, n, creg))
                for k in range(16):
                    acc = fma32(frag_a(m, k), frag_b(k, n), acc)
                    exact += Fraction(frag_a(m, k)) * Fraction(frag_b(k, n))
                    if k % 8 == 7:
                        acc, exact = h2f(f2h(acc)), Fraction(h2f(f2h(float(exact))))
                got = out_d(m, n, dreg)
                if got != f2h(acc):
                    raise AssertionError(f'HMMA D{dreg}[{m}][{n}] = {got:04x}, the FP32 sequence gives {f2h(acc):04x}')
                ulp = abs(h2f(got) - float(exact)) / max(abs(np.spacing(np.float16(float(exact)))), 2.0 ** -24)
                worst = max(worst, ulp)
                outputs += 1
                off_by_one += ulp > 0
    # Both are halves, so they agree or differ by whole ulps: FP32 accumulation before each rounding may land on the
    # other side of a rounding boundary than the exact sum does (tensor cores do not sum exactly either).
    if worst > 1 + 1e-9:
        raise AssertionError(f'HMMA is {worst:.2f} half ulps from the exactly rounded sums')
    worst = (worst, off_by_one, outputs)

    # LDSM: lane 8m + r holds the address of row r of matrix m
    def row_addr(m, r):
        return before[8 * m + r][30]

    for t in range(32):
        for m in range(4):
            at = row_addr(m, t // 4) + 0x10 + 4 * (t % 4)
            want = int.from_bytes(shared[at:at + 4], 'little')
            assert res[t][20 + m] == want, f'LDSM.4 lane {t} matrix {m}: {res[t][20 + m]:08x} != {want:08x}'
        for m in range(2):
            parts = []
            for h in range(2):
                at = row_addr(m, 2 * (t % 4) + h) + 2 * (t // 4)
                parts.append(int.from_bytes(shared[at:at + 2], 'little'))
            want = parts[0] | parts[1] << 16
            assert res[t][24 + m] == want, f'LDSM.MT88.2 lane {t} matrix {m}: {res[t][24 + m]:08x} != {want:08x}'
        at = row_addr(0, t // 4) + 0x20 + 4 * (t % 4)
        want = int.from_bytes(shared[at:at + 4], 'little')
        assert res[t][30] == want, f'LDSM.1 into its own address register, lane {t}'

    # MOVM: element (r, c) of the result is (c, r) of the source; lane t holds row t / 4, columns 2 (t % 4) + h
    def src_elem(r, c):
        word = before[4 * r + c // 2][4]
        return hi(word) if c % 2 else lo(word)

    for t in range(32):
        r, c = t // 4, 2 * (t % 4)
        want = src_elem(c, r) | src_elem(c + 1, r) << 16
        assert res[t][28] == want, f'MOVM lane {t}: {res[t][28]:08x} != {want:08x}'
        # F2FP.PACK_AB: A high, B low
        want = f2h(u2f(before[t][2])) << 16 | f2h(u2f(before[t][3]))
        assert res[t][29] == want, f'F2FP lane {t}: {res[t][29]:08x} != {want:08x}'
        # HFMA2.RELU with B = 1.0: max(a + c, 0) per half
        v = []
        for i in range(2):
            a, cc = h2f(before[t][5] >> (16 * i)), h2f(before[t][6] >> (16 * i))
            s = f2h(float(Fraction(a) + Fraction(cc)))
            v.append(0 if h2f(s) < 0 or s == 0x8000 else s)
        got = [res[t][31] & 0xffff, res[t][31] >> 16]
        assert [x if x != 0x8000 else 0 for x in got] == v, f'HFMA2.RELU lane {t}: {got} != {v}'
        # HMNMX2 with !PT: the maximum with zero
        v = [f2h(max(h2f(before[t][7] >> (16 * i)), 0.0)) for i in range(2)]
        got = [res[t][32] & 0xffff, res[t][32] >> 16]
        assert [x if x != 0x8000 else 0 for x in got] == [x if x != 0x8000 else 0 for x in v], f'HMNMX2 lane {t}'
    return len(words), worst


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--exe', default=str(ROOT / 'build' / 'tools' / 'sm86_lower.exe'))
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--runs', type=int, default=4)
    args = ap.parse_args()
    rows = patterns()
    for lean in (False, True):
        for s in range(args.seed, args.seed + args.runs):
            n, worst = check(args.exe, rows, s, lean)
            ulps, differ, outputs = worst
            print(f'{"lean" if lean else "full"} form, seed {s}: {n} Maxwell words, all checks pass '
                  f'(HMMA bit-exact to the FP32 sequence; {differ} of {outputs} outputs one half ulp from exact sums)')
    print('sm86_tensor_check: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
