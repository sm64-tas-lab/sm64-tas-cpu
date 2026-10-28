import ctypes, os, shutil, struct, threading, itertools

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DLL_SRC = os.path.join(ROOT, 'sm64_us.dll')
INST_DIR = os.path.join(HERE, '_inst')


def _read_bytes(path):
    with open(path, 'rb') as f_:
        return f_.read()


def _pe_info(path):
    f = _read_bytes(path)
    pe = struct.unpack_from('<I', f, 0x3c)[0]
    ns = struct.unpack_from('<H', f, pe + 6)[0]
    osz = struct.unpack_from('<H', f, pe + 20)[0]
    opt = pe + 24
    exp_rva = struct.unpack_from('<I', f, opt + 112)[0]
    sec = opt + osz
    secs, raw = {}, []
    for i in range(ns):
        name = f[sec + i * 40:sec + i * 40 + 8].rstrip(b'\0').decode(errors='replace')
        vs, va, rs, rp = struct.unpack_from('<IIII', f, sec + i * 40 + 8)
        secs[name] = (va, vs)
        raw.append((va, vs, rp, rs))

    def r2o(r):
        for va, vs, rp, rs in raw:
            if va <= r < va + max(vs, rs):
                return r - va + rp
    eo = r2o(exp_rva)
    nf, nn, far, nar, oar = struct.unpack_from('<IIIII', f, eo + 20)
    syms = {}
    for i in range(nn):
        r = struct.unpack_from('<I', f, r2o(nar) + 4 * i)[0]
        o = r2o(r)
        name = f[o:f.index(b'\0', o)].decode()
        ordi = struct.unpack_from('<H', f, r2o(oar) + 2 * i)[0]
        syms[name] = struct.unpack_from('<I', f, r2o(far) + 4 * ordi)[0]
    return secs, syms


VERSIONS = {'us': DLL_SRC, 'jp': os.path.join(ROOT, 'sm64_jp.dll')}


def _cached_pe_info(dll=DLL_SRC):
    import json
    cache = os.path.join(HERE, '_syms_cache.json' if dll == DLL_SRC else f'_syms_cache_{os.path.basename(dll)}.json')
    st = os.stat(dll)
    key = f'{st.st_size}:{int(st.st_mtime)}'
    try:
        with open(cache, encoding='utf-8') as f_:
            d = json.load(f_)
        if d['key'] == key:
            return {k: tuple(v) for k, v in d['secs'].items()}, d['syms']
    except Exception:
        pass
    secs, syms = _pe_info(dll)
    with open(cache, 'w', encoding='utf-8') as f_:
        json.dump({'key': key, 'secs': secs, 'syms': syms}, f_)
    return secs, syms


def _rng_rva(dll, syms):
    f = _read_bytes(dll)
    pe = struct.unpack_from('<I', f, 0x3c)[0]
    ns = struct.unpack_from('<H', f, pe + 6)[0]
    osz = struct.unpack_from('<H', f, pe + 20)[0]
    raw = [struct.unpack_from('<IIII', f, pe + 24 + osz + i * 40 + 8) for i in range(ns)]
    a = syms['random_u16']
    o = next(a - va + rp for vs, va, rs, rp in raw if va <= a < va + max(vs, rs))
    code = f[o:o + 64]
    i = code.find(bytes([0x0F, 0xB7, 0x05]))
    return a + i + 7 + struct.unpack_from('<i', code, i + 3)[0]


_VINFO = {}


def version_info(ver):
    if ver not in _VINFO:
        dll = VERSIONS[ver]
        secs, syms = _cached_pe_info(dll)
        _VINFO[ver] = (dll, secs, syms, _rng_rva(dll, syms))
    return _VINFO[ver]


_SECS, _SYMS = _cached_pe_info()
_ids = {}
INST_MODE = os.environ.get('SM64HL_INST', 'managed')
_slots = {}
_live = []
_cleaned = [False]
STALE_AGE = 60.0


def _free_library(handle):
    try:
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.FreeLibrary.argtypes = [ctypes.c_void_p]
        return bool(k32.FreeLibrary(ctypes.c_void_p(handle)))
    except Exception:
        return False


def cleanup_stale(inst_dir=INST_DIR, min_age=STALE_AGE):
    import time
    n = size = 0
    if not os.path.isdir(inst_dir):
        return 0, 0
    me = f'_p{os.getpid()}_'
    now = time.time()
    for f in os.listdir(inst_dir):
        if not (f.startswith('sm64_') and (f.endswith('.dll') or f.endswith('.tmp'))) or me in f:
            continue
        path = os.path.join(inst_dir, f)
        try:
            if now - os.path.getmtime(path) < min_age:
                continue
            sz = os.path.getsize(path)
            os.remove(path)
            n += 1
            size += sz
        except OSError:
            pass
    return n, size


def _atexit():
    for ref in list(_live):
        g = ref()
        if g is not None:
            try:
                g.close()
            except Exception:
                pass
    if INST_MODE == 'managed' and os.path.isdir(INST_DIR):
        me = f'_p{os.getpid()}_'
        for f in os.listdir(INST_DIR):
            if me in f:
                try:
                    os.remove(os.path.join(INST_DIR, f))
                except OSError:
                    pass


import atexit as _atexit_mod
import weakref as _weakref
_atexit_mod.register(_atexit)
RNG_RVA = 0xCFF020
M64_TEMPLATE = os.path.join(ROOT, 'sm64-toolkit', 'tas', 'control', 'm64_header_template.bin')
_lock = threading.Lock()

MS_FIELDS = {
    'action': (0x0C, 'I'), 'action_state': (0x18, 'H'), 'action_timer': (0x1A, 'H'),
    'intended_mag': (0x20, 'f'), 'intended_yaw': (0x24, 'h'),
    'face_pitch': (0x2C, 'h'), 'face_yaw': (0x2E, 'h'), 'face_roll': (0x30, 'h'),
    'pos_x': (0x3C, 'f'), 'pos_y': (0x40, 'f'), 'pos_z': (0x44, 'f'),
    'vel_x': (0x48, 'f'), 'vel_y': (0x4C, 'f'), 'vel_z': (0x50, 'f'),
    'forward_vel': (0x54, 'f'), 'slide_vel_x': (0x58, 'f'), 'slide_vel_z': (0x5C, 'f'),
}


class Game:

    def __init__(self, boot=True, version='us'):
        src, self.secs, self.syms, self.rng_rva = version_info(version)
        self.version = version
        tag = '' if version == 'us' else version + '_'
        if INST_MODE == 'legacy':
            with _lock:
                n = _ids[version] = _ids.get(version, -1) + 1
            path = src if n == 0 else os.path.join(INST_DIR, f'sm64_{tag}{n}.dll')
        else:
            with _lock:
                if not _cleaned[0]:
                    _cleaned[0] = True
                    cleanup_stale()
                busy = _slots.setdefault(version, set())
                n = 0
                while n in busy:
                    n += 1
                busy.add(n)
            path = src if n == 0 else os.path.join(INST_DIR, f'sm64_{tag}p{os.getpid()}_{n}.dll')
        self._slot = n
        if not os.path.exists(path) or os.path.getsize(path) != os.path.getsize(src):
            os.makedirs(INST_DIR, exist_ok=True)
            tmp = f'{path}.{os.getpid()}.tmp'
            shutil.copyfile(src, tmp)
            os.replace(tmp, path)
        self.path = path
        self.dll = ctypes.CDLL(path)
        self._closed = False
        _live.append(_weakref.ref(self))
        self.base = self.dll._handle
        self.dll.sm64_init.restype = None
        self.dll.sm64_update.restype = None
        self._ranges = [(self.base + self.secs[s][0], self.secs[s][1]) for s in ('.data', '.bss')]
        self.pad = self.addr('gControllerPads')
        self.ms = self.addr('gMarioStates')
        self.history = []
        self.frame_hooks = []
        if boot:
            self.dll.sm64_init()
            self._boot = self.save()

    def close(self):
        if getattr(self, '_closed', True):
            return
        self._closed = True
        h = getattr(self, 'base', None)
        self.dll = None
        if h:
            _free_library(h)
        if INST_MODE == 'managed':
            with _lock:
                _slots.get(self.version, set()).discard(self._slot)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def addr(self, sym):
        return self.base + self.syms[sym]

    def read(self, sym_or_addr, fmt, off=0):
        a = self.addr(sym_or_addr) if isinstance(sym_or_addr, str) else sym_or_addr
        n = struct.calcsize('<' + fmt)
        return struct.unpack('<' + fmt, ctypes.string_at(a + off, n))[0]

    def write(self, sym_or_addr, fmt, val, off=0):
        a = self.addr(sym_or_addr) if isinstance(sym_or_addr, str) else sym_or_addr
        b = struct.pack('<' + fmt, val)
        ctypes.memmove(a + off, b, len(b))

    def save(self):
        return ([ctypes.string_at(a, n) for a, n in self._ranges], tuple(self.history))

    def load(self, state):
        mem, hist = state
        for (a, sz), b in zip(self._ranges, mem):
            ctypes.memmove(a, b, sz)
        self.history = list(hist)

    def save_file(self, path):
        import json
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'format': 'sm64hl-inputs-v1', 'depart': 'allumage', 'inputs': self.history}, f)

    def load_file(self, path):
        if str(path).lower().endswith('.m64'):
            inputs = read_m64(path)
        else:
            import json
            with open(path, 'r', encoding='utf-8', errors='ignore') as f_:
                txt = f_.read().strip()
            d = json.loads(txt) if txt.startswith('{') else None
            inputs = d['inputs'] if d else read_inputs(path)
        self.reset()
        self.run(inputs)

    def save_snapshot(self, path):
        """Saves the full compressed memory state (instant loading, no replay), with the DLL's base address and every
        input played since power-on: the memory holds absolute pointers, valid only where the DLL is loaded now."""
        import pickle, zlib
        snap = {'v': 2, 'base': self.base, 'pid': os.getpid(), 'state': self.save(), 'history': list(self.history)}
        data = zlib.compress(pickle.dumps(snap, protocol=pickle.HIGHEST_PROTOCOL))
        with open(path, 'wb') as f:
            f.write(data)

    def load_snapshot(self, path):
        """Loads a snapshot: its memory at once when it was saved by this process with the DLL at the same address,
        else its inputs played again from power-on (another run: the saved pointers would crash the game; a replay
        takes a fraction of a second)."""
        import pickle, zlib
        with open(path, 'rb') as f:
            snap = pickle.loads(zlib.decompress(f.read()))
        if not isinstance(snap, dict):         # (an old snapshot: memory only, no way to check it)
            self.load(snap)
            return 'memory'
        if snap['base'] == self.base and snap.get('pid') == os.getpid():
            self.load(snap['state'])
            self.history[:] = snap['history']
            return 'memory'
        self.load(self._boot)
        del self.history[:]
        for f in snap['history']:
            self.step(f)
        return 'replayed'

    def reset(self):
        self.load(self._boot)

    def set_input(self, x=0, y=0, hi=0, lo=0):
        ctypes.memmove(self.pad, struct.pack('<Hbb', (hi << 8) | lo, x, y), 4)

    def step(self, inp=(0, 0, 0)):
        hi, lo = to_hilo(inp)
        self.set_input(inp[0], inp[1], hi, lo)
        self.dll.sm64_update()
        for h in self.frame_hooks:
            h(self)
        self.history.append(tuple(inp))

    def run(self, inputs, sample=None):
        out = []
        for i, inp in enumerate(inputs):
            self.step(inp)
            if sample == 'all':
                out.append(self.mario())
            elif callable(sample):
                v = sample(self, i)
                if v is StopIteration:
                    break
                out.append(v)
        return out

    def mario(self):
        raw = ctypes.string_at(self.ms, 0x60)
        s = {k: struct.unpack_from('<' + t, raw, o)[0] for k, (o, t) in MS_FIELDS.items()}
        s['level'] = self.read('gCurrLevelNum', 'h')
        s['area'] = self.read('gCurrAreaIndex', 'h') if 'gCurrAreaIndex' in self.syms else None
        s['timer'] = self.read('gGlobalTimer', 'I')
        s['rng'] = self.rng()
        s['cam_yaw'] = self.cam_yaw()
        s['health'] = self.read(self.ms, 'h', 0xEA)
        s['coins'] = self.read(self.ms, 'h', 0xE4)
        s['stars'] = self.read(self.ms, 'h', 0xE6)
        s['floor_height'] = self.read(self.ms, 'f', 0x7C)
        return s

    def rng(self):
        return self.read(self.base + self.rng_rva, 'H')

    def cam_yaw(self):
        cam = self.read('gCamera', 'Q')
        return self.read(cam, 'h', 2) if cam else None

    def timer(self):
        return self.read('gGlobalTimer', 'I')

    @property
    def stars(self):
        return self.read(self.ms, 'h', 0xE6)

    @property
    def coins(self):
        return self.read(self.ms, 'h', 0xE4)

    @property
    def health(self):
        return self.read(self.ms, 'h', 0xEA)

    @property
    def level(self):
        return self.read('gCurrLevelNum', 'h')

    def objects(self, active_only=True, bhv=None):
        """Lists the objects in gObjectPool (64-bit Object struct, 240 slots).
        Returns dicts holding the decoded fields and the resolved behavior name."""
        if not hasattr(self, '_bhv_syms'):
            self._bhv_syms = {v: k for k, v in self.syms.items() if k.startswith('bhv')}

        pool = self.addr('gObjectPool')
        out = []
        for i in range(240):
            base = pool + i * 1392
            active = self.read(base + 180, 'h')
            is_active = bool(active & 1)
            if active_only and not is_active:
                continue

            bhv_ptr = self.read(base + 1288, 'Q')
            rva = bhv_ptr - self.base
            bhv_name = self._bhv_syms.get(rva, f'0x{rva:X}')
            if bhv is not None:
                if isinstance(bhv, str) and bhv != bhv_name and not bhv_name.endswith(bhv):
                    continue

            raw_base = base + 216
            px = self.read(raw_base + 4 * 6, 'f')
            py = self.read(raw_base + 4 * 7, 'f')
            pz = self.read(raw_base + 4 * 8, 'f')
            vx = self.read(raw_base + 4 * 3, 'f')
            vy = self.read(raw_base + 4 * 4, 'f')
            vz = self.read(raw_base + 4 * 5, 'f')
            act = self.read(raw_base + 4 * 0x31, 'i')
            subact = self.read(raw_base + 4 * 0x32, 'i')
            timer = self.read(raw_base + 4 * 0x33, 'i')
            health = self.read(raw_base + 4 * 0x3F, 'i')
            held = self.read(raw_base + 4 * 0x27, 'i')
            move_flags = self.read(raw_base + 4 * 0x19, 'i')
            face_yaw = self.read(raw_base + 4 * 0x0D, 'i')

            out.append({
                'slot': i,
                'active': is_active,
                'bhv': bhv_name,
                'bhv_ptr': bhv_ptr,
                'pos_x': px, 'pos_y': py, 'pos_z': pz,
                'vel_x': vx, 'vel_y': vy, 'vel_z': vz,
                'face_yaw': face_yaw,
                'action': act,
                'subaction': subact,
                'timer': timer,
                'health': health,
                'held_state': held,
                'move_flags': move_flags,
                'addr': base
            })
        return out

    def find_object(self, bhv=None, slot=None, predicate=None):
        """Finds the first object matching the criteria."""
        objs = self.objects(active_only=True)
        for o in objs:
            if slot is not None and o['slot'] != slot:
                continue
            if bhv is not None and o['bhv'] != bhv and not o['bhv'].endswith(bhv):
                continue
            if predicate is not None and not predicate(o):
                continue
            return o
        return None

    def surfaces(self, kind=None):
        """Lists the static surfaces of the current area.
        kind: 'floor' (ny > 0.01), 'wall' (-0.01 <= ny <= 0.01), 'ceil' (ny < -0.01), or None."""
        if 'sSurfacePool' not in self.syms or 'gNumStaticSurfaces' not in self.syms:
            return []
        sp = self.read('sSurfacePool', 'Q')
        ns = self.read('gNumStaticSurfaces', 'i')
        if not sp or ns <= 0:
            return []
        out = []
        for i in range(ns):
            base = sp + i * 56
            stype = self.read(base, 'h')
            flags = self.read(base + 4, 'b')
            v1 = (self.read(base + 10, 'h'), self.read(base + 12, 'h'), self.read(base + 14, 'h'))
            v2 = (self.read(base + 16, 'h'), self.read(base + 18, 'h'), self.read(base + 20, 'h'))
            v3 = (self.read(base + 22, 'h'), self.read(base + 24, 'h'), self.read(base + 26, 'h'))
            nx = self.read(base + 28, 'f')
            ny = self.read(base + 32, 'f')
            nz = self.read(base + 36, 'f')
            origin = self.read(base + 40, 'f')
            obj = self.read(base + 48, 'Q')

            if ny > 0.01:
                skind = 'floor'
            elif ny < -0.01:
                skind = 'ceil'
            else:
                skind = 'wall'

            if kind is not None and skind != kind:
                continue

            out.append({
                'index': i,
                'type': stype,
                'kind': skind,
                'normal': (nx, ny, nz),
                'origin': origin,
                'v1': v1, 'v2': v2, 'v3': v3,
                'object': obj,
                'addr': base
            })
        return out


def read_inputs(path):
    with open(path) as f_:
        txt = f_.read().strip()
    if txt.startswith('['):
        import json
        return [tuple(v) for v in json.loads(txt)]
    return [tuple(int(v) for v in line.split()) for line in txt.splitlines() if line.strip()]


def fmt(s):
    return '({:.1f},{:.1f},{:.1f}) v={:.1f} yaw={} act={:X} lvl={} t={} rng={} cam={}'.format(
        s['pos_x'], s['pos_y'], s['pos_z'], s['forward_vel'], s['face_yaw'], s['action'], s['level'],
        s['timer'], s.get('rng'), s.get('cam_yaw'))


def to_hilo(f):
    if len(f) > 3:
        return f[2] & 0xFF, f[3] & 0xFF
    b = f[2] if len(f) > 2 else 0
    return b & 0xF0, (0x10 if b & 0x08 else 0) | (0x20 if b & 0x04 else 0)


def write_m64(inputs, path, poweron=True, template=M64_TEMPLATE):
    hdr = bytearray(_read_bytes(template)[:1024])
    n = len(inputs)
    struct.pack_into('<I', hdr, 0x0C, n)
    struct.pack_into('<I', hdr, 0x18, n)
    struct.pack_into('<H', hdr, 0x1C, 2 if poweron else 1)
    body = bytearray()
    for f in inputs:
        hi, lo = to_hilo(f)
        body += bytes([hi & 255, lo & 255, f[0] & 255, f[1] & 255])
    with open(path, 'wb') as f_:
        f_.write(bytes(hdr) + bytes(body))
    return n


def read_m64(path):
    d = _read_bytes(path)
    n = struct.unpack_from('<I', d, 0x18)[0]
    out = []
    for i in range(n):
        hi, lo, x, y = d[0x400 + 4 * i:0x404 + 4 * i]
        x, y = (x - 256 if x > 127 else x), (y - 256 if y > 127 else y)
        out.append((x, y, hi, lo))
    return out


_W = {}


def _winit(prefix, state_file):
    g = Game()
    if state_file:
        g.load_file(state_file)
    g.run(prefix)
    _W['g'] = g
    _W['s'] = g.save()


def _wjob(args):
    fn, cand = args
    g = _W['g']
    g.load(_W['s'])
    return fn(g, cand)


def parallel_map(fn, candidates, prefix=(), state_file=None, workers=None, chunksize=16):
    import multiprocessing as mp
    workers = workers or max(1, mp.cpu_count() - 2)
    with mp.Pool(workers, initializer=_winit, initargs=(list(prefix), state_file)) as pool:
        return pool.map(_wjob, [(fn, c) for c in candidates], chunksize=chunksize)


def _mupen_paths(mupen_exe=None, rom_path=None):
    """Mupen and the US ROM: the arguments, else MUPEN_EXE / SM64_ROM, else config.json (INSTALL.bat writes it),
    else the ROM at the repo's root"""
    cfg = {}
    try:
        import json as _json
        cfg = _json.load(open(os.path.join(ROOT, 'config.json'), encoding='utf-8'))
    except (OSError, ValueError):
        pass
    mupen = mupen_exe or os.environ.get('MUPEN_EXE') or cfg.get('mupen')
    rom = (rom_path or os.environ.get('SM64_ROM') or cfg.get('rom_us')
           or os.path.join(ROOT, 'Super Mario 64 (USA).z64'))
    if not mupen or not os.path.isfile(mupen):
        raise FileNotFoundError('Mupen64 not found: set MUPEN_EXE or run INSTALL.bat again')
    if not os.path.isfile(rom):
        raise FileNotFoundError('the US ROM not found: set SM64_ROM or run INSTALL.bat again')
    return mupen, rom


def export_mupen_savestate(movie_path, frame, st_path, clean=True, mupen_exe=None, rom_path=None, timeout=60):
    """
    Runs Mupen64 at turbo speed (speed_mode=2) to replay movie_path up to frame and save st_path.
    clean=True: stops the movie before saving so that freeze.current_sample is not embedded in the .st,
                which guarantees the savestate can serve as the base for a new standalone movie.
    """
    import os, time, subprocess, tempfile, shutil
    mupen, rom = _mupen_paths(mupen_exe, rom_path)

    tmp = tempfile.mkdtemp(prefix='hl_exp_st_')
    lua = os.path.join(tmp, 'save_st.lua')
    done = os.path.join(tmp, 'done.txt')
    st_tmp = os.path.join(tmp, 'state.st')

    stop_cmd = "pcall(function() movie.stop() end)" if clean else ""
    st_fwd, done_fwd = st_tmp.replace('\\', '/'), done.replace('\\', '/')    # (no backslash inside an f-string: 3.9)
    lua_code = f"""
local F = {frame}
local ST = "{st_fwd}"
local DONE = "{done_fwd}"
local n = 0

pcall(function() emu.set_speed_mode(2) end)

emu.atinput(function()
  n = n + 1
  if n == F then
    {stop_cmd}
    pcall(function() emu.pause(true) end)
    savestate.do_file(ST, "save", function(res)
      local f = io.open(DONE, "w"); f:write("ok"); f:close()
    end, true)
  end
end)
"""
    with open(lua, 'w') as f:
        f.write(lua_code)
        
    t0 = time.time()
    p = subprocess.Popen([mupen, '--rom', rom, '--movie', movie_path, '--lua', lua])
    
    success = False
    try:
        while time.time() - t0 < timeout:
            if os.path.exists(done) and os.path.exists(st_tmp) and os.path.getsize(st_tmp) > 0:
                time.sleep(0.5)
                success = True
                break
            if p.poll() is not None:
                break
            time.sleep(0.2)
    finally:
        try: p.kill()
        except: pass
        
    if success:
        os.makedirs(os.path.dirname(os.path.abspath(st_path)), exist_ok=True)
        shutil.copyfile(st_tmp, st_path)
        return True
    return False


def take_mupen_screenshot(st_path, png_path, mupen_exe=None, rom_path=None, timeout=25):
    """
    Starts Mupen64 on a .st savestate, advances a few frames to flush the OpenGL buffer,
    and saves the full-colour screenshot as a PNG.
    """
    import os, time, subprocess, tempfile, shutil
    from PIL import Image
    
    mupen, rom = _mupen_paths(mupen_exe, rom_path)
    bmp_dir = os.path.join(os.path.dirname(mupen), 'screenshots')

    tmp = tempfile.mkdtemp(prefix='hl_shot_')
    lua = os.path.join(tmp, 'shot.lua')
    done = os.path.join(tmp, 'done.txt')

    t0 = time.time()
    st_fwd, done_fwd = st_path.replace('\\', '/'), done.replace('\\', '/')    # (no backslash inside an f-string: 3.9)
    lua_code = f"""
local ST = "{st_fwd}"
local DONE = "{done_fwd}"
local frame = 0

savestate.do_file(ST, "load", function()
    pcall(function() emu.pause(false) end)
end)

emu.atinput(function()
    frame = frame + 1
    if frame == 10 then
        pcall(function() action.invoke("save_screenshot") end)
    end
    if frame == 15 then
        local f = io.open(DONE, "w"); f:write("ok"); f:close()
    end
end)
"""
    with open(lua, 'w') as f:
        f.write(lua_code)
        
    p = subprocess.Popen([mupen, '--rom', rom, '--lua', lua])
    try:
        while time.time() - t0 < timeout:
            if os.path.exists(done):
                time.sleep(0.5)
                break
            time.sleep(0.2)
    finally:
        try: p.kill()
        except: pass
        
    # Check for newly generated BMP
    if os.path.exists(bmp_dir):
        bmps = [os.path.join(bmp_dir, f) for f in os.listdir(bmp_dir) if f.endswith('.bmp')]
        bmps.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        for b in bmps:
            if os.path.getmtime(b) >= t0 and os.path.getsize(b) > 1000:
                os.makedirs(os.path.dirname(os.path.abspath(png_path)), exist_ok=True)
                img = Image.open(b)
                img.save(png_path)
                return True
    return False
