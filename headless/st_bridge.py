import glob, hashlib, json, os, struct, subprocess, sys, tempfile, threading, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INDEX = os.path.join(HERE, '_st_index.json')
sys.path.insert(0, HERE)
import hl_paths
MUPEN = hl_paths.MUPEN
ROM = hl_paths.ROM
NOISE = {'gControllerStatuses', 'gControllerPads', 'dynlist_mario_master', 'sParticleTypes'}
_lock = threading.RLock()


def sha1(path):
    return hashlib.sha1(open(path, 'rb').read()).hexdigest()


def _load_index():
    try:
        return json.load(open(INDEX))
    except Exception:
        return {}


def _save_index(ix):
    tmp = INDEX + '.tmp'
    json.dump(ix, open(tmp, 'w'))
    os.replace(tmp, INDEX)


def register(path, inputs, timer=None, note=''):
    with _lock:
        ix = _load_index()
        ix[sha1(path)] = {'inputs': [list(x) for x in inputs], 'timer': timer, 'path': os.path.abspath(path), 'note': note}
        _save_index(ix)


def rdram(path):
    raw = open(path, 'rb').read()
    if raw[:4] == b'\x04\x22\x4d\x18':
        import lz4.frame
        d = lz4.frame.decompress(raw)
    else:
        import gzip
        d = gzip.decompress(raw)
    a = np.frombuffer(d, dtype=np.uint8, count=0x800000, offset=0x1B0).reshape(-1, 4)[:, ::-1].reshape(-1)
    return a.tobytes()


def st_timer(rd):
    return struct.unpack('>I', rd[0x32D5D4:0x32D5D8])[0]


def quick_equal(game, rd):
    from n64bus import N64Bus
    bus = getattr(game, '_bus', None) or N64Bus(game)
    game._bus = bus
    if bus.read_bytes(0x8032D5D4, 4) != rd[0x32D5D4:0x32D5D8]:
        return False
    if bus.read_bytes(0x8038EEE0, 2) != rd[0x38EEE0:0x38EEE2]:
        return False
    a = bus.read_bytes(0x8033B170, 0x60)
    return a == rd[0x33B170:0x33B1D0]


def compare(game, path_or_rd, full=True):
    import numpy as np
    import n64map_build as B
    from n64bus import N64Bus
    from sm64hl import _SECS
    rd = rdram(path_or_rd) if isinstance(path_or_rd, str) else path_or_rd
    rdn = np.frombuffer(rd, np.uint8)
    bus = getattr(game, '_bus', None) or N64Bus(game)
    game._bus = bus
    mem = game.save()[0]
    secs = {'.data': (np.frombuffer(mem[0], np.uint8), _SECS['.data']), '.bss': (np.frombuffer(mem[1], np.uint8), _SECS['.bss'])}
    import n64mem as NM
    diffs = []
    for (a, sz, hb, ty, name) in bus.regions:
        if name in NOISE or sz > 0x40000:
            continue
        rva = NM.G[name]['rva']
        for host, (va, vs) in secs.values():
            if va <= rva < va + vs:
                img, ok = B.n64_image(ty, host, rva - va)
                m = rdn[a - 0x80000000:a - 0x80000000 + sz]
                if not np.array_equal(img[ok], m[ok]):
                    diffs.append(name)
                break
        if not full and diffs:
            break
    return diffs


def _input_files(folder):
    out = []
    for p in glob.glob(os.path.join(folder, '*.txt')) + glob.glob(os.path.join(folder, '*.json')):
        try:
            from sm64hl import read_inputs
            inp = read_inputs(p)
            if inp and all(len(x) >= 3 for x in inp[:3]):
                out.append((p, inp))
        except Exception:
            pass
    return out


def resolve(path, game=None, log=print):
    from sm64hl import Game
    h = sha1(path)
    ix = _load_index()
    if h in ix:
        return ix[h]['inputs']
    rd = rdram(path)
    T = st_timer(rd)
    g = game or Game()
    snap = g.save()
    try:
        g.reset()
        if T == 1 and quick_equal(g, rd):
            register(path, [], 1, 'allumage')
            return []
        bases = [(e['timer'], e['inputs'], e.get('path', '')) for e in ix.values() if e.get('timer') and e['timer'] < T]
        bases.append((1, [], 'allumage'))
        files = _input_files(os.path.dirname(os.path.abspath(path)))
        tried = 0
        for tb, binp, bpath in sorted(bases, key=lambda b: -b[0]):
            for fp, finp in files:
                for lead, extra in ((0, 0), (0, 1), (1, 0), (1, 1)):
                    if tb + lead + len(finp) + extra != T:
                        continue
                    tried += 1
                    g.reset()
                    g.run(binp)
                    g.run([(0, 0, 0)] * lead + list(finp) + [(0, 0, 0)] * extra)
                    if quick_equal(g, rd) and not compare(g, rd, full=False):
                        inputs = list(binp) + [[0, 0, 0]] * lead + [list(x) for x in finp] + [[0, 0, 0]] * extra
                        register(path, inputs, T,
                                 f'auto : {os.path.basename(bpath)} + '
                                 f'{"ghost + " if lead else ""}{os.path.basename(fp)}'
                                 + (' +1' if extra else ''))
                        log(f'[headless] {os.path.basename(path)} = {os.path.basename(bpath) or "power-on"} + '
                            f'{"ghost frame + " if lead else ""}{os.path.basename(fp)}'
                            + (' +1 neutral' if extra else ''))
                        return inputs
        raise LookupError(f"{os.path.basename(path)} (timer {T}): no known input sequence leads there "
                          f"({tried} combinations tried). Record its inputs: python hl.py register file.st inputs...")
    finally:
        g.load(snap)


def index_folder(folder, log=print):
    items = []
    for p in glob.glob(os.path.join(folder, '*.st')):
        try:
            items.append((st_timer(rdram(p)), p))
        except Exception as e:
            log(f'{p}: unreadable ({e})')
    ok, bad = [], []
    for T, p in sorted(items):
        try:
            resolve(p, log=log)
            ok.append(p)
        except LookupError as e:
            bad.append((p, str(e)))
    return ok, bad


LUA = r'''
local N = %d
local OFFSET = %d
local OUT = "%s"
local DONE = "%s"
local n = 0
pcall(function() emu.set_speed_mode(2) end)
emu.atinput(function()
  n = n + 1
  if n == N + OFFSET then
    pcall(function() movie.stop() end)
    emu.pause(false)
    savestate.do_file(OUT, "save", function(res)
      local f = io.open(DONE, "w"); f:write(tostring(res)); f:close()
    end, true)
  end
end)
'''


def make_st(inputs, out_path, timeout=900, log=print, offset=0):
    from sm64hl import write_m64
    out_path = os.path.abspath(out_path)
    tmp = tempfile.mkdtemp(prefix='hl_mkst_')
    m64 = os.path.join(tmp, 'movie.m64')
    write_m64(list(inputs) + [(0, 0, 0)] * 4, m64)
    done = os.path.join(tmp, 'done.txt')
    st_tmp = os.path.join(tmp, 'state.st')
    lua = os.path.join(tmp, 'save.lua')
    open(lua, 'w').write(LUA % (len(inputs), offset, st_tmp.replace('\\', '/'), done.replace('\\', '/')))
    t0 = time.time()
    p = subprocess.Popen([MUPEN, '--rom', ROM, '--movie', m64, '--lua', lua])
    try:
        while time.time() - t0 < timeout:
            if os.path.exists(done) and os.path.exists(st_tmp) and os.path.getsize(st_tmp) > 0:
                time.sleep(0.5)
                break
            if p.poll() is not None:
                raise RuntimeError('Mupen closed before the save')
            time.sleep(0.25)
        else:
            raise TimeoutError('Mupen: no .st after %d s' % timeout)
    finally:
        p.kill()
    import shutil
    shutil.move(st_tmp, out_path)
    log(f'[headless] .st made by Mupen in {time.time() - t0:.0f} s: {out_path}')
    return out_path
