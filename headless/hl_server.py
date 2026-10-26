import argparse, json, math, os, socket, struct, sys, threading, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sm64hl import Game, read_inputs, read_m64, write_m64
from n64bus import N64Bus
import st_bridge as SB

MARIO = 0x8033B170
G_ADDR = {'red_coin_count': 0x803613FE, 'hud_coin_count': 0x803316D2, 'course_index': 0x8033BAC6,
          'current_area': 0x8032DDCC, 'rng_value': 0x8038EEE0, 'global_timer': 0x8032D5D4}
PACK = [('action', MARIO + 0x0C, 'u32'), ('prev_action', MARIO + 0x10, 'u32'), ('action_timer', MARIO + 0x16, 'u16'),
        ('intended_mag', MARIO + 0x20, 'f32'), ('intended_yaw', MARIO + 0x24, 's16'), ('face_yaw', MARIO + 0x2E, 's16'),
        ('face_pitch', MARIO + 0x2C, 's16'), ('slide_yaw', MARIO + 0x38, 's16'),
        ('pos_x', MARIO + 0x3C, 'f32'), ('pos_y', MARIO + 0x40, 'f32'), ('pos_z', MARIO + 0x44, 'f32'),
        ('vel_x', MARIO + 0x48, 'f32'), ('vel_y', MARIO + 0x4C, 'f32'), ('vel_z', MARIO + 0x50, 'f32'),
        ('forward_vel', MARIO + 0x54, 'f32'), ('slide_vel_x', MARIO + 0x58, 'f32'), ('slide_vel_z', MARIO + 0x5C, 'f32'),
        ('ceil_height', MARIO + 0x6C, 'f32'), ('floor_height', MARIO + 0x70, 'f32'), ('ridden_obj', MARIO + 0x84, 'u32'),
        ('num_coins', MARIO + 0xA8, 's16'), ('num_stars', MARIO + 0xAA, 's16'), ('health', MARIO + 0xAE, 's16'),
        ('hurt_counter', MARIO + 0xB2, 'u8'), ('invinc_timer', MARIO + 0xB6, 'u16')]
BTN = {'A': 0x80, 'B': 0x40, 'Z': 0x20, 'start': 0x10, 'R': 0x08, 'L': 0x04}


def safe(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return 0.0
    return v


class Emu:
    def __init__(self, log=None):
        self.b3313 = False
        if os.environ.get('SM64_GAME') == 'b3313':
            self._use_b3313()
        else:
            self.g = Game()
            self.bus = N64Bus(self.g)
        self.g._bus = self.bus
        self.mem = {}
        self.slots = {}
        self.sticky = (0, 0, 0)
        self.port = 1
        self.log = log or (lambda *a: print(*a, file=sys.stderr, flush=True))
        self.pool = None
        self.pending = {}
        self.lock = threading.RLock()
        self.phantom = False
        self.phantom_mode = not self.b3313

    def _use_b3313(self):
        sys.path.insert(0, os.environ.get('B3313_RT_DIR') or os.path.join(os.path.expanduser('~'), 'b3313-recomp', 'rt'))
        import b3313hl
        self.b3313hl = b3313hl
        self.g = b3313hl.Game()
        self.bus = b3313hl.Bus(self.g)
        self.g._bus = self.bus
        self.b3313 = True
        self.phantom_mode = False
        self.phantom = False
        self.mem, self.slots = {}, {}

    @staticmethod
    def is_b3313_file(path):
        try:
            raw = open(path, 'rb').read()
            if path.lower().endswith('.m64'):
                return b'ULTRA 64 MARIO' in raw[0xC4:0xE4]
            import lz4.frame
            d = lz4.frame.decompress(raw) if raw[:4] == b'\x04\x22\x4d\x18' else __import__('gzip').decompress(raw)
            rd = d[0x1B0:0x1B0 + 0x800000]
            rom = os.environ.get('B3313_ROM') or os.path.join(os.path.expanduser('~'), 'b3313-reverse', 'roms', 'b3313.z64')
            if not os.path.exists(rom):
                return False
            with open(rom, 'rb') as f:
                f.seek(0x1200000 + 0x2F00)
                want = f.read(64)
            got = b''.join(rd[0x402F00 + i:0x402F04 + i][::-1] for i in range(0, 64, 4))
            return got == want
        except Exception:
            return False

    def _auto_game(self, path):
        is_b = self.is_b3313_file(path)
        if not self.b3313 and is_b:
            self.log(f'[headless] {os.path.basename(path)} is B3313: switching to the recompiled B3313 engine')
            self._use_b3313()
        elif self.b3313 and not is_b:
            rom = os.environ.get('B3313_ROM') or os.path.join(os.path.expanduser('~'), 'b3313-reverse', 'roms', 'b3313.z64')
            if path.lower().endswith('.m64') or os.path.exists(rom):
                raise RuntimeError(f'{os.path.basename(path)} : SM64 file loaded in a B3313 session, '
                                   'restart the server without SM64_GAME=b3313')

    def rd(self, a, t):
        try:
            return safe(self.bus.read(a, t))
        except KeyError:
            return None

    def pack(self):
        s = {k: self.rd(a, t) for k, a, t in PACK}
        s['red_coins'] = self.rd(G_ADDR['red_coin_count'], 's8')
        s['hud_coins'] = self.rd(G_ADDR['hud_coin_count'], 'u16')
        s['course'] = self.rd(G_ADDR['course_index'], 's16')
        s['rng'] = self.rd(G_ADDR['rng_value'], 'u16')
        s['global_timer'] = self.rd(G_ADDR['global_timer'], 'u32')
        s['frame'] = len(self.g.history)
        s['samples'] = len(self.g.history)
        return s

    @staticmethod
    def inp_from_table(t):
        t = t or {}
        b = 0
        for k, m in BTN.items():
            if t.get(k):
                b |= m
        hi = b & 0xF0
        lo = (0x10 if t.get('R') else 0) | (0x20 if t.get('L') else 0)
        for k, m in (('right', 0x01), ('left', 0x02), ('down', 0x04), ('up', 0x08)):
            if t.get(k):
                hi |= m
        for k, m in (('Cright', 0x01), ('Cleft', 0x02), ('Cdown', 0x04), ('Cup', 0x08)):
            if t.get(k):
                lo |= m
        return (int(t.get('X', 0) or 0), int(t.get('Y', 0) or 0), hi, lo)

    def load_st(self, path):
        path = os.path.abspath(path)
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        self._auto_game(path)
        if self.b3313:
            n = self.b3313hl.load_mupen_st(self.g, path)
            self.log(f'[b3313] {os.path.basename(path)}: movie frame {n}, Mario identical')
            return
        inputs = SB.resolve(path, game=self.g, log=self.log)
        self.g.reset()
        self.g.run([tuple(x) for x in inputs])
        if True:
            rd = SB.rdram(path)
            if not SB.quick_equal(self.g, rd):
                raise RuntimeError(f'{os.path.basename(path)} : the replay does not reproduce the .st')

    def save_st(self, path):
        path = os.path.abspath(path)
        inputs = [list(x) for x in self.g.history]
        json.dump({'format': 'sm64hl-inputs-v1', 'depart': 'allumage', 'inputs': inputs}, open(path + '.hls', 'w'))
        if self.b3313:
            self.log(f'[b3313] {os.path.basename(path)}.hls written ({len(inputs)} inputs since power-on)')
            return
        tmp = path + '.hl_tmp.st'
        SB.make_st(inputs, tmp, log=self.log)
        snap = self.g.save()
        try:
            d = SB.compare(self.g, tmp)
            if d:
                self.log(f'[headless] {os.path.basename(path)} : mismatch {d[:5]}, retrying with an offset')
                SB.make_st(inputs, tmp, log=self.log, offset=1)
                d = SB.compare(self.g, tmp)
        finally:
            self.g.load(snap)
        if d:
            os.remove(tmp)
            raise RuntimeError(f'.st made by Mupen differs from the headless engine: {d[:8]}')
        os.replace(tmp, path)
        SB.register(path, inputs, len(inputs) + 1, 'made by the headless engine')
        self.log(f'[headless] {os.path.basename(path)} written and verified')

    def op_ping(self, a):
        return {'pong': True, 'version': 'b3313-recomp' if self.b3313 else 'sm64-headless', 'frame': len(self.g.history)}

    def op_getframe(self, a):
        n = len(self.g.history)
        return {'frame': n, 'samples': n, 'inputs': n}

    def op_read(self, a):
        return {'value': self.rd(int(a['addr']) & 0xFFFFFFFF, a.get('type', 'u32'))}

    def op_readmany(self, a):
        return {'values': [self.rd(int(x['addr']) & 0xFFFFFFFF, x.get('type', 'u32')) for x in a.get('list', [])]}

    def op_write(self, a):
        self.bus.write(int(a['addr']), a['value'], a.get('type', 'u32'))
        return {'ok': True}

    def op_readrange(self, a):
        return {'bytes': list(self.bus.read_bytes(int(a['addr']), int(a.get('count', 0))))}

    def op_state(self, a):
        return self.pack()

    def op_setinput(self, a):
        self.sticky = self.inp_from_table(a.get('input'))
        return {'ok': True}

    def op_setport(self, a):
        self.port = a.get('port', 1)
        return {'port': self.port}

    def _phantom(self):
        if self.phantom:
            self.phantom = False
            self.g.step((0, 0, 0))

    def op_advance(self, a):
        if a.get('input') is not None:
            self.sticky = self.inp_from_table(a['input'])
        self._phantom()
        for _ in range(int(a.get('frames', 1))):
            self.g.step(self.sticky)
        return self.pack()

    def op_replaylist(self, a):
        fr = a.get('frames') or []
        samp = set(int(i) for i in (a.get('samples') or []))
        allf = bool(a.get('sampleall'))
        stop = a.get('stopstars')
        out, autob = [], []
        last_b = False
        n = len(fr)
        self._phantom()
        for i, f in enumerate(fr, 1):
            bt = int(f[2]) if len(f) > 2 and f[2] is not None else 0
            if (bt >> 8) & 1:
                bt &= 0xFF
                act = self.bus.read(MARIO + 0x0C, 'u32')
                if (act & 0x200) and act != 0x00800380 and not last_b:
                    bt |= 0x40
                    autob.append(i)
            last_b = bool(bt & 0x40)
            self.g.step((int(f[0]), int(f[1]), bt))
            if allf or i in samp:
                out.append({'f': i, 's': self.pack()})
            if stop is not None and self.rd(MARIO + 0xAA, 's16') >= int(stop):
                out.append({'f': i, 's': self.pack(), 'stop': True})
                return {'frames': out, 'autob': autob}
        if not (n in samp or allf):
            out.append({'f': n, 's': self.pack(), 'final': True})
        return {'frames': out, 'autob': autob}

    def op_savestate(self, a):
        job = a.get('job', 'load')
        if a.get('path'):
            if job == 'save':
                self.save_st(a['path'])
            else:
                self.load_st(a['path'])
                self.phantom = self.phantom_mode
        else:
            slot = a.get('slot', 1)
            if job == 'save':
                self.slots[slot] = self.g.save()
            else:
                if slot not in self.slots:
                    raise KeyError(f'slot {slot} empty')
                self.g.load(self.slots[slot])
                self.phantom = self.phantom_mode
        return {'ok': True, 'result_code': 0}

    def op_memstate(self, a):
        slot = a.get('slot', 1)
        if a.get('job') == 'save':
            self.mem[slot] = self.g.save()
            st = self.mem[slot]
            return {'ok': True, 'slot': slot, 'bytes': sum(len(b) for b in st[0]) if isinstance(st, tuple) else 0}
        if slot not in self.mem:
            raise KeyError(f'no in-memory state in slot {slot}')
        self.g.load(self.mem[slot])
        self.phantom = self.phantom_mode
        return {'ok': True, 'slot': slot}

    def op_pause(self, a):
        return {'paused': a.get('value', True) is not False}

    def op_speed(self, a):
        return {'ok': True}

    def op_eval(self, a):
        code = a.get('code', '')
        if 'speed' in code.lower():
            return {'ok': True, 'result': '1'}
        raise NotImplementedError('headless: no Lua (only speed settings are accepted)')

    def op_b3313_red_stars(self, a):
        if not self.b3313:
            raise RuntimeError('not running B3313')
        import macros as MC
        k = int(a.get('count', 1))
        base = MC.B3313_SAVE_FILES + (self.bus.read(MC.B3313_CUR_FILE, 's16') - 1) * MC.B3313_FILE_SIZE
        bits = [(0x26, 1 << s) for s in range(3, 8)] + [(0x27, 1 << s) for s in range(8)]
        b26, b27 = self.bus.read(base + 0x26, 'u8') & 0x07, 0
        for off, m in bits[:k]:
            if off == 0x26:
                b26 |= m
            else:
                b27 |= m
        self.bus.write(base + 0x26, b26, 'u8')
        self.bus.write(base + 0x27, b27, 'u8')
        return {'red_stars': MC.b3313_red_stars(self.g)}

    def op_movie(self, a):
        if a.get('job') == 'play':
            self._auto_game(a['path'])
            inputs = read_m64(a['path'])
            self.g.reset()
            import re
            m = re.search(rb'b3313:red_stars=(\d+)@(\d+)', open(a['path'], 'rb').read(0x400)[0x300:]) if self.b3313 else None
            if m:
                k, at = int(m.group(1)), int(m.group(2))
                self.g.run(inputs[:at])
                self.op_b3313_red_stars({'count': k})
                self.log(f'[b3313] movie: {k} red star(s) from input frame {at}')
                self.g.run(inputs[at:])
            else:
                self.g.run(inputs)
            return {'ok': True}
        return {'ok': True}

    def op_screenshot(self, a):
        raise NotImplementedError('headless: no rendering')

    def op_quit(self, a):
        return {'bye': True}

    def op_hl_history(self, a):
        return {'inputs': self.g.history}

    def op_hl_export(self, a):
        n = write_m64(self.g.history, a['path'])
        return {'path': a['path'], 'frames': n}

    def op_hl_compare(self, a):
        return {'diffs': SB.compare(self.g, a['path'])}

    def op_hl_phantom(self, a):
        if 'value' in a:
            self.phantom_mode = bool(a['value'])
            if not self.phantom_mode:
                self.phantom = False
        return {'phantom': self.phantom_mode}

    def op_pool_start(self, a):
        import hl_pool
        if self.pool:
            self.pool.close()
        m = a.get('milestone')
        inputs = SB.resolve(os.path.abspath(m), game=self.g, log=self.log) if m else [list(x) for x in self.g.history]
        self.pool = hl_pool.Pool(inputs, int(a.get('n') or 0) or None, self.phantom_mode)
        return {'ok': True, 'n': self.pool.n}

    def op_pool_batch(self, a):
        return {'results': self.pool.batch(a['trials'], bool(a.get('all')))}

    def op_pool_cmds(self, a):
        return {'results': self.pool.cmds(a['trials'])}

    def op_pool_stop(self, a):
        if self.pool:
            self.pool.close()
            self.pool = None
        return {'ok': True}

    def handle(self, msg):
        with self.lock:
            return self._handle(msg)

    def _handle(self, msg):
        op = msg.get('op')
        fn = getattr(self, 'op_' + str(op), None)
        try:
            if fn is None:
                raise NotImplementedError(f'unknown command: {op}')
            return {'id': msg.get('id'), 'ok': True, 'result': fn(msg.get('args') or {})}
        except Exception as e:
            self.log(f'[headless] {op} : {e}')
            return {'id': msg.get('id'), 'ok': False, 'err': f'{type(e).__name__}: {e}'}


def serve_tcp(emu, port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', port))
    srv.listen(4)
    emu.log(f'[headless] ready on 127.0.0.1:{port}')
    while True:
        c, _ = srv.accept()
        c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        f = c.makefile('rwb', buffering=0)
        try:
            buf = b''
            while True:
                chunk = c.recv(1 << 20)
                if not chunk:
                    break
                buf += chunk
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line.decode('utf-8-sig'))
                        if not isinstance(msg, dict):
                            raise ValueError('JSON object expected')
                    except ValueError as e:
                        msg = {}
                        r = {'id': None, 'ok': False, 'err': f'invalid JSON: {e}'}
                    else:
                        r = emu.handle(msg)
                    c.sendall((json.dumps(r, separators=(',', ':')) + '\n').encode())
                    if msg.get('op') == 'quit':
                        return
        except (ConnectionError, OSError):
            pass
        finally:
            c.close()


def serve_files(emu, sess):
    os.makedirs(sess, exist_ok=True)
    cmd, resp, status = (os.path.join(sess, n) for n in ('cmd.jsonl', 'resp.jsonl', 'status.json'))
    for p in (cmd, resp):
        open(p, 'w').close()
    pos, hb, t_hb = 0, 0, 0
    emu.log(f'[headless] file session: {sess}')
    buf = b''
    while True:
        now = time.time()
        if now - t_hb > 1:
            hb += 1
            t_hb = now
            with open(status, 'w') as f:
                json.dump({'alive': True, 'hb': hb, 'frame': len(emu.g.history), 'inputs': len(emu.g.history), 'headless': True}, f)
        with open(cmd, 'rb') as f:
            f.seek(pos)
            data = f.read()
        if not data:
            time.sleep(0.001)
            continue
        pos += len(data)
        buf += data
        while b'\n' in buf:
            line, buf = buf.split(b'\n', 1)
            if not line.strip():
                continue
            try:
                msg = json.loads(line.decode('utf-8-sig'))
                if not isinstance(msg, dict):
                    raise ValueError('JSON object expected')
            except ValueError as e:
                msg = {}
                r = {'id': None, 'ok': False, 'err': f'invalid JSON: {e}'}
            else:
                r = emu.handle(msg)
            with open(resp, 'a') as f:
                f.write(json.dumps(r, separators=(',', ':')) + '\n')
            if msg.get('op') == 'quit':
                return


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=0)
    ap.add_argument('--session-dir')
    ap.add_argument('--st', help='.st to load at startup')
    a = ap.parse_args()
    emu = Emu()
    if a.st:
        emu.load_st(a.st)
    if a.session_dir:
        threading.Thread(target=serve_files, args=(emu, a.session_dir), daemon=True).start()
    if a.port:
        serve_tcp(emu, a.port)
    elif a.session_dir:
        while True:
            time.sleep(3600)
