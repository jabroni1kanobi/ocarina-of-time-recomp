"""Disassemble the instructions before a return address in an NSO (module image offsets).

    python dis_call.py <repo root> <nso path> <return offset hex> [count]
"""
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root / 'recomp'))
from nso import Nso  # noqa: E402
import capstone  # noqa: E402

nso = Nso(sys.argv[2])
ret = int(sys.argv[3], 16)
count = int(sys.argv[4]) if len(sys.argv) > 4 else 24
start = ret - 4 * count
md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
for insn in md.disasm(bytes(nso.image[start:ret + 8]), start):
    print(f'{insn.address:#010x}  {insn.mnemonic:8s} {insn.op_str}')
