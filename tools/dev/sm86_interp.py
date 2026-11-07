"""A CPU interpreter for the SM86 (Ampere) mesh programs of the white-out investigation (docs/handoff-whiteout.md): one
warp of 32 lanes runs the program on the buffers OOT_MESH_CAPTURE wrote (runtime/vk_exec_draw.c mesh_capture), and the
ISBE attribute lines it writes are printed as clip-space vertices. The oracle is independent of runtime/sm86_lower.c
and of the translator: field positions come from Mesa NAK's sm70_encode.rs (MIT) and the semantics from the ISA.
Only the opcodes the wall programs use are implemented; anything else stops with its offset.

    python tools/dev/sm86_interp.py build/v131072/meshcap 0 [--meshlet N] [--wg W] [--trace]

Layout as the runtime's (docs/mesh-spec.md): ISBE input word 0 = workgroup index, payload from byte 0x10 (the meshlet
id list the task wrote: --meshlet puts N at word 4 + W); attribute writes land in attr[], map writes in map[].
"""
import argparse
import math
import struct
import sys
from pathlib import Path

RZ, PT = 255, 7
import os
AMD_SHF = os.environ.get('SM86_AMD_SHF') == '1'


def f32(x):
    """Round a Python float to binary32."""
    try:
        return struct.unpack('<f', struct.pack('<f', x))[0]
    except OverflowError:
        return math.inf if x > 0 else -math.inf


def as_f(u):
    return struct.unpack('<f', struct.pack('<I', u & 0xffffffff))[0]


def as_u(f):
    return struct.unpack('<I', struct.pack('<f', f))[0]


def s32(u):
    u &= 0xffffffff
    return u - (1 << 32) if u & 0x80000000 else u


class Insn:
    def __init__(self, words, offset):
        self.w = words
        self.off = offset
        self.v = words[0] | words[1] << 32 | words[2] << 64 | words[3] << 96

    def bits(self, o, n):
        return (self.v >> o) & ((1 << n) - 1)

    @property
    def op(self):
        return self.w[0] & 0xfff

    @property
    def base_op(self):
        return self.w[0] & 0x1ff

    @property
    def form(self):
        return (self.w[0] >> 9) & 7


class Lane:
    def __init__(self, lane):
        self.r = [0] * 256
        self.p = [False] * 7
        self.lane = lane


class Machine:
    def __init__(self, code, cbufs, mem, trace=False):
        self.code = code
        self.cbufs = cbufs       # bank -> bytes
        self.mem = mem           # list of (base, bytes) global ranges
        self.ur = [0] * 64
        self.up = [False] * 7
        self.lanes = [Lane(i) for i in range(32)]
        self.isbe_in = bytearray(0x1000)
        self.attr = bytearray(0x4000)
        self.map = bytearray(0x1000)
        self.trace = trace
        self.trace_lane = 0
        self.unknown = {}

    # --- memory
    def cbuf(self, bank, off, n=4):
        b = self.cbufs.get(bank)
        if b is None or off + n > len(b):
            return 0
        return int.from_bytes(b[off:off + n], 'little')

    def gload(self, addr, n):
        for base, data in self.mem:
            if base <= addr and addr + n <= base + len(data):
                o = addr - base
                return int.from_bytes(data[o:o + n], 'little')
        self.unknown.setdefault('load outside the bound ranges', set()).add(hex(addr))
        return 0

    # --- operands
    def reg(self, L, r):
        return 0 if r == RZ else L.r[r] & 0xffffffff

    def ureg(self, u):
        return 0 if u == 63 else self.ur[u] & 0xffffffff

    def src1(self, L, x):
        """The second ALU source by form: 1 register, 4 immediate, 5 constant, 6 uniform (x forms swap with src2)."""
        f = x.form
        if f in (1,):
            return self.reg(L, x.bits(32, 8))
        if f in (4,):
            return x.bits(32, 32)
        if f in (5,):
            return self.cbuf(x.bits(54, 5), x.bits(38, 16))
        if f in (6,):
            return self.ureg(x.bits(32, 6))
        if f in (2, 3, 7):   # the wide operand is src2; src1 is the register at 64-71
            return self.reg(L, x.bits(64, 8))
        raise NotImplementedError(f'form {f}')

    def src2(self, L, x):
        f = x.form
        if f in (1, 4, 5, 6):
            return self.reg(L, x.bits(64, 8))
        if f == 2:
            return x.bits(32, 32)
        if f == 3:
            return self.cbuf(x.bits(54, 5), x.bits(38, 16))
        if f == 7:
            return self.ureg(x.bits(32, 6))
        raise NotImplementedError(f'form {f}')

    def srcs(self, L, x, fp=False):
        """src0, src1, src2 with the ALU modifier bits (neg 72/63/75, abs 73/62/74) applied."""
        a = self.reg(L, x.bits(24, 8))
        b = self.src1(L, x)
        c = self.src2(L, x)
        if fp:
            a, b, c = as_f(a), as_f(b), as_f(c)
            if x.bits(73, 1): a = abs(a)
            if x.bits(72, 1): a = -a
            # the modifier bits of the wide operand travel with it: in the x forms, bits 62/63 belong to src2; an
            # immediate (forms 4, 2) fills bits 32-63 and has no modifier bits
            imm = x.form in (2, 4)
            nb, ab, nc, ac = 0 if imm else x.bits(63, 1), 0 if imm else x.bits(62, 1), x.bits(75, 1), x.bits(74, 1)
            if x.form in (2, 3, 7):
                nb, ab, nc, ac = nc, ac, nb, ab
            if ab: b = abs(b)
            if nb: b = -b
            if ac: c = abs(c)
            if nc: c = -c
            return a, b, c
        return a, b, c

    def two(self, L, x, fp=False):
        """The two operands of a two-source ALU op: src0 and the wide operand (the second slot in forms 1/4/5/6, the
        third in 2/3/7, whose register slot the compiler leaves as 0). Modifiers: a neg 72 / abs 73; wide neg 63 / abs 62."""
        a = self.reg(L, x.bits(24, 8))
        b = self.src2(L, x) if x.form in (2, 3, 7) else self.src1(L, x)
        if fp:
            a, b = as_f(a), as_f(b)
            if x.bits(73, 1): a = abs(a)
            if x.bits(72, 1): a = -a
            if x.form not in (2, 4):   # an immediate has no modifier bits
                if x.bits(62, 1): b = abs(b)
                if x.bits(63, 1): b = -b
        return a, b

    def pred(self, L, idx, neg):
        v = True if idx == PT else L.p[idx]
        return (not v) if neg else v

    # --- run
    def run(self, max_steps=100000):
        pc = 0
        n = len(self.code)
        steps = 0
        while pc < n and steps < max_steps:
            x = self.code[pc]
            steps += 1
            g = x.bits(12, 4)
            op = x.base_op
            if op == 0x14d:   # EXIT
                return 'exit'
            if op == 0x147:   # BRA (only the trailing self-branch is expected)
                return 'bra'
            if op == 0x118:   # NOP
                pc += 1
                continue
            if op in (0x0b9, 0x090, 0x092, 0x099, 0x087, 0x08c, 0x082, 0x0a4, 0x091, 0x085, 0x0bf, 0x0bd, 0x09c):   # uniform datapath
                if g != PT and not (self.up[g & 7] ^ bool(g & 8)):
                    pc += 1
                    continue
                self.uniform(x)
                pc += 1
                continue
            if op == 0x1c2:   # R2UR: the register of the first active lane into a uniform register
                for L in self.lanes:
                    if g == PT or self.pred(L, g & 7, g & 8):
                        self.ur[x.bits(16, 6)] = self.reg(L, x.bits(24, 8))
                        break
                pc += 1
                continue
            for L in self.lanes:
                if g != PT and not self.pred(L, g & 7, g & 8):
                    continue
                self.exec(L, x)
            pc += 1
        return 'steps' if steps >= max_steps else 'end'

    def uniform(self, x):
        op = x.base_op
        d = x.bits(16, 6)
        if op == 0x0b9:   # ULDC: udst 16-21, cbuf, size 73-75
            size = x.bits(73, 3)
            bank, off = x.bits(54, 5), x.bits(38, 16)
            if size == 5:
                v = self.cbuf(bank, off, 8)
                self.ur[d], self.ur[d + 1] = v & 0xffffffff, v >> 32
            elif size == 4:
                self.ur[d] = self.cbuf(bank, off)
            else:
                raise NotImplementedError(f'ULDC size {size} at {x.off:#x}')
            return
        # uniform ALU operands: the same slots as the ALU's with 6-bit uniform registers (form 1 register, 4 immediate,
        # 2 immediate as the third source with the second at 64-69)
        a = self.ureg(x.bits(24, 6))
        if x.form == 4:
            b, c = x.bits(32, 32), self.ureg(x.bits(64, 6))
        elif x.form == 2:
            b, c = self.ureg(x.bits(64, 6)), x.bits(32, 32)
        else:
            b, c = self.ureg(x.bits(32, 6)), self.ureg(x.bits(64, 6))
        na, nb, nc = x.bits(72, 1), (0 if x.form == 4 else x.bits(63, 1)), x.bits(75, 1)
        if x.form == 2: nb, nc = nc, 0

        def upred(idx, neg):
            v = True if idx == PT else self.up[idx]
            return (not v) if neg else v

        def setu(v):
            if d != 63: self.ur[d] = v & 0xffffffff

        if op == 0x090:   # UIADD3
            if na: a = (-a) & 0xffffffff
            if nb: b = (-b) & 0xffffffff
            if nc: c = (-c) & 0xffffffff
            if x.bits(74, 1):
                raise NotImplementedError(f'UIADD3.X at {x.off:#x}')
            setu(a + b + c)
        elif op == 0x082:   # UMOV
            setu(b)
        elif op == 0x092:   # ULOP3
            lut = x.bits(72, 8)
            r = 0
            for i in range(32):
                idx = ((a >> i & 1) << 2) | ((b >> i & 1) << 1) | (c >> i & 1)
                r |= (lut >> idx & 1) << i
            setu(r)
            pd = x.bits(81, 3)
            if pd != PT: self.up[pd] = r != 0
        elif op == 0x099:   # USHF
            low, shift, high = a, b, c
            t = x.bits(73, 2)
            wrap, right, dst_high = x.bits(75, 1), x.bits(76, 1), x.bits(80, 1)
            is64, signed = t in (0, 1), t in (0, 2)
            width = 64 if is64 else 32
            shift = shift & (width - 1) if wrap else min(shift, width)
            val = low | (high << 32)
            if right:
                if signed and (val >> 63) & 1: val -= 1 << 64
                r = val >> shift
            else:
                r = val << shift
            r &= (1 << 64) - 1
            setu(r >> 32 if dst_high else r)
        elif op == 0x087:   # USEL
            setu(a if upred(x.bits(87, 3), x.bits(90, 1)) else b)
        elif op == 0x08c:   # UISETP
            if x.bits(73, 1): a, b = s32(a), s32(b)
            cmp = x.bits(76, 3)
            r = {0: False, 1: a < b, 2: a == b, 3: a <= b, 4: a > b, 5: a != b, 6: a >= b, 7: True}[cmp]
            acc = upred(x.bits(87, 3), x.bits(90, 1))
            sop = x.bits(74, 2)
            r = (r and acc) if sop == 0 else (r or acc) if sop == 1 else (r != acc)
            pd, pd1 = x.bits(81, 3), x.bits(84, 3)
            if pd != PT: self.up[pd] = r
            if pd1 != PT: self.up[pd1] = (not r) and acc if sop == 0 else (not r) or acc if sop == 1 else (not r) != acc
        elif op == 0x0a4:   # UIMAD
            if nc: c = (-c) & 0xffffffff
            setu(s32(a) * s32(b) + s32(c) if x.bits(73, 1) else a * b + c)
        elif op == 0x091:   # ULEA
            shift = x.bits(75, 5)
            if na: a = (-a) & 0xffffffff
            v = ((((a | c << 32) << shift) >> 32) + b) if x.bits(80, 1) else (((a << shift) & 0xffffffff) + b)
            setu(v)
            pd = x.bits(81, 3)
            if pd != PT: self.up[pd] = v >> 32 != 0
        elif op == 0x0bf:   # UPOPC
            if x.form != 4 and x.bits(63, 1): b = (~b) & 0xffffffff
            setu(bin(b).count('1'))
        elif op == 0x0bd:   # UFLO
            if x.form != 4 and x.bits(63, 1): b = (~b) & 0xffffffff
            if x.bits(73, 1) and b & 0x80000000: b = (~b) & 0xffffffff
            r = 0xffffffff if b == 0 else b.bit_length() - 1
            if x.bits(74, 1) and r != 0xffffffff: r = 31 - r
            setu(r)
        elif op == 0x085:   # UIMNMX
            if x.bits(73, 1): a, b = s32(a), s32(b)
            setu(min(a, b) if upred(x.bits(87, 3), x.bits(90, 1)) else max(a, b))
        else:
            raise NotImplementedError(f'uniform op {op:#x} at {x.off:#x}')

    def exec(self, L, x):
        op = x.base_op
        d = x.bits(16, 8)

        def setd(v):
            if d != RZ:
                L.r[d] = v & 0xffffffff

        def setf(fv):
            setd(as_u(f32(fv)))

        if op == 0x002:   # MOV
            setd(self.src1(L, x))
        elif op == 0x119:   # S2R
            sr = x.bits(72, 8)
            if sr == 0:
                setd(L.lane)            # SR_LANEID
            elif sr == 0x21:
                setd(L.lane)            # SR_TID.X: one warp
            else:
                raise NotImplementedError(f'S2R {sr:#x}')
        elif op == 0x023:   # FFMA
            a, b, c = self.srcs(L, x, fp=True)
            setf(a * b + c)
        elif op == 0x020:   # FMUL
            a, b = self.two(L, x, fp=True)
            setf(a * b)
        elif op == 0x021:   # FADD
            a, b = self.two(L, x, fp=True)
            setf(a + b)
        elif op == 0x010:   # IADD3 (.X at 74: carries in from the predicates at 87-89 / 77-79, outs at 81-83 / 84-86)
            a, b, c = self.srcs(L, x)
            if x.bits(72, 1): a = (-a) & 0xffffffff
            nb, nc = (0 if x.form == 4 else x.bits(63, 1)), x.bits(75, 1)
            if x.form in (2, 3, 7): nb, nc = nc, (0 if x.form == 2 else nb)
            if nb: b = (-b) & 0xffffffff
            if nc: c = (-c) & 0xffffffff
            v = a + b + c
            if x.bits(74, 1):
                v += int(self.pred(L, x.bits(87, 3), x.bits(90, 1))) + int(self.pred(L, x.bits(77, 3), x.bits(80, 1)))
            setd(v)
            pd, pd1 = x.bits(81, 3), x.bits(84, 3)
            if pd != PT: L.p[pd] = (v >> 32) & 1 == 1
            if pd1 != PT: L.p[pd1] = v >> 33 != 0
        elif op == 0x024:   # IMAD
            a, b, c = self.srcs(L, x)
            signed = x.bits(73, 1)
            nc = x.bits(75, 1) if x.form not in (2, 3, 7) else (0 if x.form == 2 else x.bits(63, 1))
            if nc: c = (-c) & 0xffffffff
            if signed:
                setd(s32(a) * s32(b) + s32(c))
            else:
                setd(a * b + c)
        elif op == 0x025:   # IMAD.WIDE
            a, b, c = self.srcs(L, x)
            cw = c | (self.reg(L, x.bits(64, 8) + 1) << 32) if x.form in (1, 4, 5, 6) else c
            if x.bits(73, 1):
                v = s32(a) * s32(b) + (cw - (1 << 64) if cw & (1 << 63) else cw)
            else:
                v = a * b + cw
            v &= (1 << 64) - 1
            setd(v & 0xffffffff)
            if d != RZ: L.r[d + 1] = v >> 32
        elif op == 0x012:   # LOP3
            a, b, c = self.srcs(L, x)
            lut = x.bits(72, 8)
            r = 0
            for i in range(32):
                idx = ((a >> i & 1) << 2) | ((b >> i & 1) << 1) | (c >> i & 1)
                r |= (lut >> idx & 1) << i
            setd(r)
            pd = x.bits(81, 3)
            if pd != PT:
                L.p[pd] = r != 0
        elif op == 0x019:   # SHF
            low, shift, high = self.srcs(L, x)
            t = x.bits(73, 2)
            wrap, right, dst_high = x.bits(75, 1), x.bits(76, 1), x.bits(80, 1)
            is64 = t in (0, 1)
            signed = t in (0, 2)
            width = 64 if is64 else 32
            if wrap:
                shift &= width - 1
            else:
                shift = min(shift, width)
            val = low | (high << 32)
            if right:
                if signed and (val >> 63) & 1:
                    val -= 1 << 64
                r = val >> shift
            else:
                r = val << shift
            r &= (1 << 64) - 1
            if AMD_SHF:   # the translator's formula as the owner's AMD GPU runs it (shift amounts taken mod 32)
                sh = shift & 31
                if right:
                    a, cc = (high, 0) if dst_high and not is64 else (low, high)
                    if dst_high and not is64: cc = 0
                    emu = ((a >> sh) | ((cc << ((32 - sh) & 31)) & 0xffffffff)) & 0xffffffff if not dst_high else None
                    if emu is None:   # dst_high of a 64-bit right shift: Ryujinx reads high as A and nothing as C? keep exact
                        emu = r >> 32
                    elif is64 and shift >= 32:
                        emu = (((high - (1 << 32)) if signed and high & 0x80000000 else high) >> (shift - 32)) & 0xffffffff
                else:
                    emu = ((high << sh) | (low >> ((32 - sh) & 31))) & 0xffffffff if dst_high else ((low << sh) & 0xffffffff)
                setd(emu)
                return
            setd(r >> 32 if dst_high else r)
        elif op == 0x007:   # SEL
            a, b = self.two(L, x)
            setd(a if self.pred(L, x.bits(87, 3), x.bits(90, 1)) else b)
        elif op == 0x008:   # FSEL (float modifiers on the sources)
            a, b = self.two(L, x, fp=True)
            setf(a if self.pred(L, x.bits(87, 3), x.bits(90, 1)) else b)
        elif op == 0x108:   # MUFU: op 74-79 on the second source
            _, b = self.two(L, x, fp=True)
            mop = x.bits(74, 6)
            try:
                if mop == 0: r = math.cos(b)
                elif mop == 1: r = math.sin(b)
                elif mop == 2: r = 2.0 ** b
                elif mop == 3: r = math.log2(b) if b > 0 else (-math.inf if b == 0 else math.nan)
                elif mop == 4: r = 1.0 / b if b != 0 else math.copysign(math.inf, b)
                elif mop == 5: r = 1.0 / math.sqrt(b) if b > 0 else (math.inf if b == 0 else math.nan)
                elif mop == 8: r = math.sqrt(b) if b >= 0 else math.nan
                elif mop == 9: r = math.tanh(b)
                else: raise NotImplementedError(f'MUFU op {mop} at {x.off:#x}')
            except (OverflowError, ValueError):
                r = math.nan
            setf(r)
        elif op == 0x00b:   # FSETP
            a, b = self.two(L, x, fp=True)
            cmp = x.bits(76, 4)
            unord = math.isnan(a) or math.isnan(b)
            base = {1: a < b, 2: a == b, 3: a <= b, 4: a > b, 5: a != b, 6: a >= b}
            if cmp == 0: r = False
            elif cmp == 7: r = not unord
            elif cmp == 8: r = unord
            elif cmp in base: r = base[cmp] and not unord
            elif cmp - 8 in base: r = base[cmp - 8] or unord
            else: r = True
            acc = self.pred(L, x.bits(87, 3), x.bits(90, 1))
            sop = x.bits(74, 2)
            r = (r and acc) if sop == 0 else (r or acc) if sop == 1 else (r != acc)
            pd, pd1 = x.bits(81, 3), x.bits(84, 3)
            if pd != PT: L.p[pd] = r
            if pd1 != PT: L.p[pd1] = (not r) and acc if sop == 0 else (not r) or acc if sop == 1 else (not r) != acc
        elif op == 0x01c:   # PLOP3: dsts 81-83 / 84-86 = lut(p0 87-89, p1 77-79, p2 68-70); luts at 64-66|72-76 and 16-23
            p0 = self.pred(L, x.bits(87, 3), x.bits(90, 1))
            p1 = self.pred(L, x.bits(77, 3), x.bits(80, 1))
            p2 = self.pred(L, x.bits(68, 3), x.bits(71, 1))
            lut0 = x.bits(64, 3) | (x.bits(72, 5) << 3)
            lut1 = x.bits(16, 8)
            idx = (int(p0) << 2) | (int(p1) << 1) | int(p2)
            pd, pd1 = x.bits(81, 3), x.bits(84, 3)
            if pd != PT: L.p[pd] = (lut0 >> idx) & 1 == 1
            if pd1 != PT: L.p[pd1] = (lut1 >> idx) & 1 == 1
            d = RZ
        elif op == 0x00c:   # ISETP
            a, b = self.two(L, x)
            signed = x.bits(73, 1)
            if signed: a, b = s32(a), s32(b)
            cmp = x.bits(76, 3)
            r = {0: False, 1: a < b, 2: a == b, 3: a <= b, 4: a > b, 5: a != b, 6: a >= b, 7: True}[cmp]
            if x.bits(72, 1):
                raise NotImplementedError(f'ISETP.EX at {x.off:#x}')
            acc = self.pred(L, x.bits(87, 3), x.bits(90, 1))
            sop = x.bits(74, 2)
            r = (r and acc) if sop == 0 else (r or acc) if sop == 1 else (r != acc)
            pd, pd1 = x.bits(81, 3), x.bits(84, 3)
            if pd != PT: L.p[pd] = r
            if pd1 != PT: L.p[pd1] = (not r) and acc if sop == 0 else (not r) or acc if sop == 1 else (not r) != acc
        elif op == 0x017:   # IMNMX
            a, b = self.two(L, x)
            if x.bits(73, 1): a, b = s32(a), s32(b)
            use_min = self.pred(L, x.bits(87, 3), x.bits(90, 1))
            setd(min(a, b) if use_min else max(a, b))
        elif op == 0x011:   # LEA: dst = (a << shift) + b (low), or the high part with a_high; carry out at 81-83
            a, b, ah = self.srcs(L, x)
            shift = x.bits(75, 5)
            if x.bits(72, 1): a = (-a) & 0xffffffff
            if x.bits(80, 1):
                v = (((a | ah << 32) << shift) >> 32) + b
            else:
                v = ((a << shift) & 0xffffffff) + b
            if x.bits(74, 1):
                v += int(self.pred(L, x.bits(87, 3), x.bits(90, 1)))
            setd(v)
            pd = x.bits(81, 3)
            if pd != PT: L.p[pd] = v >> 32 != 0
        elif op == 0x045:   # I2FP
            b = self.src1(L, x)
            if x.form != 4 and x.bits(63, 1): b = (-b) & 0xffffffff
            v = s32(b) if x.bits(74, 1) else b
            setf(float(v))
        elif op == 0x105:   # F2I: source at 32-39 (float modifiers), signed 72, size 75-76, rounding 78-79 (0 round, 1 floor, 2 ceil, 3 trunc)
            _, b = self.two(L, x, fp=True)
            rnd = x.bits(78, 2)
            bits_ = 8 << x.bits(75, 2)
            if math.isnan(b): v = 0
            elif math.isinf(b):
                v = (((1 << (bits_ - 1)) - 1) if b > 0 else -(1 << (bits_ - 1))) if x.bits(72, 1) else (((1 << bits_) - 1) if b > 0 else 0)
            else:
                r = round(b) if rnd == 0 else math.floor(b) if rnd == 1 else math.ceil(b) if rnd == 2 else math.trunc(b)
                if rnd == 0 and abs(b - math.trunc(b)) == 0.5: r = 2 * round(b / 2)   # round half to even
                bits_ = 8 << x.bits(75, 2)
                if x.bits(72, 1): lo, hi = -(1 << (bits_ - 1)), (1 << (bits_ - 1)) - 1
                else: lo, hi = 0, (1 << bits_) - 1
                v = int(max(lo, min(hi, r)))
            setd(v & 0xffffffff)
        elif op == 0x107:   # FRND: to an integral float, rounding 78-79
            _, b = self.two(L, x, fp=True)
            rnd = x.bits(78, 2)
            if math.isnan(b) or math.isinf(b): r = b
            else:
                r = float(round(b)) if rnd == 0 else float(math.floor(b)) if rnd == 1 else float(math.ceil(b)) if rnd == 2 else float(math.trunc(b))
                if rnd == 0 and abs(b - math.trunc(b)) == 0.5: r = float(2 * round(b / 2))
            setf(r)
        elif op == 0x016:   # PRMT: byte permute of {c:a} by the selector nibbles of the wide operand (mode 72-74 = 0 only)
            a, b, c = self.srcs(L, x)
            if x.bits(72, 3): raise NotImplementedError(f'PRMT mode {x.bits(72, 3)} at {x.off:#x}')
            src = a | (c << 32)
            r = 0
            for i in range(4):
                nib = (b >> (4 * i)) & 0xf
                byte = (src >> (8 * (nib & 7))) & 0xff
                if nib & 8: byte = 0xff if byte & 0x80 else 0
                r |= byte << (8 * i)
            setd(r)
        elif op == 0x01a:   # SGXT: sign (73) or zero extend the low n bits of a, n = the wide operand (.W 75 wraps n mod 32, else clamps)
            a, n = self.two(L, x)
            n = n & 31 if x.bits(75, 1) else min(n, 32)
            if n == 0: r = 0
            elif n >= 32: r = a
            else:
                r = a & ((1 << n) - 1)
                if x.bits(73, 1) and r & (1 << (n - 1)): r |= (0xffffffff << n) & 0xffffffff
            setd(r)
        elif op == 0x100:   # FLO
            b = self.src1(L, x)
            if x.form != 4 and x.bits(63, 1): b = (~b) & 0xffffffff
            signed = x.bits(73, 1)
            if signed and b & 0x80000000:
                b = (~b) & 0xffffffff
            r = 0xffffffff if b == 0 else b.bit_length() - 1
            if x.bits(74, 1) and r != 0xffffffff:
                r = 31 - r
            setd(r)
        elif op == 0x109:   # POPC
            b = self.src1(L, x)
            if x.form != 4 and x.bits(63, 1): b = (~b) & 0xffffffff
            setd(bin(b).count('1'))
        elif op == 0x181:   # LD (0x381 register address, 0x981 uniform base)
            size = x.bits(73, 3)
            off = x.bits(40, 24)
            if off & 0x800000: off -= 1 << 24
            ra = x.bits(24, 8)
            if x.op == 0x381:
                addr = self.reg(L, ra) | (self.reg(L, ra + 1) << 32)
            else:
                u = x.bits(32, 6)
                addr = self.ureg(u) | (self.ureg(u + 1) << 32)
                if ra != RZ:
                    addr += self.reg(L, ra) | ((self.reg(L, ra + 1) << 32) if x.bits(90, 1) else 0)
            addr = (addr + off) & ((1 << 64) - 1)
            n = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 8, 6: 16}[size]
            v = self.gload(addr, n)
            if size == 1 and v & 0x80: v |= 0xffffff00
            if size == 3 and v & 0x8000: v |= 0xffff0000
            for k in range(max(1, n // 4)):
                if d + k != RZ:
                    L.r[d + k] = (v >> (32 * k)) & 0xffffffff
        elif op == 0x123:   # ISBERD: in region
            ra = x.bits(24, 8)
            addr = self.reg(L, ra) + x.bits(40, 24)
            setd(int.from_bytes(self.isbe_in[addr:addr + 4], 'little') if addr + 4 <= len(self.isbe_in) else 0)
        elif op == 0x127:   # ISBEWR
            ra, data = x.bits(24, 8), x.bits(32, 8)
            addr = self.reg(L, ra) + x.bits(40, 24)
            region, wide = x.bits(76, 2), x.bits(75, 1)
            v = self.reg(L, data)
            if region == 3:
                if addr + 4 <= len(self.attr):
                    self.attr[addr:addr + 4] = struct.pack('<I', v)
            elif region == 0:
                if wide:
                    self.map[4 * addr:4 * addr + 4] = struct.pack('<I', v)
                else:
                    self.map[4 * addr:4 * addr + 4] = struct.pack('<I', v & 0xff)
            else:
                raise NotImplementedError(f'ISBE region {region}')
        else:
            raise NotImplementedError(f'opcode {x.op:#x} at {x.off:#x}')
        if self.trace and L.lane == self.trace_lane:
            print(f'  lane {L.lane:2d} {x.off:#06x} op {x.op:03x} -> R{d} = {L.r[d] if d != RZ else 0:#010x} ({as_f(L.r[d] if d != RZ else 0):.4g})')


def load_capture(d, n):
    d = Path(d)
    index = (d / f'{n}_index.txt').read_text().splitlines()
    head = index[0]
    ctl = (d / f'{n}_s7_ctl.bin').read_bytes()
    code = (d / f'{n}_s7_code.bin').read_bytes()
    cbufs = {0: (d / f'{n}_s7_c0.bin').read_bytes()}
    mem = []
    for line in index[1:]:
        parts = line.split()
        st, kind, slot, addr, size = parts[:5]
        if st != 's7':
            continue
        if len(parts) > 5 and parts[5].startswith('='):   # "=<n>": the range an earlier capture wrote (any stage, any slot)
            src = int(parts[5][1:])
            data = None
            for l2 in (d / f'{src}_index.txt').read_text().splitlines()[1:]:
                p2 = l2.split()
                if len(p2) == 5 and p2[1] == kind and p2[3] == addr:   # (the writer's size text carries a stray 0: match the address)
                    data = (d / f'{src}_{p2[0]}_{kind}{p2[2]}.bin').read_bytes()
                    break
            if data is None:
                raise FileNotFoundError(f'capture {src} has no file for {addr}+{size}')
        else:
            data = (d / f'{n}_s7_{kind}{slot}.bin').read_bytes()
        if kind == 'ubo':
            cbufs[int(slot) + 3] = data
        else:   # ssbo, or ptr: a range a pointer in a storage block names
            mem.append((int(addr, 16), data))
    return head, ctl, code, cbufs, mem


def run_one(insns, cbufs, mem, lines, maxv, wg, meshlet, trace=False):
    m = Machine(insns, cbufs, mem, trace=trace)
    struct.pack_into('<I', m.isbe_in, 0, wg)
    struct.pack_into('<I', m.isbe_in, 0x10 + 4 * wg, meshlet)
    status = m.run()
    count = struct.unpack_from('<I', m.map, 12)[0]
    verts = []
    for v in range(maxv):
        g, s = v // 32, v % 32
        base = g * lines * 128 + s * 4
        verts.append(tuple(as_f(struct.unpack_from('<I', m.attr, base + k * 128)[0]) for k in range(min(lines, 4))))
    tri = [struct.unpack_from('<I', m.map, 4 * (4 + i))[0] & 0xff for i in range(3 * min(count, 126))]
    return m, status, count, verts, tri


def gpu_records(log):
    """OOT_MESH_DEBUG records with OOT_MESH_CAPTURE on (vk_exec_draw.c mesh_debug_log): {rec: (wg, meshlet, prims, verts, indices)}."""
    import re
    recs, verts, idx = {}, {}, {}
    for line in open(log, errors='replace'):
        m = re.search(r'debug record (\d+): .*workgroup (\d+) meshlet (\d+) primitives (\d+)', line)
        if m:
            recs[int(m.group(1))] = tuple(int(g) for g in m.groups()[1:])
            continue
        m = re.search(r'rec (\d+) v(\d+): (\S+) (\S+) (\S+) (\S+)', line)
        if m:
            verts.setdefault(int(m.group(1)), {})[int(m.group(2))] = tuple(float(g) for g in m.groups()[2:])
            continue
        m = re.search(r'rec (\d+) indices:(.*)', line)
        if m:
            idx[int(m.group(1))] = [tuple(int(e) for e in t.split(',')) for t in m.group(2).split()]
    return {r: (wg, ml, pr, verts.get(r, {}), idx.get(r, [])) for r, (wg, ml, pr) in recs.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dir')
    ap.add_argument('n', type=int)
    ap.add_argument('--meshlet', type=int, default=0)
    ap.add_argument('--wg', type=int, default=0)
    ap.add_argument('--trace', action='store_true')
    ap.add_argument('--lines', type=int, default=0, help='attribute lines per vertex (default: from the header)')
    ap.add_argument('--compare', help='run log with the GPU records of the same draws: every record is run and compared')
    args = ap.parse_args()
    head, ctl, code, cbufs, mem = load_capture(args.dir, args.n)
    end = struct.unpack_from('<I', ctl, 0x38)[0]
    insns = [Insn(struct.unpack_from('<4I', code, o), o) for o in range(0x80, min(end, len(code)), 16)]
    lines = args.lines or bin(code[0x35]).count('1') + sum(bin(b).count('1') for b in code[0x36:0x46])
    maxv = struct.unpack_from('<H', ctl, 0x60)[0] or 128
    print(head)
    print(f'{len(insns)} instructions, {lines} lines per vertex, max {maxv} vertices')
    if args.compare:
        for r, (wg, ml, pr, gv, gi) in sorted(gpu_records(args.compare).items()):
            try:
                m, status, count, cv, ct = run_one(insns, cbufs, mem, lines, maxv, wg, ml)
            except NotImplementedError as e:
                print(f'rec {r}: {e}')
                continue
            same_tri = sum(1 for a, b in zip(gi, [tuple(ct[3 * i:3 * i + 3]) for i in range(len(ct) // 3)]) if a == b)
            dmax = [0.0] * 4
            n = 0
            for v, g in gv.items():
                if v < len(cv) and any(g):
                    n += 1
                    for k in range(4):
                        dmax[k] = max(dmax[k], abs(cv[v][k] - g[k]))
            flag = 'MATCH' if max(dmax) < 1e-2 * max(1.0, max(abs(q) for g in gv.values() for q in g)) else 'DIFF'
            print(f'rec {r:2d} wg {wg:3d} meshlet {ml:3d}: GPU prims {pr:3d} CPU {count:3d} ({status}); triangles equal {same_tri}/{len(gi)}; '
                  f'max |CPU-GPU| x {dmax[0]:.4g} y {dmax[1]:.4g} z {dmax[2]:.4g} w {dmax[3]:.4g} over {n} vertices  {flag}')
            if flag == 'DIFF':
                for v in list(gv)[:3]:
                    print(f'      v{v}: CPU {tuple(round(q, 4) for q in cv[v])}  GPU {gv[v]}')
        return
    m, status, count, verts, tri = run_one(insns, cbufs, mem, lines, maxv, args.wg, args.meshlet, trace=args.trace)
    print(f'workgroup {args.wg} meshlet {args.meshlet}: run: {status}; primitive count {count}')
    for k, v in m.unknown.items():
        print(f'  note: {k}: {len(v)} ({sorted(v)[:4]})')
    seen = 0
    for v, vals in enumerate(verts):
        if all(x == 0 for x in vals):
            continue
        seen += 1
        if seen <= 16 or v % 16 == 0:
            x, y, z, w = (list(vals) + [0, 0, 0, 0])[:4]
            ndc = f'ndc ({x / w:7.3f} {y / w:7.3f})' if w not in (0, 0.0) and not math.isnan(w) else ''
            print(f'  v{v:2d}: clip ({x:9.3f} {y:9.3f} {z:9.3f} {w:9.3f}) {ndc}')
    print(f'  {seen} vertices written; first triangles {tri[:12]}')


if __name__ == '__main__':
    main()
