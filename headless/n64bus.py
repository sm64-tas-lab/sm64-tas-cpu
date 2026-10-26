import bisect, ctypes, json, os, struct
from functools import lru_cache
import n64mem as NM

HERE = os.path.dirname(os.path.abspath(__file__))
SYNTH_BASE = 0x80800000

with open(os.path.join(HERE, '_n64map.json'), encoding='utf-8') as _f:
    _M = json.load(_f)
_CONST_PTRS = {int(k): v for k, v in _M['ptrs'].items()}
from sm64hl import _SECS
RDATA = (_SECS['.rdata'][0], _SECS['.rdata'][0] + _SECS['.rdata'][1])


def _prim_kind(t):
    t = NM.strip(t)
    if t == NM.PTRINT_T:
        return ('pi', 4, None)
    d = NM.T[t]
    if d['k'] == 'ptr':
        return ('p', 4, d['t'])
    if d['k'] == 'base':
        return ('f' if d['float'] else ('i' if d['signed'] else 'u'), d['size'], None)
    return (None, 0, None)


@lru_cache(maxsize=200000)
def _resolve(t, off):
    host = 0
    base = 0
    obj = None
    while True:
        t = NM.strip(t)
        if t is None:
            return None
        if t == NM.PTRINT_T:
            return (t, base, host, obj)
        d = NM.T[t]
        k = d['k']
        if k in ('base', 'ptr'):
            return (t, base, host, obj)
        if k == 'array':
            es, ea, eh, _ = NM.layout(d['t'])
            if es == 0:
                return None
            flat = 1
            for x in d['dims']:
                flat *= x
            i = (off - base) // es
            if flat and i >= flat:
                return None
            base += i * es
            host += i * eh
            t = d['t']
            continue
        if k in ('struct', 'union'):
            hit = None
            for mo, mh, mt, mn in NM.members(t):
                ms = NM.layout(mt)[0]
                if base + mo <= off < base + mo + ms:
                    hit = (mo, mh, mt, mn)
                    break
            if hit is None:
                return None
            if d.get('n') == 'Object' and hit[3] == 'rawData':
                obj = (base, host)
            base += hit[0]
            host += hit[1]
            t = hit[2]
            continue
        return None


@lru_cache(maxsize=None)
def _obj_offsets():
    t = NM.find_type('Object')
    raw = [m for m in NM.members(t) if m[3] == 'rawData'][0]
    pd = [m for m in NM.T[t]['m'] if m['n'] == 'ptrData'][0]
    return raw[0], raw[1], pd['off']


_SEG_RE = __import__('re').compile(r'_seg\d|^bhv')


def _is_const_data(name):
    return bool(_SEG_RE.search(name))


class N64Bus:
    def __init__(self, game):
        self.g = game
        self.base = game.base
        self.regions = []
        for name, a in _M['globals'].items():
            info = NM.G.get(name)
            if not info or not info['type']:
                continue
            sz = NM.layout(info['type'])[0]
            if sz:
                self.regions.append((a, sz, self.base + info['rva'], info['type'], name))
        self.regions.sort()
        self.all_regions = self.regions
        self.regions = [r for r in self.regions if not _is_const_data(r[4])]
        self.starts = [r[0] for r in self.regions]
        self.synth = []
        self.synth_by_host = {}
        self.next_synth = SYNTH_BASE
        self.host_lo = self.base
        self.host_hi = self.base + 0x2400000
        self.const_rev = {v: k for k, v in _CONST_PTRS.items()}
        self.host_regions = sorted((r[2], NM.layout(r[3])[2], r) for r in self.all_regions)
        self.host_starts = [x[0] for x in self.host_regions]

    def _region(self, addr):
        if addr >= SYNTH_BASE:
            i = bisect.bisect_right([s[0] for s in self.synth], addr) - 1
            if i >= 0 and self.synth[i][0] <= addr < self.synth[i][0] + max(self.synth[i][1], 1):
                return self.synth[i]
            return None
        i = bisect.bisect_right(self.starts, addr) - 1
        while i >= 0:
            r = self.regions[i]
            if r[0] <= addr < r[0] + r[1]:
                return r[:4]
            if r[0] + 0x40000 < addr:
                break
            i -= 1
        return None

    def host_to_n64(self, hp, pointee=None):
        if hp == 0:
            return 0
        i = bisect.bisect_right(self.host_starts, hp) - 1
        while i >= 0:
            hs, hsz, r = self.host_regions[i]
            if hs <= hp < hs + hsz:
                n = NM.host_to_n64(r[3], hp - hs)
                if n is not None:
                    return r[0] + n
                break
            if hs + 0x40000 < hp:
                break
            i -= 1
        rva = hp - self.base
        if rva in _CONST_PTRS and RDATA[0] <= rva < RDATA[1]:
            return _CONST_PTRS[rva]
        if hp in self.synth_by_host:
            return self.synth_by_host[hp]
        size = NM.layout(pointee)[0] if pointee else 0
        size = size or 0x100
        a = self.next_synth
        self.next_synth += (size + 0xF) & ~0xF
        self.synth.append((a, size, hp, pointee))
        self.synth_by_host[hp] = a
        return a

    def n64_to_host(self, a):
        if a == 0:
            return 0
        r = self._region(a)
        if r is None:
            if a in self.const_rev:
                return self.base + self.const_rev[a]
            raise KeyError(f'unknown N64 address 0x{a:08X}')
        n64s, sz, hbase, t = r
        if t is None:
            return hbase + (a - n64s)
        res = _resolve(t, a - n64s)
        if res is None:
            raise KeyError(f'address 0x{a:08X}: padding')
        pt, pn, ph, obj = res
        return hbase + ph + (a - n64s - pn)

    def _prim_bytes(self, hbase, t, pn, ph, obj):
        kind, size, pointee = _prim_kind(t)
        ha = hbase + ph
        if obj is not None:
            ro, rh, pdh = _obj_offsets()
            slot = (pn - obj[0] - ro) // 4
            hp = struct.unpack('<Q', ctypes.string_at(hbase + obj[1] + pdh + 8 * slot, 8))[0]
            if hp:
                return struct.pack('>I', self.host_to_n64(hp) & 0xFFFFFFFF)
            b = ctypes.string_at(hbase + obj[1] + rh + 4 * slot, 4)
            if slot in NM.OBJ_S16_COMMON:
                return bytes([b[1], b[0], b[3], b[2]])
            return b[::-1]
        if kind == 'p':
            hp = struct.unpack('<Q', ctypes.string_at(ha, 8))[0]
            return struct.pack('>I', self.host_to_n64(hp, pointee) & 0xFFFFFFFF)
        if kind == 'pi':
            v = struct.unpack('<Q', ctypes.string_at(ha, 8))[0]
            if self.host_lo <= v < self.host_hi:
                return struct.pack('>I', self.host_to_n64(v) & 0xFFFFFFFF)
            return struct.pack('>I', v & 0xFFFFFFFF)
        return ctypes.string_at(ha, size)[::-1]

    def read_bytes(self, addr, n):
        out = bytearray()
        a = addr
        while a < addr + n:
            r = self._region(a)
            if r is None:
                raise KeyError(f'unknown N64 address 0x{a:08X} (not in the table; see n64map_build.py)')
            n64s, sz, hbase, t = r
            if t is None:
                out += ctypes.string_at(hbase + a - n64s, 1)
                a += 1
                continue
            res = _resolve(t, a - n64s)
            if res is None:
                out.append(0)
                a += 1
                continue
            pt, pn, ph, obj = res
            if obj is not None:
                ro = _obj_offsets()[0]
                slot_n = obj[0] + ro + ((pn - obj[0] - ro) // 4) * 4
                b = self._prim_bytes(hbase, pt, slot_n, ph - (pn - slot_n), obj)
                pn = slot_n
            else:
                b = self._prim_bytes(hbase, pt, pn, ph, None)
            start = n64s + pn
            take = b[a - start:min(len(b), addr + n - start)]
            out += take
            a += len(take)
        return bytes(out)

    def write_bytes(self, addr, data):
        a = addr
        end = addr + len(data)
        while a < end:
            r = self._region(a)
            if r is None:
                raise KeyError(f'unknown N64 address 0x{a:08X}')
            n64s, sz, hbase, t = r
            res = _resolve(t, a - n64s)
            if res is None:
                a += 1
                continue
            pt, pn, ph, obj = res
            kind, size, pointee = _prim_kind(pt)
            if obj is not None:
                ro, rh, pdh = _obj_offsets()
                slot = (pn - obj[0] - ro) // 4
                slot_n = obj[0] + ro + slot * 4
                cur = bytearray(self._prim_bytes(hbase, pt, slot_n, 0, obj))
                s0 = n64s + slot_n
                for i in range(4):
                    if a <= s0 + i < end:
                        cur[i] = data[s0 + i - addr]
                if slot in NM.OBJ_S16_COMMON:
                    hb = bytes([cur[1], cur[0], cur[3], cur[2]])
                else:
                    hb = bytes(cur[::-1])
                ctypes.memmove(hbase + obj[1] + rh + 4 * slot, hb, 4)
                a = max(a + 1, min(end, s0 + 4))
                continue
            cur = bytearray(self._prim_bytes(hbase, pt, pn, ph, None))
            s0 = n64s + pn
            for i in range(len(cur)):
                if a <= s0 + i < end:
                    cur[i] = data[s0 + i - addr]
            if kind == 'p':
                hp = self.n64_to_host(struct.unpack('>I', bytes(cur))[0])
                ctypes.memmove(hbase + ph, struct.pack('<Q', hp), 8)
            elif kind == 'pi':
                ctypes.memmove(hbase + ph, bytes(cur[::-1]), 4)
            else:
                ctypes.memmove(hbase + ph, bytes(cur[::-1]), len(cur))
            a = max(a + 1, min(end, s0 + len(cur)))

    FMT = {'u8': '>B', 's8': '>b', 'u16': '>H', 's16': '>h', 'u32': '>I', 's32': '>i', 'f32': '>f', 'f64': '>d'}

    def read(self, addr, typ='u32'):
        f = self.FMT[typ]
        return struct.unpack(f, self.read_bytes(addr & 0xFFFFFFFF, struct.calcsize(f)))[0]

    def write(self, addr, value, typ='u32'):
        f = self.FMT[typ]
        if typ in ('f32', 'f64'):
            value = float(value)
        else:
            value = int(value)
            bits = struct.calcsize(f) * 8
            if f[1].isupper():
                value &= (1 << bits) - 1
        self.write_bytes(addr & 0xFFFFFFFF, struct.pack(f, value))
