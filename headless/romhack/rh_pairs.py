import glob
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import n64_lvl as N

OUT = os.path.join(HERE, '_rh_pairs.json')

LVL_FIELDS = {
    0x00: ['BBH', 'PTR:rom', 'PTR:rom', 'PTR:lvl'], 0x01: ['BBH', 'PTR:rom', 'PTR:rom', 'PTR:lvl'],
    0x05: ['BBH', 'PTR:lvl'], 0x06: ['BBH', 'PTR:lvl'], 0x0B: ['BBBB', 'W'], 0x0C: ['BBBB', 'W', 'PTR:lvl'],
    0x0D: ['BBBB', 'W', 'PTR:lvl'], 0x0E: ['BBBB', 'W'], 0x11: ['BBH', 'PTR:func'], 0x12: ['BBH', 'PTR:func'],
    0x16: ['BBH', 'PTR:ram', 'PTR:rom', 'PTR:rom'], 0x17: ['BBH', 'PTR:rom', 'PTR:rom'],
    0x18: ['BBH', 'PTR:rom', 'PTR:rom'], 0x1A: ['BBH', 'PTR:rom', 'PTR:rom'], 0x1F: ['BBBB', 'PTR:geo'],
    0x21: ['BBH', 'PTR:dl'], 0x22: ['BBH', 'PTR:geo'], 0x23: ['BBH', 'PTR:ram', 'W'],
    0x24: ['BBBB', 'HH', 'HH', 'HH', 'W', 'PTR:bhv'], 0x25: ['BBBB', 'W', 'PTR:bhv'], 0x26: ['BBBB', 'BBBB'],
    0x27: ['BBBB', 'BBBB'], 0x28: ['BBBB', 'HH', 'HH'], 0x29: ['BBBB'], 0x2A: ['BBBB'],
    0x2B: ['BBBB', 'HH', 'HH'], 0x2E: ['BBH', 'PTR:col'], 0x2F: ['BBH', 'PTR:rooms'], 0x30: ['BBBB'],
    0x33: ['BBBB', 'BBBB'], 0x34: ['BBBB'], 0x35: ['BBBB'], 0x36: ['BBH', 'HH'], 0x39: ['BBH', 'PTR:macro'],
    0x3A: ['BBH', 'HH', 'HH'], 0x3B: ['BBBB', 'HH', 'HH'], 0x3C: ['BBBB'],
}


def lvl_fields(cid, size):
    f = LVL_FIELDS.get(cid)
    if f is None:
        f = ['BBH'] + ['W'] * (size // 4 - 1)
    if len(f) != size // 4:
        raise ValueError(f'level command {cid:02X}: size {size} vs {len(f)} fields')
    return f


def host_word(g, a):
    return g.read(a, 'Q')


def pair_level(m, a, g, h, pairs, seen, depth=0, rom=None):
    while (a, h) not in seen and depth < 64:
        try:
            cid, size = m.u8(a), m.u8(a + 1)
        except KeyError:
            return
        seen.add((a, h))
        hw = host_word(g, h)
        if (hw & 0xFF) != cid or ((hw >> 8) & 0xFF) != size:
            pairs.setdefault('_mismatch', []).append((hex(a), hex(h)))
            return
        f = lvl_fields(cid, size)
        if rom is not None and cid in (0x00, 0x01, 0x17, 0x18):
            s_, r0, r1 = m.u8(a + 3), m.u32(a + 4), m.u32(a + 8)
            try:
                m.segs[s_] = rom[r0:r1] if cid != 0x18 else _mio0(rom, r0)
            except Exception:
                pass
        for i, k in enumerate(f):
            if not k.startswith('PTR:'):
                continue
            v, hv = m.u32(a + 4 * i), host_word(g, h + 8 * i)
            kind = k[4:]
            if not v or not hv or kind == 'rom':
                continue
            pairs.setdefault(kind, {})[hex(v)] = hv - g.base
            if kind == 'lvl':
                pair_level(m, v, g, hv, pairs, seen, depth + 1, rom)
            elif kind == 'geo':
                pair_geo(m, v, g, hv, pairs, seen, depth + 1)
        if cid in (0x02, 0x07, 0x05) or size == 0:
            return
        a += size
        h += 8 * (size // 4)


def pair_geo(m, a, g, h, pairs, seen, depth=0):
    while ('g', a, h) not in seen and depth < 64:
        try:
            f = N.geo_fields(m, a)
            c = m.u8(a)
        except (KeyError, ValueError):
            return
        seen.add(('g', a, h))
        if (host_word(g, h) & 0xFF) != c:
            pairs.setdefault('_mismatch', []).append((hex(a), hex(h)))
            return
        for i, k in enumerate(f):
            if not k.startswith('PTR:'):
                continue
            v, hv = m.u32(a + 4 * i), host_word(g, h + 8 * i)
            if not v or not hv:
                continue
            kind = k[4:]
            pairs.setdefault(kind, {})[hex(v)] = hv - g.base
            if kind == 'geo':
                pair_geo(m, v, g, hv, pairs, seen, depth + 1)
        if c in (0x01, 0x03) or (c == 0x02 and m.u8(a + 1) == 0):
            return
        a += 4 * len(f)
        h += 8 * len(f)


def _mio0(rom, off):
    import rh_level
    return rh_level.mio0(rom, off)


def collect_rom(rom_path, st_path):
    from sm64hl import Game
    g = Game()
    rom = open(rom_path, 'rb').read()
    m, st = N.from_st(st_path)
    m.seg_base = {s: b for s, b in m.seg_base.items() if s in (0x13, 0x15, 0x16, 0x17)}
    pairs, seen = {}, set()
    pair_level(m, 0x15000000, g, g.addr('level_main_scripts_entry'), pairs, seen, rom=rom)
    pairs.pop('_mismatch', None)
    return pairs


def collect(st_paths):
    from sm64hl import Game
    g = Game()
    pairs, seen = {}, set()
    host_main = g.addr('level_main_scripts_entry')
    for p in st_paths:
        m, st = N.from_st(p)
        pair_level(m, 0x15000000, g, host_main, pairs, seen)
    pairs.pop('_mismatch', None)
    return pairs


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hl_paths
VANILLA_ROM = hl_paths.ROM


def main():
    sts = sys.argv[1:] or sorted(glob.glob(os.path.join(os.path.dirname(os.path.dirname(HERE)), 'MOVIE_0STAR', '*.st')))
    pairs = collect(sts)
    if os.path.exists(VANILLA_ROM):
        pr = collect_rom(VANILLA_ROM, [p for p in sts if not p.endswith('00_reset.st')][0])
        for k, d in pr.items():
            for a, h in d.items():
                pairs.setdefault(k, {}).setdefault(a, h)
    json.dump(pairs, open(OUT, 'w'), indent=0)
    print({k: len(v) for k, v in pairs.items()}, '->', OUT)


if __name__ == '__main__':
    main()
