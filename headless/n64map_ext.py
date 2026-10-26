import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import n64mem as NM
import n64map_build as B
import static_map as SM
import st_bridge as SB
from sm64hl import Game, _SECS, read_m64

OUT = os.path.join(HERE, '_n64map_ext.json')
VARS = os.path.join(HERE, '_vars_cache.json')
BASE_MAP = os.path.join(HERE, '_n64map.json')
SRC_DIRS = ('src/game', 'src/engine')
SNAP_NAMES = ('01_controle', '02_depart_lj', '03_avant_pont', '04_apres_lakitu', '05_hall', '06_hall_libre',
              '07_pied_escalier_bob', '10_palier', '11_bitdw_arrivee', '12_bitdw_controle')


def _vars_by_cu():
    out = {}
    for v in json.load(open(VARS))['vars']:
        cu = v['cu'].replace('\\', '/')
        out.setdefault(cu, {})[f"{v['fn']}:{v['n']}" if v['fn'] else v['n']] = v
    return out


_INC_RE = __import__('re').compile(r'^[ \t]*#[ \t]*include[ \t]+"([^"]+\.c)"[ \t]*$', __import__('re').M)


def _expand(path, depth=0):
    txt = open(path, encoding='utf-8', errors='replace').read()
    if depth > 4:
        return txt

    def sub(m):
        for d in (os.path.dirname(path), os.path.join(SM.DECOMP, 'src', 'game'), SM.DECOMP):
            p = os.path.join(d, m.group(1))
            if os.path.exists(p):
                return _expand(p, depth + 1)
        return ''
    return _INC_RE.sub(sub, txt)


def file_decls(path):
    import re
    text = SM.preprocess(_expand(path))
    decls, stmt, depth, body, fname = [], '', 0, '', None
    for ch in text:
        if depth == 0:
            if ch == ';':
                decls.append(('decl', stmt.strip()))
                stmt = ''
                continue
            if ch == '{':
                if '=' in stmt:
                    stmt += ' {INIT} '
                    depth, body, fname = 1, None, None
                else:
                    m = re.search(r'(\w+)\s*\([^()]*\)\s*$', stmt)
                    fname = m.group(1) if m else None
                    stmt, body, depth = '', '', 1
                continue
            stmt += ch
        else:
            if body is not None:
                body += ch
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and body is not None:
                    if fname:
                        decls.append(('func', fname, body))
                    body = None
    order, defined, inited, def_order = [], set(), set(), []

    def take(s, prefix=''):
        s = ' '.join(s.split())
        if not s or s.startswith('typedef'):
            return
        ext = s.startswith('extern')
        lhs, init = (s.split('=', 1) + [None])[:2]
        if '(' in lhs and not re.search(r'\(\s*\*\s*\w+\s*\)', lhs):
            return
        for part in lhs.split(','):
            m = re.search(r'(\w+)\s*(\[[^\]]*\]\s*)*\)?\s*(\[[^\]]*\]\s*)*$', part.strip())
            if not m:
                continue
            n = prefix + m.group(1)
            if n not in order:
                order.append(n)
            if not ext:
                if n not in defined:
                    def_order.append(n)
                defined.add(n)
                if init is not None:
                    inited.add(n)
    for d in decls:
        if d[0] == 'decl':
            take(d[1])
        else:
            for m in re.finditer(r'\bstatic\s+([^;{}()]+?(?:=[^;]*)?);', d[2]):
                take(m.group(1), d[1] + ':')
    return ([(n, False) for n in order if n in defined and n not in inited] +
            [(n, True) for n in def_order if n in inited])


def link_order(section):
    import re
    ld = SM.preprocess(open(os.path.join(SM.DECOMP, 'sm64.ld')).read())
    segs, cur, in_seg = [], None, None
    for line in ld.split('\n'):
        s = line.strip()
        m = re.match(r'BEGIN_(SEG|NOLOAD)\((main|engine)\b', s)
        if m:
            in_seg = (m.group(1), m.group(2))
            cur = []
            continue
        if re.match(r'END_(SEG|NOLOAD)\(', s):
            if in_seg and cur:
                segs.append((in_seg, cur))
            in_seg, cur = None, None
            continue
        if in_seg is None:
            continue
        want = '.bss' if section == '.bss' else '.data'
        m = re.match(r'BUILD_DIR/(src/(?:game|engine))(/\w+\.o|\*\.o)\((\.\w+)\*?\)', s)
        if m and m.group(3) == want:
            if m.group(2) == '*.o':
                done = {c for c in cur}
                rest = sorted(os.path.relpath(p, SM.DECOMP).replace('\\', '/')
                              for p in glob.glob(os.path.join(SM.DECOMP, m.group(1), '*.c')))
                cur.extend(r for r in rest if r not in done and not any(r in c for _, c in segs))
            else:
                cur.append(m.group(1) + m.group(2)[:-2] + '.c')
    kind = 'NOLOAD' if section == '.bss' else 'SEG'
    return [c for (k, name), c in segs if k == kind]


def _sec_of(rva):
    for name in ('.data', '.bss'):
        va, vs = _SECS[name]
        if va <= rva < va + vs:
            return name, rva - va
    return None, None


class Snapshots:

    def __init__(self, names=SNAP_NAMES, log=print):
        ix = SB._load_index()
        by_name = {os.path.splitext(os.path.basename(e['path']))[0]: e for e in ix.values()}
        g = Game()
        self.snaps = []
        mov = os.path.join(os.path.dirname(HERE), 'MOVIE_0STAR')
        for n in names:
            e = by_name.get(n)
            st = os.path.join(mov, n + '.st')
            if e is None or not os.path.exists(st):
                log(f'  (snapshot {n} absent)')
                continue
            g.reset()
            g.run(e['inputs'])
            rd = np.frombuffer(SB.rdram(st), np.uint8)
            if not SB.quick_equal(g, rd.tobytes()):
                log(f'  (snapshot {n}: replay differs from the .st, skipped)')
                continue
            mem = g.save()[0]
            self.snaps.append(({'.data': np.frombuffer(mem[0], np.uint8), '.bss': np.frombuffer(mem[1], np.uint8)}, rd))
        for d in filter(None, os.environ.get('SM64_SNAPS', '').split(';')):
            man = json.load(open(os.path.join(d, 'manifest.json')))
            mv = [tuple(f) for f in read_m64(os.path.join(HERE, man['movie']))]
            g.reset()
            done = 0
            for n in sorted(man['frames']):
                g.run(mv[done:n])
                done = n
                rd = np.frombuffer(SB.rdram(os.path.join(d, f'f{n:05d}.st')), np.uint8)
                if not SB.quick_equal(g, rd.tobytes()):
                    log(f'  (snapshot {d} f{n}: replay differs, skipped)')
                    continue
                mem = g.save()[0]
                self.snaps.append(({'.data': np.frombuffer(mem[0], np.uint8), '.bss': np.frombuffer(mem[1], np.uint8)}, rd))
        log(f'{len(self.snaps)} snapshots')

    def images(self, var):
        sec, off = _sec_of(var['rva'])
        _prims, plist, _objptr = B.flatten(var['type'])
        out = _Imgs()
        for host, rd in self.snaps:
            img, ok = B.n64_image(var['type'], host[sec], off)
            ptrs = [(no, bool(host[sec][off + ho:off + ho + 8].any())) for no, ho, _pt in plist]
            out.append((img, ok, ptrs))
        out.compat = compat_kinds(var['type'])
        return out


class _Imgs(list):
    compat = None


REF_W = 16


def compat_kinds(t):
    st = NM.strip(t)
    if st not in (None, NM.PTRINT_T) and NM.T[st]['k'] in ('base', 'ptr'):
        sz = NM.layout(t)[0]
        return {1: {'lb', 'lbu', 'sb'}, 2: {'lh', 'lhu', 'sh'}, 4: {'lw', 'sw', 'lwc1', 'swc1'},
                8: {'ldc1', 'sdc1'}}.get(sz, set()) | {'addr'}
    if st == NM.PTRINT_T:
        return {'lw', 'sw', 'addr'}
    return {'lb', 'lbu', 'sb', 'lh', 'lhu', 'sh', 'lw', 'sw', 'lwc1', 'swc1', 'ldc1', 'sdc1', 'addr'}


def ido_layout(var, data):
    size, align = NM.layout(var['type'])[:2]
    return size, max(align, 8 if size >= 8 else 1, 4 if data else 1)


def rel_offsets(seq, data):
    out, addr = [], 0
    for v in seq:
        size, al = ido_layout(v, data)
        addr = (addr + al - 1) // al * al
        out.append(addr)
        addr += size
    return out


def score_var(imgs, snaps, a):
    o = a - 0x80000000
    good = tot = 0
    compat = getattr(imgs, 'compat', None)
    if compat is not None:
        import n64_coderefs
        kinds = n64_coderefs.coderefs().get(a)
        tot += REF_W
        if kinds and kinds & compat:
            good += REF_W
    for (img, ok, ptrs), (host, rd) in zip(imgs, snaps):
        if o < 0 or o + len(img) > len(rd):
            return 0, 1
        m = rd[o:o + len(img)]
        good += int((img[ok] == m[ok]).sum())
        tot += int(ok.sum())
        for no, nz in ptrs:
            tot += 4
            good += 4 * (bool(m[no:no + 4].any()) == nz)
    return good, tot


def block_score(b, base, S):
    g = t = 0
    for im, r in zip(b.imgs, b.rel):
        gg, tt = score_var(im, S, base + r)
        g += gg
        t += tt
    return g - 4 * (t - g), g, t


def refine_base(b, center, S, radius=0x100, step=4):
    best = max(range(center - radius, center + radius + 1, step), key=lambda c: block_score(b, c, S)[0])
    return best


def find_base(seq, rel, imgs, snaps, known):
    votes = {}
    for v, r in zip(seq, rel):
        if v['n'] in known:
            votes[known[v['n']] - r] = votes.get(known[v['n']] - r, 0) + 1
    if votes:
        return max(votes.items(), key=lambda kv: kv[1])[0], 'anchor'
    best = None
    for i, (v, r) in enumerate(zip(seq, rel)):
        for s, (img, ok, _p) in enumerate(imgs[i]):
            nz = int((ok & (img != 0)).sum())
            if nz >= 3 and ok.all() and (best is None or nz > best[0]):
                best = (nz, i, s)
    if best is None:
        return None, 'no signature'
    _, i, s = best
    img = imgs[i][s][0].tobytes()
    hay = snaps[s][1].tobytes()
    cands, p = [], hay.find(img)
    while p != -1 and len(cands) < 64:
        cands.append(0x80000000 + p - rel[i])
        p = hay.find(img, p + 1)
    scored = []
    for b in cands:
        g = t = 0
        for j, (v, r) in enumerate(zip(seq, rel)):
            gg, tt = score_var(imgs[j], snaps, b + r)
            g += gg
            t += tt
        scored.append((g - (t - g) * 4, b))
    if not scored:
        return None, 'signature not found'
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None, 'ambiguous'
    return scored[0][1], 'search'


SEC_ALIGN = 16


class Block:
    def __init__(self, path, data, seq, snaps):
        self.path, self.data, self.seq = path, data, seq
        self.rel = rel_offsets(seq, data)
        self.size = (self.rel[-1] + NM.layout(seq[-1]['type'])[0]) if seq else 0
        self.imgs = [snaps.images(v) for v in seq]
        self.base, self.how = None, None


def build_blocks(snaps):
    cus = _vars_by_cu()
    blocks = {}
    for sec in ('.data', '.bss'):
        for seg in link_order(sec):
            lst = []
            for rel_path in seg:
                f = os.path.join(SM.DECOMP, rel_path)
                cu = cus.get(rel_path, {})
                seq = []
                if os.path.exists(f):
                    for key, init in file_decls(f):
                        v = cu.get(key)
                        if (init == (sec == '.data') and v and v['type'] and _sec_of(v['rva'])[0]
                                and NM.layout(v['type'])[0] > 0):
                            seq.append(dict(v, key=key))
                lst.append(Block(rel_path, sec == '.data', seq, snaps))
            blocks.setdefault(sec, []).append(lst)
    return blocks


def report_gaps(blocks, log=print):
    for sec, segs in blocks.items():
        for seg in segs:
            prev = None
            for b in seg:
                if not b.seq:
                    continue
                if b.base is not None and prev is not None and prev.base is not None:
                    gap = b.base - (prev.base + prev.size)
                    log(f'  {sec} {prev.path} -> {b.path}: end {prev.base + prev.size:08X} next {b.base:08X} gap {gap}')
                prev = b


def main(log=print, verbose=False):
    known = json.load(open(BASE_MAP))['globals']
    snaps = Snapshots(log=log)
    S = snaps.snaps
    blocks = build_blocks(snaps)
    kn = {}
    for segs in blocks.values():
        for seg in segs:
            for b in seg:
                if not b.seq:
                    continue
                kk = {v['key']: known[v['key']] for v in b.seq if v['key'] in known}
                kn.update(kk)
                b.base, b.how = find_base([dict(v, n=v['key']) for v in b.seq], b.rel, b.imgs, S, kk)
    if verbose:
        report_gaps(blocks, log)
    for segs in blocks.values():
        for seg in segs:
            for _ in range(len(seg)):
                changed = False
                items = [b for b in seg if b.seq]
                for i, b in enumerate(items):
                    if b.base is not None:
                        continue
                    if i > 0 and items[i - 1].base is not None:
                        p = items[i - 1]
                        b.base = refine_base(b, (p.base + p.size + SEC_ALIGN - 1) // SEC_ALIGN * SEC_ALIGN, S)
                        b.how, changed = 'chain', True
                    elif i + 1 < len(items) and items[i + 1].base is not None:
                        n = items[i + 1]
                        b.base = refine_base(b, (n.base - b.size) // SEC_ALIGN * SEC_ALIGN, S)
                        b.how, changed = 'chain-back', True
                if not changed:
                    break
    for segs in blocks.values():
        for seg in segs:
            for b in seg:
                if b.base is not None and b.how.startswith('chain'):
                    _sc, g_, t_ = block_score(b, b.base, S)
                    if t_ == 0 or g_ < 0.9 * t_:
                        b.base, b.how = None, f'chain unverified ({g_}/{t_})'
    out, status, rejected = {}, {}, {}
    for segs in blocks.values():
        for seg in segs:
            for b in seg:
                if b.base is None:
                    for v in b.seq:
                        rejected[v['key']] = f'{b.path}: block not located ({b.how})'
                    continue
                prev_end = prev_a = prev_r = None
                for v, r, im in zip(b.seq, b.rel, b.imgs):
                    a = b.base + r if prev_a is None else prev_a + (r - prev_r)
                    g, t = score_var(im, S, a)
                    st = b.how
                    if v['key'] in known:
                        a, st = known[v['key']], 'anchor'
                    elif g < t:
                        lo = prev_end if prev_end is not None else a - 16
                        best = None
                        for c in range(lo, a + 33):
                            if c % max(NM.layout(v['type'])[1], 1):
                                continue
                            gg, tt = score_var(im, S, c)
                            if gg == tt and (best is None or abs(c - a) < abs(best - a)):
                                best = c
                        if best is None:
                            rejected[v['key']] = f'{b.path}: {g}/{t} bytes match at {a:08X}'
                            continue
                        a, st = best, 'shifted'
                    out[v['key']] = a
                    status[v['key']] = st + ('' if t else ' (no data to check)')
                    prev_end, prev_a, prev_r = a + NM.layout(v['type'])[0], a, r
    import n64_coderefs
    refs_all = sorted(n64_coderefs.coderefs())
    for segs in blocks.values():
        for seg in segs:
            for b in seg:
                placed = [out[v['key']] for v in b.seq if v['key'] in out]
                if not placed:
                    continue
                lo, hi = min(placed) - 0x200, max(placed) + 0x200
                cands = [a for a in refs_all if lo <= a < hi]
                for v, im in zip(b.seq, b.imgs):
                    k = v['key']
                    if k in known:
                        continue
                    if k in out:
                        g_, t_ = score_var(im, S, out[k])
                        if g_ == t_:
                            continue
                    best = []
                    for a in cands:
                        if a % max(NM.layout(v['type'])[1], 1):
                            continue
                        g_, t_ = score_var(im, S, a)
                        if g_ == t_ and t_ > REF_W:
                            nz = sum(int((ok & (img != 0) & (S[i][1][a - 0x80000000:a - 0x80000000 + len(img)] == img)).sum())
                                     for i, (img, ok, _p) in enumerate(im))
                            best.append((nz, a))
                    best.sort(reverse=True)
                    if best and best[0][0] > 0 and (len(best) == 1 or best[0][0] > best[1][0]):
                        out[k] = best[0][1]
                        status[k] = 'free'
                        rejected.pop(k, None)
    size = {}
    for n in set(out) | set(known):
        v = NM.G.get(n)
        size[n] = NM.layout(v['type'])[0] if v and v['type'] else 1
    iv = sorted((known[n], known[n] + max(size[n], 1), n) for n in known)
    starts = [x[0] for x in iv]
    import bisect
    newn = sorted((out[n], out[n] + max(size.get(n, 1), 1), n) for n in out if n not in known)
    bad = set()
    for a, e, n in newn:
        i = bisect.bisect_right(starts, e - 1) - 1
        while i >= 0 and iv[i][1] > a:
            if iv[i][2] != n:
                bad.add(n)
            i -= 1
    import n64_coderefs
    refs = n64_coderefs.coderefs()
    by_key = {v['key']: v for segs in blocks.values() for seg in segs for b in seg for v in b.seq}

    def on_ref(n):
        v = by_key.get(n)
        return n in out and v is not None and bool(refs.get(out[n], set()) & compat_kinds(v['type']))
    block_of = {v['key']: b for segs in blocks.values() for seg in segs for b in seg for v in b.seq}
    block_conf = {}
    for b in set(block_of.values()):
        placed = [v['key'] for v in b.seq if v['key'] in out]
        block_conf[id(b)] = sum(on_ref(k) for k in placed) / max(len(placed), 1)

    def rank_of(n):
        b = block_of.get(n)
        return (0 if on_ref(n) else 1, -round(block_conf.get(id(b), 0), 3),
                {'anchor': 0, 'free': 0, 'search': 1, 'chain': 2, 'chain-back': 2, 'shifted': 3}.get(status[n].split()[0], 9))
    ok_new = [x for x in newn if x[2] not in bad]
    conflicts = []
    for (a1, e1, n1), (a2, e2, n2) in zip(ok_new, ok_new[1:]):
        if a2 < e1:
            r1, r2 = rank_of(n1), rank_of(n2)
            if r1 == r2:
                bad.update((n1, n2))
            else:
                bad.add(n2 if r1 < r2 else n1)
            conflicts.append((n1, hex(a1), r1, n2, hex(a2), r2))
    for n in bad:
        rejected[n] = f'overlaps another variable at {out[n]:08X}'
        out.pop(n)
        status.pop(n)
    for n in out:
        if n not in known and rank_of(n)[0] == 0:
            status[n] += ' +coderef'
    json.dump({'globals': out, 'status': status, 'rejected': rejected, 'conflicts': conflicts}, open(OUT, 'w'), indent=0)
    new = [n for n in out if n not in known]
    log(f'{len(out)} placed ({len(new)} new: ' +
        ', '.join(f'{k} {sum(1 for n in new if status[n].startswith(k))}' for k in ('anchor', 'search', 'chain', 'shifted', 'free')) +
        f'; {sum(1 for n in new if "no data" in status[n])} unchecked), {len(rejected)} rejected -> {OUT}')
    return out, status, rejected


if __name__ == '__main__':
    main(verbose=True)
