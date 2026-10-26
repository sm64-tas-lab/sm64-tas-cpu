import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
CODE_RANGES = ((0x80246000, 0x8032D560), (0x80378800, 0x80385F90))
LOADS = {0x20: 'lb', 0x24: 'lbu', 0x21: 'lh', 0x25: 'lhu', 0x23: 'lw', 0x31: 'lwc1', 0x28: 'sb', 0x29: 'sh',
         0x2B: 'sw', 0x39: 'swc1', 0x35: 'ldc1', 0x3D: 'sdc1', 0x09: 'addr'}
_CACHE = None


def coderefs(st_path=None):
    global _CACHE
    if _CACHE is not None and st_path is None:
        return _CACHE
    import st_bridge as SB
    rd = SB.rdram(st_path or os.path.join(os.path.dirname(HERE), 'MOVIE_0STAR', '00_reset.st'))
    refs = {}
    for lo, hi in CODE_RANGES:
        w = struct.unpack(f'>{(hi - lo) // 4}I', rd[lo - 0x80000000:hi - 0x80000000])
        for i, x in enumerate(w):
            if x >> 26 != 0x0F:
                continue
            rt, h = (x >> 16) & 31, x & 0xFFFF
            for y in w[i + 1:i + 12]:
                op, rs, rt2, imm = y >> 26, (y >> 21) & 31, (y >> 16) & 31, y & 0xFFFF
                if op in LOADS and rs == rt:
                    a = ((h << 16) + (imm - 0x10000 if imm & 0x8000 else imm)) & 0xFFFFFFFF
                    if 0x80000000 <= a < 0x80800000:
                        refs.setdefault(a, set()).add(LOADS[op])
                    if op == 0x09 or rt2 == rt:
                        break
                elif rt2 == rt and op not in (0x2B, 0x29, 0x28, 0x39, 0x3D):
                    break
    if st_path is None:
        _CACHE = refs
    return refs


if __name__ == '__main__':
    r = coderefs()
    print(len(r), 'referenced addresses')
    for a in sorted(r):
        if 0x80331600 <= a < 0x80331800 or 0x8033B000 <= a < 0x8033B100:
            print(hex(a), sorted(r[a]))
