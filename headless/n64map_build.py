import collections, json, os, struct, sys
import numpy as np
import n64mem as NM
from sm64hl import Game, read_inputs, _SECS

HERE = os.path.dirname(os.path.abspath(__file__))
MOV = os.path.join(os.path.dirname(HERE), 'MOVIE_0STAR')
OUT = os.path.join(HERE, '_n64map.json')
CHAIN = [('01_menus_inputs.txt', '01_controle', 0), ('03_chaine_inputs_depuis_01.txt', '03_avant_pont', 0),
         ('04_lakitu_inputs_depuis_03.txt', '04_apres_lakitu', 0), ('05_porte_chateau_inputs_depuis_04.txt', '05_hall', 0),
         ('06_texte_hall_inputs_depuis_05.txt', '06_hall_libre', 0), ('07_trajet_inputs_depuis_06.txt', '07_pied_escalier_bob', 1)]
HINTS = {'gCurrentArea': 0x8032DDCC, 'gCurrCourseNum': 0x8033BAC6, 'gCurrLevelNum': 0x8032DDF8}
KNOWN = {'gMarioStates': 0x8033B170, 'gObjectPool': 0x8033D488, 'gGlobalTimer': 0x8032D5D4, 'gCurrLevelNum': 0x8032DDF8}


def rdram_be(path):
    import lz4.frame
    d = lz4.frame.decompress(open(path, 'rb').read())
    a = np.frombuffer(d, dtype=np.uint8, count=0x800000, offset=0x1B0).reshape(-1, 4)[:, ::-1].reshape(-1)
    return a.copy()


_flat = {}


def flatten(t):
    key = t
    if key in _flat:
        return _flat[key]
    prims = {1: ([], []), 2: ([], []), 4: ([], []), 8: ([], [])}
    ptrs = []
    objptr = []

    def walk(t, n, h, depth=0):
        t = NM.strip(t)
        if t is None:
            return
        if t == NM.PTRINT_T:
            ptrs.append((n, h, None))
            return
        d = NM.T[t]
        k = d['k']
        if k == 'base':
            s = d['size']
            if s in prims:
                prims[s][0].append(n); prims[s][1].append(h)
            return
        if k == 'ptr':
            ptrs.append((n, h, d['t']))
            return
        if k == 'array':
            es, ea, eh, _ = NM.layout(d['t'])
            cnt = 1
            for x in d['dims']:
                cnt *= x
            et = NM.strip(d['t'])
            if et not in (None, NM.PTRINT_T) and NM.T[et]['k'] == 'base' and NM.T[et]['size'] in prims:
                s = NM.T[et]['size']
                prims[s][0].extend(range(n, n + es * cnt, es)); prims[s][1].extend(range(h, h + eh * cnt, eh))
                return
            for i in range(cnt):
                walk(d['t'], n + i * es, h + i * eh, depth + 1)
            return
        if k in ('struct', 'union'):
            ms = NM.members(t)
            if k == 'union':
                ms = ms[:1]
            for mo, mh, mt, mn in ms:
                walk(mt, n + mo, h + mh, depth + 1)
            if d.get('n') == 'Object':
                raw = [m for m in NM.members(t) if m[3] == 'rawData'][0]
                pd = [m for m in d['m'] if m['n'] == 'ptrData'][0]
                for i in range(0x50):
                    objptr.append((n + raw[0] + 4 * i, h + pd['off'] + 8 * i, h + raw[1] + 4 * i, i))
    walk(t, 0, 0)
    res = ({s: (np.array(a, dtype=np.int64), np.array(b, dtype=np.int64)) for s, (a, b) in prims.items() if a}, ptrs, objptr)
    _flat[key] = res
    return res


def n64_image(t, host, hbase):
    size = NM.layout(t)[0]
    img = np.zeros(size, np.uint8)
    ok = np.zeros(size, bool)
    prims, ptrs, objptr = flatten(t)
    for s, (no, ho) in prims.items():
        idx_h = hbase + ho[:, None] + np.arange(s)[None, :]
        idx_n = no[:, None] + np.arange(s)[None, :]
        inb = (idx_h >= 0).all(axis=1) & (idx_h < len(host)).all(axis=1)   # (a variable past the end of the host image)
        img[idx_n[inb]] = host[idx_h[inb]][:, ::-1]
        ok[idx_n[inb]] = True
    for no, ho, hr, i in objptr:
        if host[hbase + ho:hbase + ho + 8].any():
            ok[no:no + 4] = False
        elif i in NM.OBJ_S16_COMMON:
            b = host[hbase + hr:hbase + hr + 4]
            img[no:no + 4] = [b[1], b[0], b[3], b[2]]
        elif i in NM.OBJ_S16_SPECIFIC:
            ok[no:no + 4] = False
    return img, ok


def main():
    g = Game()
    snaps = []
    dsec, bsec = _SECS['.data'], _SECS['.bss']
    for f, ref, extra in CHAIN:
        g.run(read_inputs(os.path.join(MOV, f)) + [(0, 0, 0)] * extra)
        mem = g.save()[0]
        host = {'.data': np.frombuffer(mem[0], np.uint8), '.bss': np.frombuffer(mem[1], np.uint8)}
        snaps.append((host, rdram_be(os.path.join(MOV, ref + '.st')), g.base))
        print('milestone', ref, file=sys.stderr)

    def host_loc(rva):
        for name, (va, vs) in (('.data', dsec), ('.bss', bsec)):
            if va <= rva < va + vs:
                return name, rva - va
        return None, None

    found, amb, none, skipped = {}, 0, 0, 0
    ambc = {}
    items = sorted(NM.G.items(), key=lambda kv: kv[1]['rva'])
    for idx, (name, info) in enumerate(items):
        sec, hoff = host_loc(info['rva'])
        t = info['type']
        if sec is None or t is None:
            continue
        size = NM.layout(t)[0]
        if size < 2 or size > 0x40000:
            skipped += 1
            continue
        imgs = [n64_image(t, s[0][sec], hoff) for s in snaps]
        best = max(range(len(imgs)), key=lambda i: int((imgs[i][0][imgs[i][1]] != 0).sum()))
        img, ok = imgs[best]
        nz = ok & (img != 0)
        if (size <= 16 and ok.sum() < size) or (size > 16 and (ok.sum() < size // 4 or nz.sum() < 4)):
            none += 1
            continue
        if nz.sum() < 2:
            none += 1
            continue
        w = min(8, size)
        score = np.convolve(nz.astype(int), np.ones(w, int), 'valid')
        valid = np.convolve(ok.astype(int), np.ones(w, int), 'valid') == w
        score[~valid] = -1
        s0 = int(score.argmax())
        if score[s0] < 2:
            none += 1
            continue
        needle = img[s0:s0 + w].tobytes()
        hay = snaps[best][1].tobytes()
        cands = []
        p = hay.find(needle)
        while p != -1 and len(cands) < 200:
            base = p - s0
            if base >= 0 and base + size <= 0x800000 and base % min(NM.layout(t)[1], 8) == 0:
                cands.append(base)
            p = hay.find(needle, p + 1)
        good = []
        for c in cands:
            if all(np.array_equal(im[o], snaps[i][1][c:c + size][o]) for i, (im, o) in enumerate(imgs)):
                good.append(c)
        if len(good) == 1:
            found[name] = 0x80000000 + good[0]
        elif len(good) > 1:
            amb += 1
            ambc[name] = set(good)
        else:
            none += 1
        if idx % 2000 == 0:
            print(idx, len(found), file=sys.stderr)
    for k, v in KNOWN.items():
        print('anchor', k, hex(v), '->', hex(found.get(k, 0)), 'OK' if found.get(k) == v else 'FAIL')
    for k, v in KNOWN.items():
        found[k] = v

    for name, a in HINTS.items():
        info = NM.G[name]
        sec, hoff = host_loc(info['rva'])
        t = info['type']
        size = NM.layout(t)[0]
        okall = True
        for host, rd, hb in snaps:
            img, ok = n64_image(t, host[sec], hoff)
            if not np.array_equal(img[ok], rd[a - 0x80000000:a - 0x80000000 + size][ok]):
                okall = False
        ts = NM.strip(t)
        if okall and ts not in (None, NM.PTRINT_T) and NM.T[ts]['k'] == 'ptr':
            okall = 'pointer'
        print('index', name, hex(a), okall, file=sys.stderr)
        if okall:
            found[name] = a

    import bisect
    ivs = sorted((v['rva'], NM.layout(v['type'])[2], k) for k, v in NM.G.items() if v['type'] and NM.layout(v['type'])[2] > 0)
    starts = [x[0] for x in ivs]

    def owner(rva):
        i = bisect.bisect_right(starts, rva) - 1
        while i >= 0 and ivs[i][0] + ivs[i][1] <= rva and i > 0 and ivs[i - 1][0] + ivs[i - 1][1] > rva:
            i -= 1
        if i >= 0 and ivs[i][0] <= rva < ivs[i][0] + ivs[i][1]:
            return ivs[i]
        return None

    def pairs_from(name, a):
        info = NM.G.get(name)
        sec, hoff = host_loc(info['rva'])
        t = info['type']
        if sec is None or NM.layout(t)[0] > 0x40000:
            return
        _, plist, objptr = flatten(t)
        plist = list(plist) + [(no, ho, None) for no, ho, hr, i in objptr]
        for host, rd, hb in snaps:
            H = host[sec]
            for no, ho, pt in plist:
                hp = int.from_bytes(H[hoff + ho:hoff + ho + 8].tobytes(), 'little')
                o = a - 0x80000000 + no
                nv = int.from_bytes(rd[o:o + 4].tobytes(), 'big')
                if hp and nv and 0 <= hp - hb < 0x2400000:
                    yield hp - hb, nv

    for it in range(8):
        votes = {}
        for name, a in list(found.items()):
            if name not in NM.G:
                continue
            for rva, nv in pairs_from(name, a):
                ow = owner(rva)
                if not ow or ow[2] in found:
                    continue
                n = NM.host_to_n64(NM.G[ow[2]]['type'], rva - ow[0])
                if n is None:
                    continue
                votes.setdefault(ow[2], collections.Counter())[nv - n] += 1
        added = 0
        for name, bases in votes.items():
            b, nb = bases.most_common(1)[0]
            if nb >= 0.75 * sum(bases.values()) and (nb >= 2 or len(bases) == 1):
                if 0x80000000 <= b < 0x80800000:
                    found[name] = b; added += 1
        for name, info in NM.G.items():
            if name in found or info['type'] is None:
                continue
            t = NM.strip(info['type'])
            if t is None or t == NM.PTRINT_T or NM.T[t]['k'] != 'ptr':
                continue
            sec, hoff = host_loc(info['rva'])
            if sec is None:
                continue
            cand = None
            for host, rd, hb in snaps:
                hp = int.from_bytes(host[sec][hoff:hoff + 8].tobytes(), 'little')
                if not hp:
                    continue
                ow = owner(hp - hb)
                if not ow or ow[2] not in found:
                    cand = set() if cand is None else cand
                    continue
                n = NM.host_to_n64(NM.G[ow[2]]['type'], hp - hb - ow[0])
                if n is None:
                    continue
                want = (found[ow[2]] + n).to_bytes(4, 'big')
                hay = rd.tobytes(); pos = set(); p = hay.find(want)
                while p != -1:
                    if p % 4 == 0:
                        pos.add(p)
                    p = hay.find(want, p + 1)
                cand = pos if cand is None else cand & pos
            if cand and len(cand) == 1:
                found[name] = 0x80000000 + cand.pop(); added += 1
            elif cand and len(cand) < 64:
                ambc[name] = cand
        placed = sorted((NM.G[k]['rva'], v) for k, v in found.items() if k in NM.G)
        prv = [x[0] for x in placed]
        for name, cs in list(ambc.items()):
            if name in found:
                ambc.pop(name); continue
            r = NM.G[name]['rva']
            i = bisect.bisect_left(prv, r)
            near = [placed[j][1] - 0x80000000 for j in (i - 1, i) if 0 <= j < len(placed) and abs(placed[j][0] - r) < 0x800]
            if not near:
                continue
            ok = [c for c in cs if any(abs(c - n) < 0x400 for n in near)]
            if len(ok) == 1:
                found[name] = 0x80000000 + ok[0]; added += 1; ambc.pop(name)
        print('pointer pass', it, '+', added, file=sys.stderr)
        if not added:
            break

    ptrs = {}
    conflicts = set()
    for name, a in found.items():
        info = NM.G.get(name)
        if not info:
            continue
        sec, hoff = host_loc(info['rva'])
        t = info['type']
        if sec is None or NM.layout(t)[0] > 0x40000:
            continue
        _, plist, _ = flatten(t)
        for host, rd, hb in snaps:
            H = host[sec]
            for no, ho, pt in plist:
                hp = int.from_bytes(H[hoff + ho:hoff + ho + 8].tobytes(), 'little')
                o = a - 0x80000000 + no
                nv = int.from_bytes(rd[o:o + 4].tobytes(), 'big')
                if hp == 0 or nv == 0:
                    continue
                rva = hp - hb
                if not (0 <= rva < 0x2400000):
                    continue
                if rva in ptrs and ptrs[rva] != nv:
                    conflicts.add(rva)
                ptrs[rva] = nv
    for r in conflicts:
        ptrs.pop(r, None)
    json.dump({'globals': found, 'ptrs': {str(k): v for k, v in ptrs.items()}}, open(OUT, 'w'))
    print(f'{len(found)} globals placed, {amb} ambiguous, {none} without signature, {skipped} skipped ; '
          f'{len(ptrs)} pointers learned ({len(conflicts)} conflicts dropped)')


if __name__ == '__main__':
    main()
