#!/usr/bin/env python3
"""Serve the RomFS from the .nup itself instead of a 26 GB romfs.bin copy.

    python3.14 tools/nup_romfs_inplace.py [--config config.local.toml] [--romfs DIR] [--patches DIR]

runtime/romfs.c reads one image through an index of (offset, size, path) and never parses the RomFS format. In this
game's .nup 146,000 of the 186,587 RomFS files (21.65 of 24.43 GiB) are stored raw, so the .nup can be that image:

- <romfs>/romfs.bin is a hard link to the .nup (same volume, no copy; the runtime opens it read only), and
  <romfs>/index.json lists every raw member at its absolute offset in the .nup, with the .nup's size and SHA-256 as
  the image's. tools/build_game.py (tools/romfs_index.py) turns it into romfs-index.bin as for any extraction.
- The zstd members (40,588 files, 2.79 GiB unpacked) cannot be read in place. They are decompressed into
  <patches>/, default build/v<version>/romfs-patches next to romfs-index.bin, whose manifest.tsv adds them to the
  RomFS (original SHA-256 "-"), the overlay form runtime/romfs.c overlay_load takes.

Status "pass" only when every raw member matched its SHA-256 from the NUP TOC (checked in one sequential pass over the
.nup, which also yields the image's SHA-256) and every zstd member decompressed to its recorded size and SHA-256.

The hard link shares the .nup's data: never open <romfs>/romfs.bin for writing. Deleting it only removes the link.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import extract_nup

ROOT = Path(__file__).resolve().parent.parent
CHUNK = 64 << 20


def check_raw(nup, raw):
    """One sequential read of the whole .nup: the SHA-256 of the file and of every raw member, which must match the
    TOC. raw: [(path, rec)] with size > 0. The .nup stores identical files once, so several paths can share an
    extent; each extent is hashed once and checked against every path that names it. Returns the file's SHA-256."""
    extents = {}
    for path, rec in raw:
        extents.setdefault((rec['offset'], rec['size']), []).append((path, rec))
    members = sorted(extents.items())
    whole = hashlib.sha256()
    active = []   # [end, hash, start, [(path, rec)]] of extents the read is inside
    nxt, pos, total = 0, 0, nup.path.stat().st_size
    with open(nup.path, 'rb', buffering=0) as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            view = memoryview(chunk)
            whole.update(view)
            end = pos + len(chunk)
            while nxt < len(members) and members[nxt][0][0] < end:
                (start, size), names = members[nxt]
                active.append([start + size, hashlib.sha256(), start, names])
                nxt += 1
            done = []
            for a in active:
                lo, hi = max(a[2], pos), min(a[0], end)
                if lo < hi:
                    a[1].update(view[lo - pos:hi - pos])
                if a[0] <= end:
                    digest = a[1].digest()
                    for path, rec in a[3]:
                        extract_nup.require(digest == rec['sha256'], f'{path}: SHA-256 mismatch in the .nup')
                    done.append(a)
            for a in done:
                active.remove(a)
            pos = end
            if pos // CHUNK % 64 == 0 or pos == total:
                print(f'nup pass: {pos / 2**30:.2f}/{total / 2**30:.2f} GiB, {nxt}/{len(members)} raw extents',
                      flush=True)
    extract_nup.require(pos == total and nxt == len(members) and not active, 'short read of the .nup')
    return whole.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--config', default=str(ROOT / 'config.local.toml'))
    ap.add_argument('--input', help='the .nup (default: [input] base from the config)')
    ap.add_argument('--romfs', type=Path, help='RomFS directory (default build/extracted/v<version>/romfs)')
    ap.add_argument('--patches', type=Path, help='overlay directory for the zstd members '
                                                 '(default build/v<version>/romfs-patches)')
    args = ap.parse_args()
    src = args.input or extract_nup.win_to_wsl(extract_nup.load_config(args.config)['input']['base'])
    try:
        nup = extract_nup.Nup(src)
        ups = [p for p in nup.packages if p['kind'] == 'update']
        pkg = max(ups, key=lambda p: p['version']) if ups else next(p for p in nup.packages if p['kind'] == 'base')
        extract_nup.require(pkg['romfs_dir'] is not None, 'the package has no RomFS')
        version = f'v{pkg["version"]}'
        romfs = args.romfs or ROOT / 'build' / 'extracted' / version / 'romfs'
        patches = args.patches or ROOT / 'build' / version / 'romfs-patches'
        entries = sorted(nup.walk(pkg['romfs_dir']), key=lambda e: e[0].encode())
        for path, _ in entries:
            extract_nup.require('..' not in path.split('/') and not path.startswith('/') and '\t' not in path,
                                f'unsafe path {path!r}')
        raw = [(p, r) for p, r in entries if r['codec'] == 0]
        packed = [(p, r) for p, r in entries if r['codec'] == 1]
        print(f'romfs: {len(raw)} raw files ({sum(r["size"] for _, r in raw) / 2**30:.2f} GiB, read in place), '
              f'{len(packed)} zstd files ({sum(r["size"] for _, r in packed) / 2**30:.2f} GiB, extracted)', flush=True)

        # The zstd members first: the overlay folder, verified, its manifest written last.
        patches.mkdir(parents=True, exist_ok=True)
        lines = ['# RomFS overlay: the .nup members stored as zstd (tools/nup_romfs_inplace.py)\n']
        for n, (path, rec) in enumerate(packed, 1):
            out = patches / path
            if not (out.is_file() and out.stat().st_size == rec['size']
                    and hashlib.sha256(out.read_bytes()).digest() == rec['sha256']):
                nup.extract_file(rec, out, True)
            lines.append(f'{path}\t{rec["size"]}\t{rec["sha256"].hex()}\t-\n')
            if n % 2000 == 0 or n == len(packed):
                print(f'overlay: {n}/{len(packed)} files', flush=True)
        (patches / 'manifest.tsv.part').write_text(''.join(lines), encoding='utf-8')
        os.replace(patches / 'manifest.tsv.part', patches / 'manifest.tsv')

        # The raw members in place: every one checked, then the link and the index.
        image_sha = check_raw(nup, [(p, r) for p, r in raw if r['size']])
        romfs.mkdir(parents=True, exist_ok=True)
        image = romfs / 'romfs.bin'
        if image.exists() and not os.path.samefile(image, nup.path):
            raise extract_nup.NupError(f'{image} exists and is not the .nup; move it away first')
        if not image.exists():
            os.link(nup.path, image)
        size = nup.path.stat().st_size
        index = {'status': 'pass', 'version': pkg['version'], 'source': nup.path.name, 'romfs_size': size,
                 'romfs_sha256': image_sha,
                 'integrity': ('romfs.bin is a hard link to the .nup; every raw member checked against its SHA-256 in '
                               'the NUP TOC, whose own SHA-256 the superblock holds; the zstd members are the overlay '
                               f'in {patches}, each checked the same way'),
                 'files': [{'path': p, 'offset': r['offset'] if r['size'] else 0, 'size': r['size']} for p, r in raw]}
        (romfs / 'index.json.part').write_text(json.dumps(index), encoding='utf-8')
        os.replace(romfs / 'index.json.part', romfs / 'index.json')
        print(f'wrote {romfs / "index.json"} ({len(raw)} files in place) and {patches / "manifest.tsv"} '
              f'({len(packed)} files)')
    except extract_nup.NupError as e:
        print(f'nup_romfs_inplace: {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
