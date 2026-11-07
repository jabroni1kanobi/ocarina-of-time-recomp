"""Find where a translated shader first makes a NaN (or an infinity), on the GPU, in a real frame.

    python tools/dev/nanbisect.py in.glsl out.comp [--inf]

in.glsl is spirv-cross output of a cached shader (shader-cache/<key>.spv). Every scalar or vector float assignment
gets a check after it; the first one that sees a NaN (with --inf: a NaN or an infinity) records its line in in.glsl,
and every imageStore stores that line instead of its value: r = line >> 6, g = line & 63 (each /64, exact in
r11g11b10f and in the dump's 8-bit PNG), b = 1 as the marker; a store without a NaN writes 0 (the frame is wrong on
purpose: only the marks matter). Compile out.comp with glslangValidator -V, put the
.spv in place of the cached one (keep the original), run with OOT_DUMP, and read the written image's PNG with
--decode:

    python tools/dev/nanbisect.py --decode pass_..._img0_....png

prints how many texels carry each line. A diagnostic, never shipped: put the original .spv back afterwards.
"""
import re
import sys
from collections import Counter

DECL = re.compile(r'^(\s*)(?:precise )?(float|vec[234]) (_\d+) = .*;\s*$')
ASSIGN = re.compile(r'^(\s*)(_\d+) = .*;\s*$')


def split_args(s):
    """Top-level comma split of the text inside imageStore( ... )."""
    out, depth, cur = [], 0, ''
    for ch in s:
        if ch == ',' and depth == 0:
            out.append(cur)
            cur = ''
            continue
        depth += ch in '(['
        depth -= ch in ')]'
        cur += ch
    out.append(cur)
    return out


def instrument(src, inf):
    lines = src.split('\n')
    types = {}
    for ln in lines:
        m = DECL.match(ln)
        if m:
            types[m.group(3)] = m.group(2)
        for t, v in re.findall(r'\b(float|vec[234]) (_\d+)\b', ln):
            types.setdefault(v, t)
    bad = '(isnan({0}) || isinf({0}))' if inf else 'isnan({0})'
    out = []
    n, declared = 0, False
    for i, ln in enumerate(lines, 1):
        if not declared and re.match(r'^(int|uint|float|vec\d|bool|void) \w+\(', ln):   # before the first function
            out.append('int g_nan = 0;')
            declared = True
        ln = rewrite_stores(ln)
        out.append(ln)
        m = DECL.match(ln)
        var = m.group(3) if m else None
        if not m:
            m2 = ASSIGN.match(ln)
            if m2 and m2.group(2) in types:
                var = m2.group(2)
                m = m2
        if var:
            t = types[var]
            test = bad.format(var)
            if t != 'float':
                test = 'any(' + test.replace(' || ', ') || any(') + ')'
            out.append(f'{m.group(1)}if (g_nan == 0 && {test}) g_nan = {i};')
            n += 1
    return '\n'.join(out), n


def rewrite_stores(ln):
    out, rest = '', ln
    while 'imageStore(' in rest:
        k = rest.index('imageStore(') + len('imageStore(')
        depth, j = 1, k
        while depth:
            depth += rest[j] == '('
            depth -= rest[j] == ')'
            j += 1
        args = split_args(rest[k:j - 1])
        code = 'vec4(float(g_nan >> 6) / 64.0, float(g_nan & 63) / 64.0, 1.0, 1.0)'
        args[2] = f' (g_nan != 0 ? {code} : vec4(0.0))'
        out += rest[:k] + ','.join(args) + ')'
        rest = rest[j:]
    return out + rest


def decode(path):
    from PIL import Image
    im = Image.open(path).convert('RGBA')
    c = Counter()
    for r, g, b, a in im.getdata():
        if b == 255:
            c[round(r * 64 / 255) * 64 + round(g * 64 / 255)] += 1
    total = im.size[0] * im.size[1]
    print(f'{path}: {sum(c.values())} of {total} texels marked')
    for line, k in c.most_common(20):
        print(f'  line {line}: {k}')


if __name__ == '__main__':
    if sys.argv[1] == '--decode':
        for p in sys.argv[2:]:
            decode(p)
        sys.exit(0)
    src = open(sys.argv[1]).read()
    text, n = instrument(src, '--inf' in sys.argv)
    open(sys.argv[2], 'w').write(text)
    print(f'{n} checks')
