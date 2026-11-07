"""Pack a reference (Lichii-lowered) Maxwell instruction stream into the layout our translator reads for a task / mesh
program: a zero scheduling word before every group of three instructions, and Lichii's ISBE pseudo instruction (top
bits 0xefc: docs/mesh-spec.md 1.3) turned into the shared-memory accesses of our own lowering (runtime/sm86_lower.c
lower_isbe): a read -> LDS.32 rd, [ra + in + offset]; an attribute write -> STS.32 [ra + attr + offset]; a map write
-> STS.32 [(ra << 2) + map + 4 * offset] (one word per byte address; the epilogue masks the index to 8 bits). The
shift needs a register: R253 is saved to and restored from the uniform spill area, which the reference code never
touches. Dev tool for the white-out investigation (docs/handoff-whiteout.md); the reference stream comes from
build/lichii-ref/lichii_lower.exe (a harness around the GPL header, kept out of the repository).

    python tools/dev/lichii_pack.py <in.u64> <out.code> <in> <attr> <map> <spill>      (region byte offsets)
"""
import struct
import sys

LDS = 0xef48000000000000
STS = 0xef58000000000000
SHL = 0x3848000000000000   # SHL R, R, imm (Maxwell 0x3848)
RZ, PT = 255, 7


def mem(op, pred, reg, ra, off, size=4):
    assert 0 <= off < (1 << 24), hex(off)
    return op | (pred << 16) | reg | (ra << 8) | (off << 20) | (size << 48)


def convert(words, in_, attr, map_, spill):
    out = []
    save = spill + 0xf8
    for w in words:
        if (w >> 52) != 0xefc:
            out.append(w)
            continue
        reg, ra, pred = w & 0xff, (w >> 8) & 0xff, (w >> 16) & 0xf
        off = (w >> 20) & 0xffffff
        wide, is_attr, skew, o, write = (w >> 44) & 1, (w >> 45) & 1, (w >> 46) & 1, (w >> 47) & 1, (w >> 48) & 1
        if not write:
            out.append(mem(LDS, pred, reg, ra, in_ + off))
        elif is_attr:
            out.append(mem(STS, pred, reg, ra, attr + off))
        elif ra == RZ:
            out.append(mem(STS, pred, reg, RZ, map_ + 4 * off))
        else:
            out.append(mem(STS, pred, 253, RZ, save))                       # save R253
            out.append(SHL | (pred << 16) | 253 | (ra << 8) | (2 << 20))      # R253 = ra << 2
            out.append(mem(STS, pred, reg, 253, map_ + 4 * off))            # map[ra*4 + off*4] = data
            out.append(mem(LDS, pred, 253, RZ, save))                       # restore
    return out


def pack(insns):
    code = []
    for i, w in enumerate(insns):
        if i % 3 == 0:
            code.append(0)
        code.append(w)
    while len(code) % 4:
        code.append(0)
    return code


def main():
    src, dst = sys.argv[1], sys.argv[2]
    in_, attr, map_, spill = (int(x, 0) for x in sys.argv[3:7])
    d = open(src, 'rb').read()
    words = struct.unpack(f'<{len(d) // 8}Q', d)
    conv = convert(words, in_, attr, map_, spill)
    code = pack(conv)
    open(dst, 'wb').write(struct.pack(f'<{len(code)}Q', *code))
    print(f'{len(words)} reference instructions -> {len(conv)} ({len(code)} words with scheduling)')


if __name__ == '__main__':
    main()
