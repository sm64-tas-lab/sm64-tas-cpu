import ctypes
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import n64_lvl as N
import rh_pairs as P

FIXED_SEG_RAM = {0x05: 0x80720000, 0x06: 0x80570000, 0x07: 0x80620000, 0x0E: 0x80420000, 0x13: 0x807B0000}
AREA_TABLE, AREA_MAGIC_AT, AREA_MAGIC = 0x19005F00, 0x19005FFC, 0x4BC9189A
GLOBAL_SEGS = (0x15, 0x16, 0x17)


def mio0(data, off=0):
    if data[off:off + 4] != b'MIO0':
        raise ValueError('not MIO0')
    size, comp, uncomp = struct.unpack_from('>III', data, off + 4)
    out = bytearray()
    bits_p, comp_p, raw_p = off + 16, off + comp, off + uncomp
    bit_n, word = 0, 0
    while len(out) < size:
        if bit_n == 0:
            word = struct.unpack_from('>I', data, bits_p)[0]
            bits_p += 4
            bit_n = 32
        if word & 0x80000000:
            out.append(data[raw_p])
            raw_p += 1
        else:
            v = struct.unpack_from('>H', data, comp_p)[0]
            comp_p += 2
            length, dist = (v >> 12) + 3, (v & 0xFFF) + 1
            for _ in range(length):
                out.append(out[-dist])
        word = (word << 1) & 0xFFFFFFFF
        bit_n -= 1
    return bytes(out[:size])


def _write_ro(addr, data):
    k32 = ctypes.windll.kernel32
    old = ctypes.c_uint32()
    k32.VirtualProtect(ctypes.c_void_p(addr), len(data), 0x04, ctypes.byref(old))
    ctypes.memmove(addr, data, len(data))
    k32.VirtualProtect(ctypes.c_void_p(addr), len(data), old.value, ctypes.byref(old))


class RomhackLevels:
    def __init__(self, g, st, rom_path, tr, pairs_path=P.OUT, bounds4=True, log=None, area_map=None):
        self.g, self.st, self.tr, self.log = g, st, tr, log or (lambda *a: None)
        self.area_map = {int(k): {int(a): int(b) for a, b in v.items()} for k, v in (area_map or {}).items()}
        self._amap = {}
        self.rom = open(rom_path, 'rb').read()
        if self.rom[:4] != b'\x80\x37\x12\x40':
            raise ValueError('ROM must be .z64 (big-endian)')
        if not os.path.exists(pairs_path):
            P.main()
        p = json.load(open(pairs_path))
        self.pairs = {k: {int(a, 16): v for a, v in d.items()} for k, d in p.items()}
        self.bounds4 = bounds4
        seg = {s: st.get('I', st.all['sSegmentTable'] + 4 * s) for s in range(32)}
        self.ram_seg = {s: b for s, b in seg.items() if b}
        self.keep, self.done = [], {}
        self.issues = []
        self.dummy_dl = g.base + next(iter(self.pairs['dl'].values()))
        self.patched = []
        self._roms = {}
        self.area_flags = {}

    def _rom(self, r0, r1, is_mio0=False):
        k = (r0, r1, is_mio0)
        if k not in self._roms:
            self._roms[k] = mio0(self.rom, r0) if is_mio0 else self.rom[r0:r1]
        return self._roms[k]

    def _mem(self, segs):
        m = N.Mem(self.st.rd, {s: b for s, b in self.ram_seg.items() if s in (0x13, 0x15, 0x16, 0x17)}, dict(segs))
        return m

    def _host_buf(self, data):
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        self.keep.append(buf)
        return ctypes.addressof(buf)

    def ptr(self, m, kind, v, ctx):
        if v == 0 or kind in ('rom', 'ram'):
            return 0
        seg = v >> 24
        if kind == 'func':
            h = self.pairs.get('func', {}).get(v)
            if h is None:
                self.issues.append(f'function {v:08X} ({ctx}) not in the vanilla pairs (custom code of the hack, '
                                   f'or a vanilla function no vanilla level uses): left NULL')
                return 0
            return self.g.base + h
        if kind == 'bhv':
            a = v if v >= 0x80000000 else 0x80000000 + self.ram_seg[0x13] + (v & 0xFFFFFF)
            h = self.tr.bhv.to_host(a)
            if h is None and hasattr(self.tr.bhv, 'translate_at'):
                def data_ptr(c, w, m=m):
                    if c != 0x2A:
                        return None
                    try:
                        return self.ptr(self._data_mem(m), 'col', w, f'{ctx} bhv {v:08X}') or None
                    except (KeyError, IndexError, struct.error):
                        return None
                h = self.tr.bhv.translate_at(a, data_ptr)
            if h is None:
                brk = (self.tr.bhv.by_name.get('bhvSpinAirborneWarp') or (None,) * 4)[3]
                self.issues.append(f'unknown behavior {v:08X} ({ctx})' + (': spawned inert' if brk else ''))
                return brk or 0
            return h
        if kind in ('lvl', 'geo') and seg in GLOBAL_SEGS and v in self.pairs.get(kind, {}):
            return self.g.base + self.pairs[kind][v]
        key = self._key(m, kind, v)
        if key in self.done:
            return self.done[key]
        if kind == 'lvl':
            return self.convert_level(m, v)
        elif kind == 'geo':
            return self.convert_geo(m, v)
        elif kind == 'dl':
            return self.dummy_dl
        elif kind == 'col':
            h = self.convert_collision(m, v)
        elif kind in ('rooms', 'macro'):
            b, o = m.buf(v)
            data = bytes(b[o:])
            if kind == 'macro':
                n = 0
                while n * 10 + 2 <= len(data) and struct.unpack_from('>h', data, n * 10)[0] != 0x1E:
                    n += 1
                vals = struct.unpack_from(f'>{5 * n + 1}h', data)
                h = self._host_buf(struct.pack(f'<{len(vals)}h', *vals))
            else:
                h = self._host_buf(data[:0x4000])
        else:
            raise ValueError(kind)
        self.done[key] = h
        return h

    @staticmethod
    def word(m, a, kind):
        b = m.bytes(a, 4)
        if kind == 'BBBB':
            return int.from_bytes(b, 'little')
        if kind == 'BBH':
            return b[0] | b[1] << 8 | struct.unpack('>H', b[2:])[0] << 16
        if kind == 'HH':
            return struct.unpack('>H', b[:2])[0] | struct.unpack('>H', b[2:])[0] << 16
        if kind == 'W':
            return struct.unpack('>I', b)[0]
        raise ValueError(kind)

    @staticmethod
    def _key(m, kind, v):
        try:
            b, o = m.buf(v)
            return (kind, id(b), o)
        except KeyError:
            return (kind, v, None)

    def _reserve(self, m, kind, a, n_words):
        buf = ctypes.create_string_buffer(8 * n_words)
        self.keep.append(buf)
        self.done[self._key(m, kind, a)] = ctypes.addressof(buf)
        return buf

    def convert_level(self, m, a):
        if self._key(m, 'lvl', a) in self.done:
            return self.done[self._key(m, 'lvl', a)]
        a0 = a
        out, cmds = [], []
        area = None
        banks = []
        while True:
            cid, size = m.u8(a), m.u8(a + 1)
            f = P.lvl_fields(cid, size)
            cmds.append((a, cid, size, f))
            if cid in (0x00, 0x01):
                s, r0, r1 = m.u8(a + 3), m.u32(a + 4), m.u32(a + 8)
                m.segs[s] = self._rom(r0, r1)
            elif cid == 0x17 and m.u8(a + 2) == 1:
                s, r0, r1 = m.u8(a + 3), m.u32(a + 4), m.u32(a + 8)
                m.segs[s] = self._rom(r0, r1)
            elif cid == 0x17 or cid == 0x18:
                s, r0, r1 = m.u8(a + 3), m.u32(a + 4), m.u32(a + 8)
                m.segs[s] = self._rom(r0, r1, cid == 0x18)
            elif cid == 0x1F:
                area = m.u8(a + 2)
                self._area_bank(m, area)
            banks.append(getattr(m, 'area_bank', None))
            if cid in (0x02, 0x07, 0x05) or size == 0:
                break
            a += size
        buf = self._reserve(m, 'lvl', a0, sum(len(f) for _, _, _, f in cmds))
        for (a, cid, size, f), bank in zip(cmds, banks):
            for i, k in enumerate(f):
                if k.startswith('PTR:'):
                    kind = k[4:]
                    v = m.u32(a + 4 * i)
                    m.area_bank = bank
                    mm = self._data_mem(m) if kind in ('col', 'rooms', 'macro', 'geo', 'dl') else self._script_mem(m)
                    out.append(self.ptr(mm, kind, v, f'lvl {a:08X}'))
                elif i == 0 and cid in (0x17, 0x18):
                    out.append(cid | size << 8)
                elif i == 0 and cid == 0x1F and m.u8(a + 2) in self._amap:
                    out.append(cid | size << 8 | self._amap[m.u8(a + 2)] << 16 | m.u8(a + 3) << 24)
                else:
                    out.append(self.word(m, a + 4 * i, k))
        ctypes.memmove(buf, struct.pack(f'<{len(out)}Q', *out), 8 * len(out))
        return ctypes.addressof(buf)

    def _area_bank(self, m, area):
        try:
            if m.u32(AREA_MAGIC_AT) != AREA_MAGIC:
                return
            r0, r1 = m.u32(AREA_TABLE + 16 * area), m.u32(AREA_TABLE + 16 * area + 4)
        except KeyError:
            return
        m.area_bank = self._rom(r0, r1)

    def _script_mem(self, m):
        return m

    def _data_mem(self, m):
        bank = getattr(m, 'area_bank', None)
        if bank is None:
            return m
        mm = N.Mem(m.rd, m.seg_base, dict(m.segs))
        mm.segs[0x0E] = bank
        return mm

    def convert_geo(self, m, a):
        if self._key(m, 'geo', a) in self.done:
            return self.done[self._key(m, 'geo', a)]
        a0 = a
        out, cmds = [], []
        while True:
            f = N.geo_fields(m, a)
            c = m.u8(a)
            cmds.append((a, f))
            if c in (0x01, 0x03) or (c == 0x02 and m.u8(a + 1) == 0):
                break
            a += 4 * len(f)
        buf = self._reserve(m, 'geo', a0, sum(len(f) for _, f in cmds))
        for a, f in cmds:
            for i, k in enumerate(f):
                if k.startswith('PTR:'):
                    out.append(self.ptr(m, k[4:], m.u32(a + 4 * i), f'geo {a:08X}'))
                else:
                    out.append(self.word(m, a + 4 * i, k))
        ctypes.memmove(buf, struct.pack(f'<{len(out)}Q', *out), 8 * len(out))
        return ctypes.addressof(buf)

    def convert_collision(self, m, a):
        b, o = m.buf(a)
        n = (len(b) - o) // 2
        vals = list(struct.unpack_from(f'>{n}h', b, o))
        if vals[0] != 0x40:
            self.issues.append(f'collision at {a:08X} does not start with TERRAIN_LOAD_VERTICES')
        elif self.bounds4:
            nv = vals[1]
            for i in range(2, 2 + 3 * nv):
                vals[i] >>= 2
        return self._host_buf(struct.pack(f'<{n}h', *vals))

    def level_entry(self, level):
        m = self._mem({})
        for o in range(0, 0x3000, 4):
            a = 0x15000000 + o
            if m.u32(a) == 0x0C0C0200 and m.u32(a + 4) == level:
                t = m.u32(a + 8)
                if m.u8(t) == 0x00:
                    return t, m.u8(t + 3), m.u32(t + 4), m.u32(t + 8), m.u32(t + 12)
        raise KeyError(f'level {level}: no JUMP_IF in segment 0x15')

    def host_execute_word(self, level):
        g = self.g
        h = g.addr('level_main_scripts_entry')
        for i in range(0, 4000):
            w = g.read(h + 8 * i, 'Q')
            if (w & 0xFFFF) == 0x0C0C and g.read(h + 8 * i + 8, 'Q') == level:
                t = g.read(h + 8 * i + 16, 'Q')
                if (g.read(t, 'Q') & 0xFF) == 0x00:
                    return t + 24
        raise KeyError(f'level {level}: host JUMP_IF not found')

    def script_areas(self, level):
        t, seg, r0, r1, entry = self.level_entry(level)
        m = self._mem({seg: self._rom(r0, r1)})
        out, a = [], entry
        for _ in range(4096):
            cid, size = m.u8(a), m.u8(a + 1)
            if cid == 0x1F:
                out.append(m.u8(a + 2))
            if cid in (0x02, 0x07, 0x05) or size == 0:
                break
            a += size
        return out

    def install(self, level):
        t, seg, r0, r1, entry = self.level_entry(level)
        m = self._mem({seg: self._rom(r0, r1)})
        self._amap = self.area_map.get(level, {})
        if self._amap:
            self.done = {k: v for k, v in self.done.items() if k[0] != 'lvl'}
        try:
            host_entry = self.convert_level(m, entry)
        finally:
            self._amap = {}
        try:
            if m.u32(AREA_MAGIC_AT) == AREA_MAGIC:
                self.area_flags[level] = bytes(m.u8(AREA_TABLE + 0x0F + 16 * k) for k in range(16))
        except KeyError:
            pass
        if self.area_map.get(level):
            self.log(f'[rh_level] level {level}: areas renumbered {self.area_map[level]} (as the hack does at run time)')
        w = self.host_execute_word(level)
        self.patched.append((w, self.g.read(w, 'Q')))
        _write_ro(w, struct.pack('<Q', host_entry))
        self.log(f'[rh_level] level {level}: hack script {entry:08X} (ROM {r0:X}-{r1:X}) installed')
        return host_entry

    def uninstall(self):
        for w, v in self.patched:
            _write_ro(w, struct.pack('<Q', v))
        self.patched = []

    def report(self):
        return {'installed': len(self.patched), 'host_bytes': sum(len(b) for b in self.keep), 'issues': self.issues}
