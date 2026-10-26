import bisect
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import n64mem as NM

SEG_BHV = 0x13


def _vars():
    return json.load(open(os.path.join(HERE, '_vars_cache.json' if os.environ.get('SM64_VERSION', 'us') == 'us' else '_vars_cache_%s.json' % os.environ['SM64_VERSION'])))['vars']


BHV_CMD_WORDS = {0x00: 1, 0x01: 1, 0x02: 2, 0x03: 1, 0x04: 2, 0x05: 1, 0x06: 1, 0x07: 1, 0x08: 1, 0x09: 1, 0x0A: 1,
                 0x0B: 1, 0x0C: 2, 0x0D: 1, 0x0E: 1, 0x0F: 1, 0x10: 1, 0x11: 1, 0x12: 1, 0x13: 2, 0x14: 2, 0x15: 2,
                 0x16: 2, 0x17: 2, 0x18: 1, 0x19: 1, 0x1A: 1, 0x1B: 1, 0x1C: 3, 0x1D: 1, 0x1E: 1, 0x1F: 1, 0x20: 1,
                 0x21: 1, 0x22: 1, 0x23: 2, 0x24: 1, 0x25: 1, 0x26: 1, 0x27: 2, 0x28: 1, 0x29: 3, 0x2A: 2, 0x2B: 3,
                 0x2C: 3, 0x2D: 1, 0x2E: 2, 0x2F: 2, 0x30: 5, 0x31: 2, 0x32: 1, 0x33: 2, 0x34: 1, 0x35: 1, 0x36: 2,
                 0x37: 2}
BHV_PTR_SLOTS = {0x02: (1,), 0x04: (1,), 0x0C: (1,), 0x1C: (2,), 0x27: (1,), 0x29: (2,), 0x2A: (1,), 0x2C: (2,),
                 0x37: (1,)}
BHV_END = (0x09, 0x0A, 0x0B, 0x03, 0x04, 0x1D)


def _bhv_cmds(words):
    out, k = [], 0
    while k < len(words):
        c = words[k] >> 24
        sz = BHV_CMD_WORDS.get(c)
        if sz is None:
            return None
        out.append(c)
        k += sz
    return tuple(out) if k == len(words) else None


def _poke_q(g, a, v):
    import ctypes
    old = ctypes.string_at(a, 8)
    k32 = ctypes.windll.kernel32
    prot = ctypes.c_uint32()
    k32.VirtualProtect(ctypes.c_void_p(a), 8, 0x04, ctypes.byref(prot))
    g.__dict__.setdefault('_bhv_value_edits', []).append((a, old))
    ctypes.memmove(a, struct.pack('<Q', v), 8)
    k32.VirtualProtect(ctypes.c_void_p(a), 8, prot.value, ctypes.byref(prot))


def _unpoke(g):
    import ctypes
    k32 = ctypes.windll.kernel32
    for a, old in reversed(g.__dict__.pop('_bhv_value_edits', [])):
        prot = ctypes.c_uint32()
        k32.VirtualProtect(ctypes.c_void_p(a), 8, 0x04, ctypes.byref(prot))
        ctypes.memmove(a, old, 8)
        k32.VirtualProtect(ctypes.c_void_p(a), 8, prot.value, ctypes.byref(prot))


class BhvMap:

    @staticmethod
    def _find_script(st, img, fits):
        k = next((i for i, w in enumerate(img) if w is not None), None)
        if k is None:
            return None
        import numpy as np
        words = np.frombuffer(st.rd[:len(st.rd) // 4 * 4], dtype='>u4')
        for idx in np.flatnonzero(words == img[k]):
            a = 0x80000000 + 4 * (int(idx) - k)
            if fits(a, img):
                return a
        return None

    def __init__(self, g, st, anchor='bhvMario', romhack=False):
        import ctypes
        _unpoke(g)
        self.g, self.st = g, st
        self.missing = []
        vs = [v for v in _vars() if v['cu'].replace('\\', '/').endswith('data/behavior_data.c') and not v['fn']
              and v['type'] and NM.layout(v['type'])[0]]
        vs.sort(key=lambda v: v['line'])
        lo, hi = g.base, g.base + 0x2400000
        imgs = []
        for v in vs:
            n = NM.layout(v['type'])[0] // 4
            words = struct.unpack(f'<{n}Q', ctypes.string_at(g.base + v['rva'], 8 * n))
            imgs.append([None if lo <= w < hi else w & 0xFFFFFFFF for w in words])

        def fits(a, img):
            o = a - 0x80000000
            if o < 0 or o + 4 * len(img) > len(st.rd):
                return False
            return all(w is None or w == struct.unpack_from('>I', st.rd, o + 4 * i)[0] for i, w in enumerate(img))
        if romhack:
            self._locate_romhack(vs, imgs)
            return
        ia = next(i for i, v in enumerate(vs) if v['n'] == anchor)
        a_anchor = json.load(open(os.path.join(HERE, '_n64map.json')))['globals'][anchor]
        if not fits(a_anchor, imgs[ia]):
            # Another version (JP): the anchor script is elsewhere; find it by its words in the RAM image.
            a_anchor = self._find_script(st, imgs[ia], fits)
            if a_anchor is None:
                raise RuntimeError(f'{anchor} not found in this .st')
        pos = {ia: a_anchor}
        for i in range(ia + 1, len(vs)):
            pred = pos[i - 1] + 4 * len(imgs[i - 1])
            pos[i] = next((pred + d for d in sorted(range(-64, 65, 4), key=abs) if fits(pred + d, imgs[i])), None)
            if pos[i] is None:
                raise RuntimeError(f'{vs[i]["n"]}: not found near {pred:08X}')
        for i in range(ia - 1, -1, -1):
            pred = pos[i + 1] - 4 * len(imgs[i])
            pos[i] = next((pred + d for d in sorted(range(-64, 65, 4), key=abs) if fits(pred + d, imgs[i])), None)
            if pos[i] is None:
                raise RuntimeError(f'{vs[i]["n"]}: not found near {pred:08X}')
        self.base = pos[0]
        self.items = sorted((pos[i], 4 * len(imgs[i]), v['n'], g.base + v['rva'], 8) for i, v in enumerate(vs))
        self.shifted = [v['n'] for i, v in enumerate(vs) if i and pos[i] != pos[i - 1] + 4 * len(imgs[i - 1])]
        self.starts = [x[0] for x in self.items]
        self.by_name = {x[2]: x for x in self.items}

    def _locate_romhack(self, vs, imgs, span=0x40000):
        import numpy as np
        st = self.st
        seg = 0x80000000 | st.get('I', st.all['sSegmentTable'] + 4 * SEG_BHV)
        self.base, self.items, self.starts = seg, [], []
        o0 = seg - 0x80000000
        W = np.frombuffer(st.rd, '>u4', count=min(span, len(st.rd) - o0) // 4, offset=o0).astype(np.uint32)
        cands = []
        for img in imgs:
            c = np.arange(max(0, len(W) - len(img) + 1))
            for i, w in enumerate(img):
                if w is not None:
                    c = c[W[c + i] == w]
                    if not len(c):
                        break
            cands.append([seg + 4 * int(x) for x in c])
        pos = [c[0] if len(c) == 1 else None for c in cands]
        try:
            van0 = json.load(open(os.path.join(HERE, '_bhv_us_addrs.json')))['scripts']
        except (OSError, ValueError, KeyError):
            van0 = {}
        dl0 = {}
        for i, v in enumerate(vs):
            if pos[i] is not None and v['n'] in van0:
                dl0[pos[i] - van0[v['n']]] = dl0.get(pos[i] - van0[v['n']], 0) + 1
        if dl0:
            d0 = max(dl0.items(), key=lambda kv: kv[1])[0]
            for i, (c, v) in enumerate(zip(cands, vs)):
                if pos[i] is None and len(c) > 1 and v['n'] in van0 and van0[v['n']] + d0 in c:
                    pos[i] = van0[v['n']] + d0
        for i, c in enumerate(cands):
            if pos[i] is not None or not c:
                continue
            lo = max((pos[j] + 4 * len(imgs[j]) for j in range(i) if pos[j] is not None), default=seg)
            hi = next((pos[j] for j in range(i + 1, len(pos)) if pos[j] is not None and len(cands[j]) == 1), 1 << 32)
            ok = [a for a in c if lo <= a < hi] or [a for a in c if a >= lo] or c
            pos[i] = ok[0]
        if dl0:
            at = {}
            for i in range(len(vs)):
                if pos[i] is not None:
                    at.setdefault(pos[i], []).append(i)
            for a, ii in at.items():
                if len(ii) < 2:
                    continue
                own = [i for i in ii if vs[i]['n'] in van0 and van0[vs[i]['n']] + d0 == a]
                for i in ii:
                    if own and i not in own:
                        pos[i] = None
        self.edited = []
        host_of = {}
        try:
            van = json.load(open(os.path.join(HERE, '_bhv_us_addrs.json')))['scripts']
            deltas = {}
            for i, v in enumerate(vs):
                if pos[i] is not None and v['n'] in van:
                    d = pos[i] - van[v['n']]
                    deltas[d] = deltas.get(d, 0) + 1
            if deltas:
                delta, cnt = max(deltas.items(), key=lambda kv: kv[1])
                taken = sorted((pos[j], pos[j] + 4 * len(imgs[j])) for j in range(len(vs))
                               if pos[j] is not None and vs[j]['n'] in van and pos[j] - van[vs[j]['n']] == delta)
                for i, v in enumerate(vs):
                    if pos[i] is not None or v['n'] not in van or imgs[i][0] is None:
                        continue
                    a = van[v['n']] + delta
                    o = a - 0x80000000
                    if not (0 <= o and o + 4 <= len(st.rd)):
                        continue
                    if struct.unpack_from('>I', st.rd, o)[0] != imgs[i][0]:
                        continue
                    if any(lo_ <= a < hi_ for lo_, hi_ in taken):
                        continue
                    pos[i] = a
                    self.edited.append(v['n'])
                    n = len(imgs[i])
                    if o + 4 * n <= len(st.rd):
                        hw = struct.unpack_from(f'>{n}I', st.rd, o)
                        if _bhv_cmds(hw) is not None and _bhv_cmds(hw) == _bhv_cmds(
                                [w if w is not None else 0 for w in imgs[i]]):
                            ha = self.g.base + v['rva']
                            for k in range(n):
                                if imgs[i][k] is not None and hw[k] != imgs[i][k]:
                                    _poke_q(self.g, ha + 8 * k, hw[k])
                            host_of[i] = ha
                            continue
                    tr = self._translate(a, i, vs, imgs, pos)
                    if tr:
                        host_of[i] = tr
        except (OSError, ValueError, KeyError):
            pass
        self.missing = [v['n'] for i, v in enumerate(vs) if pos[i] is None]
        placed = sorted((pos[j], pos[j] + 4 * len(imgs[j])) for j in range(len(vs)) if pos[j] is not None)
        self.alias = {}
        for i, c in enumerate(cands):
            if pos[i] is None or len(c) < 2:
                continue
            for a in c:
                if a != pos[i] and not any(lo_ <= a < hi_ for lo_, hi_ in placed) and a not in self.alias:
                    self.alias[a] = (vs[i]['n'], self.g.base + vs[i]['rva'])
        self.base = seg
        self.start_host = {}
        items = []
        for i, v in enumerate(vs):
            if pos[i] is None:
                continue
            if isinstance(host_of.get(i), tuple):
                buf, nw = host_of[i]
                items.append((pos[i], 4 * nw, v['n'], buf, 8))
                self.start_host[pos[i]] = self.g.base + v['rva']
            else:
                items.append((pos[i], 4 * len(imgs[i]), v['n'], self.g.base + v['rva'], 8))
        self.items = sorted(items)
        self.translated = [vs[i]['n'] for i in host_of if isinstance(host_of[i], tuple)]
        self._vs, self._imgs, self._pos = vs, imgs, pos
        try:
            self._van = json.load(open(os.path.join(HERE, '_bhv_us_addrs.json')))['scripts']
        except (OSError, ValueError, KeyError):
            self._van = {}
        self._delta = d0 if dl0 else 0
        self.edited_values = [vs[i]['n'] for i in host_of if not isinstance(host_of[i], tuple)]
        self.shifted = []
        self.starts = [x[0] for x in self.items]
        self.by_name = {x[2]: x for x in self.items}

    def check(self, names=None):
        bad = []
        lo, hi = self.g.base, self.g.base + 0x2400000
        for a, n, name, h, _ in self.items:
            if names and name not in names:
                continue
            for i in range(n // 4):
                hv = struct.unpack('<Q', __import__('ctypes').string_at(h + 8 * i, 8))[0]
                if lo <= hv < hi:
                    continue
                if (hv & 0xFFFFFFFF) != self.st.get('I', a + 4 * i):
                    bad.append((name, i))
                    break
        return bad

    def to_host(self, a):
        i = bisect.bisect_right(self.starts, a) - 1
        if i < 0 or not (self.items[i][0] <= a < self.items[i][0] + max(self.items[i][1], 4)):
            al = getattr(self, 'alias', {}).get(a)
            return al[1] if al else None
        s, n, name, h, es = self.items[i]
        if (a - s) % 4:
            return None
        if a == s and a in getattr(self, 'start_host', {}):
            return self.start_host[a]
        return h + (a - s) // 4 * es

    def translate_at(self, a, data_ptr=None):
        if not hasattr(self, '_vs') or a in self.__dict__.setdefault('_translating', set()):
            return None
        self._translating.add(a)
        try:
            return self._translate_at(a, data_ptr)
        finally:
            self._translating.discard(a)

    def _translate_at(self, a, data_ptr):
        slot = next((j for j, v in enumerate(self._vs) if self._pos[j] is None and v['n'] in self._van
                     and self._van[v['n']] + self._delta == a), None)
        tr = self._translate(a, slot, self._vs, self._imgs, self._pos, goto=False, data_ptr=data_ptr)
        if not tr:
            return None
        buf, nw = tr
        bisect.insort(self.items, (a, 4 * nw, f'hack_{a:08X}', buf, 8))
        self.starts = [x[0] for x in self.items]
        self.translated.append(f'hack_{a:08X}')
        return buf

    def _translate(self, a, i, vs, imgs, pos, goto=True, data_ptr=None):
        import ctypes
        st, g = self.st, self.g
        rd = st.rd
        word = lambda x: struct.unpack_from('>I', rd, x - 0x80000000)[0]
        if not hasattr(self, '_ptr_pairs'):
            pp = {}
            for j, v in enumerate(vs):
                if pos[j] is None or v['n'] in self.edited:
                    continue
                hw = struct.unpack(f'<{len(imgs[j])}Q', ctypes.string_at(g.base + v['rva'], 8 * len(imgs[j])))
                for k, w in enumerate(imgs[j]):
                    if w is None:
                        pp.setdefault(word(pos[j] + 4 * k), hw[k])
            self._ptr_pairs = pp
        van_ptr, k, occ = {}, 0, {}
        n0 = 0
        if i is not None:
            v = vs[i]
            n0 = len(imgs[i])
            van_h = struct.unpack(f'<{n0}Q', ctypes.string_at(g.base + v['rva'], 8 * n0))
        while k < n0:
            c = (imgs[i][k] >> 24) if imgs[i][k] is not None else None
            sz = BHV_CMD_WORDS.get(c)
            if sz is None:
                return None
            occ[c] = occ.get(c, 0) + 1
            for s_ in BHV_PTR_SLOTS.get(c, ()):
                van_ptr[(c, occ[c], s_)] = van_h[k + s_]
            k += sz
        out, k, occ = [], 0, {}
        while True:
            w = word(a + 4 * k)
            c = w >> 24
            sz = BHV_CMD_WORDS.get(c)
            if sz is None or k + sz > 96:
                return None
            occ[c] = occ.get(c, 0) + 1
            for s_ in range(sz):
                ws = word(a + 4 * (k + s_))
                if s_ in BHV_PTR_SLOTS.get(c, ()):
                    h = van_ptr.get((c, occ[c], s_))
                    if h is None:
                        h = self._ptr_pairs.get(ws)
                    if h is None and c in (0x02, 0x04, 0x1C, 0x29, 0x2C):
                        ra = ws if ws >= 0x80000000 else (self.base + (ws & 0xFFFFFF) if ws >> 24 == SEG_BHV else None)
                        if ra is not None:
                            h = self.to_host(ra)
                            if h is None and ra != a:
                                h = self.translate_at(ra, data_ptr)
                    if h is None and data_ptr is not None:
                        h = data_ptr(c, ws)
                    if h is None:
                        return None
                    out.append(h)
                else:
                    out.append(ws)
            k += sz
            if c in BHV_END:
                break
        buf = (ctypes.c_uint64 * len(out))(*out)
        g.__dict__.setdefault('_bhv_bufs', []).append(buf)
        if i is not None and goto:
            start = g.base + vs[i]['rva']
            _poke_q(g, start, 0x04000000)
            _poke_q(g, start + 8, ctypes.addressof(buf))
        return (ctypes.addressof(buf), len(out))

    def name(self, a):
        i = bisect.bisect_right(self.starts, a) - 1
        if i >= 0 and self.items[i][0] <= a < self.items[i][0] + self.items[i][1]:
            return self.items[i][2], a - self.items[i][0]
        return None, None


class ConstMap:

    def __init__(self, g, st, seg_table_addr):
        import re
        import numpy as np
        import n64map_build as B
        from sm64hl import _SECS
        self.g, self.st, self.B, self.np = g, st, B, np
        self.seg_base = {}
        for s in range(32):
            b = st.get('I', seg_table_addr + 4 * s)
            if b:
                self.seg_base[s] = 0x80000000 | b
        rx = re.compile(r'_([0-9A-Fa-f]{8})$')
        self.by_seg = {}
        sections = [(_SECS[k][0], _SECS[k][0] + _SECS[k][1], k) for k in ('.data', '.rdata') if k in _SECS]
        for v in _vars():
            m = rx.search(v['n'])
            if not m or v['fn'] or not v['type']:
                continue
            a = int(m.group(1), 16)
            size = NM.layout(v['type'])[0]
            sec = next((k for lo, hi, k in sections if lo <= v['rva'] < hi), None)
            if not size or sec is None or not 1 <= a >> 24 <= 0x1F:
                continue
            self.by_seg.setdefault(a >> 24, []).append((a & 0xFFFFFF, size, v))
        for s in self.by_seg:
            self.by_seg[s].sort(key=lambda x: x[0])
        rs = re.compile(r'_seg([0-9a-fA-F]{1,2})_')
        self.unaddr = {}
        for v in _vars():
            if rx.search(v['n']) or v['fn'] or not v['type']:
                continue
            m = rs.search(v['n'])
            size = NM.layout(v['type'])[0] if m else 0
            sec = next((k for lo, hi, k in sections if lo <= v['rva'] < hi), None)
            if m and size and sec is not None:
                self.unaddr.setdefault(int(m.group(1), 16), []).append((size, v))
        self._located = {}
        self._host = {'.data': (_SECS['.data'][0], np.frombuffer(ctypes_string(g.base + _SECS['.data'][0], _SECS['.data'][1]), np.uint8)),
                      '.rdata': (_SECS['.rdata'][0], np.frombuffer(ctypes_string(g.base + _SECS['.rdata'][0], _SECS['.rdata'][1]), np.uint8))}
        self._ok = {}

    def _matches(self, v, n64_addr):
        key = (v['n'], v['cu'], n64_addr)
        if key not in self._ok:
            sec = '.data' if v['rva'] < self._host['.rdata'][0] else '.rdata'
            va, arr = self._host[sec]
            img, ok = self.B.n64_image(v['type'], arr, v['rva'] - va)
            o = n64_addr - 0x80000000
            ram = self.np.frombuffer(self.st.rd, self.np.uint8)[o:o + len(img)]
            _p, plist, _o = self.B.flatten(v['type'])
            hoff = v['rva'] - va
            ptr_ok = [bool(arr[hoff + ho:hoff + ho + 8].any()) == bool(ram[no:no + 4].any())
                      for no, ho, _t in plist if no + 4 <= len(ram)]
            checkable = int(ok.sum()) + 4 * len(ptr_ok)
            self._ok[key] = (len(ram) == len(img) and checkable > 0 and bool((img[ok] == ram[ok]).all())
                             and all(ptr_ok))
        return self._ok[key]

    def _locate(self, s, var, size, span=0x200000):
        key = (s, var['n'], var['rva'])
        if key not in self._located:
            o = None
            sec = '.data' if var['rva'] < self._host['.rdata'][0] else '.rdata'
            va, arr = self._host[sec]
            img, ok = self.B.n64_image(var['type'], arr, var['rva'] - va)
            if len(img) == size and bool(ok.all()) and int((img != 0).sum()) >= 8:
                base = self.seg_base[s] - 0x80000000
                hay = bytes(self.st.rd[base:base + span])
                needle = img.tobytes()
                i = hay.find(needle)
                if i >= 0 and hay.find(needle, i + 1) < 0:
                    o = i
            self._located[key] = o
        return self._located[key]

    def seg_off(self, v):
        if 0x01000000 <= v < 0x20000000:
            return v >> 24, v & 0xFFFFFF
        if 0x80000000 <= v < 0x80800000:
            best = None
            for s, b in self.seg_base.items():
                if b <= v and s in self.by_seg and (best is None or v - b < best[1]):
                    best = (s, v - b)
            return best
        return None

    def to_host(self, v, shift=True):
        if 0x01000000 <= v < 0x20000000:
            cands = [(v >> 24, v & 0xFFFFFF)] if v >> 24 in self.seg_base else []
        elif 0x80000000 <= v < 0x80800000:
            cands = sorted(((s, v - b) for s, b in self.seg_base.items() if b <= v and s in self.by_seg),
                           key=lambda x: x[1])
        else:
            return None
        import n64bus as NB
        for s, off in cands:
            base = self.seg_base[s]
            for o, size, var in self.by_seg.get(s, ()):
                if o <= off < o + size and self._matches(var, base + o):
                    if off == o:
                        return self.g.base + var['rva']
                    res = NB._resolve(var['type'], off - o)
                    return None if res is None else self.g.base + var['rva'] + res[2] + (off - o - res[1])
        for s, off in cands:
            for size, var in self.unaddr.get(s, ()):
                o = self._locate(s, var, size)
                if o is not None and o <= off < o + size:
                    if off == o:
                        return self.g.base + var['rva']
                    res = NB._resolve(var['type'], off - o)
                    return None if res is None else self.g.base + var['rva'] + res[2] + (off - o - res[1])
        if not shift:
            return None
        hits = set()
        for s, off in cands[:3]:
            base = self.seg_base[s]
            for o, size, var in self.by_seg.get(s, ()):
                if abs(o - off) <= self.SHIFT and self._matches(var, base + off) and self._strong(var, base + off):
                    hits.add(var['rva'])
        return self.g.base + hits.pop() if len(hits) == 1 else None

    def _strong(self, v, at=None):
        sec = '.data' if v['rva'] < self._host['.rdata'][0] else '.rdata'
        va, arr = self._host[sec]
        img, ok = self.B.n64_image(v['type'], arr, v['rva'] - va)
        if int((ok & (img != 0)).sum()) >= 8:
            return True
        if at is None:
            return False
        _p, plist, _o = self.B.flatten(v['type'])
        hoff, good = v['rva'] - va, 0
        for no, ho, pt in plist:
            hp = int.from_bytes(arr[hoff + ho:hoff + ho + 8].tobytes(), 'little')
            nv = self.st.get('I', at + no)
            if not hp or not nv:
                if bool(hp) != bool(nv):
                    return False
                continue
            if pt is None or not self.g.base <= hp < self.g.base + 0x2400000:
                return False
            target = nv if nv >= 0x80000000 else self.seg_base.get(nv >> 24, 0) + (nv & 0xFFFFFF)
            rva = hp - self.g.base
            sec2 = '.data' if rva < self._host['.rdata'][0] else '.rdata'
            va2, arr2 = self._host[sec2]
            if not va2 <= rva < va2 + len(arr2):
                return False
            o = target - 0x80000000
            rd = self.np.frombuffer(self.st.rd, self.np.uint8)
            spt = NM.strip(pt)
            if spt not in (None, NM.PTRINT_T) and NM.T[spt]['k'] == 'base' and NM.T[spt]['size'] in (1, 2, 4):
                es = NM.T[spt]['size']
                h = arr2[rva - va2:rva - va2 + 32].reshape(-1, es)[:, ::-1].reshape(-1)
                ram = rd[o:o + 32]
                if len(ram) != 32 or int((h != 0).sum()) < 4 or not (h == ram).all():
                    return False
            else:
                img2, ok2 = self.B.n64_image(pt, arr2, rva - va2)
                ram = rd[o:o + len(img2)]
                if len(ram) != len(img2) or int((ok2 & (img2 != 0)).sum()) < 4 or not (img2[ok2] == ram[ok2]).all():
                    return False
            good += 1
        return good > 0

    SHIFT = 0x800


def ctypes_string(a, n):
    import ctypes
    return ctypes.string_at(a, n)
