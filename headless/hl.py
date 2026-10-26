import argparse, json, struct, sys, time
from sm64hl import Game, read_inputs, read_m64, write_m64, fmt


def load_inputs(paths):
    out = []
    for p in paths:
        if p.startswith('+') and p[1:].isdigit():
            out += [(0, 0, 0)] * int(p[1:])
        elif p.lower().endswith('.m64'):
            out += read_m64(p)
        else:
            out += read_inputs(p)
    return out


def mupen_st(path):
    raw = open(path, 'rb').read()
    if raw[:4] == b'\x04\x22\x4d\x18':
        import lz4.frame
        d = lz4.frame.decompress(raw)
    else:
        import gzip
        d = gzip.decompress(raw)
    rd32 = lambda a: struct.unpack_from('<I', d, 0x1B0 + (a & 0x7FFFFF))[0]
    f32 = lambda a: struct.unpack('<f', struct.pack('<I', rd32(a)))[0]
    s16 = lambda a: struct.unpack('<h', struct.pack('<H', (rd32(a & ~3) >> (0 if a & 2 else 16)) & 0xFFFF))[0]
    MS = 0x8033B170
    cam = rd32(rd32(MS + 0x90) + 0x24)
    return {'timer': rd32(0x8032D5D4), 'pos_x': f32(MS + 0x3C), 'pos_y': f32(MS + 0x40), 'pos_z': f32(MS + 0x44),
            'forward_vel': f32(MS + 0x54), 'action': rd32(MS + 0x0C), 'face_yaw': s16(MS + 0x2E),
            'level': s16(0x8032DDF8), 'rng': rd32(0x8038EEE0) >> 16, 'cam_yaw': s16(cam + 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['run', 'm64', 'check', 'bench'])
    ap.add_argument('inputs', nargs='*')
    ap.add_argument('-o', '--out')
    ap.add_argument('--st')
    ap.add_argument('--trace')
    ap.add_argument('--save')
    ap.add_argument('--from', dest='frm')
    a = ap.parse_intermixed_args()
    inp = load_inputs(a.inputs)

    if a.cmd == 'm64':
        n = write_m64(inp, a.out or 'movie.m64')
        print(f'{a.out}: {n} frames since power-on')
        return

    if a.cmd == 'bench':
        g = Game()
        t = time.perf_counter(); n = 0
        while time.perf_counter() - t < 2:
            g.step((0, 0, 0)); n += 1
        fps = n / (time.perf_counter() - t)
        s = g.save(); t = time.perf_counter()
        for _ in range(200):
            g.load(s)
        print(f'{fps:.0f} frames/s ; savestate load {1000 * (time.perf_counter() - t) / 200:.2f} ms')
        return

    g = Game()
    if a.frm:
        g.load_file(a.frm)
    t = time.perf_counter()
    if a.trace:
        with open(a.trace, 'w') as f:
            f.write('frame,stick_x,stick_y,btn,pos_x,pos_y,pos_z,forward_vel,face_yaw,action,level,rng,cam_yaw\n')
            for i, x in enumerate(inp):
                g.step(x); s = g.mario()
                f.write(f"{i},{x[0]},{x[1]},{x[2]},{s['pos_x']},{s['pos_y']},{s['pos_z']},{s['forward_vel']},"
                        f"{s['face_yaw']},{s['action']:X},{s['level']},{s['rng']},{s['cam_yaw']}\n")
    else:
        g.run(inp)
    dt = time.perf_counter() - t
    m = g.mario()
    print(f'{len(inp)} frames in {dt * 1000:.0f} ms ({len(inp) / max(dt, 1e-9):.0f} f/s)')
    print(fmt(m))
    if a.save:
        g.save_file(a.save); print('headless savestate ->', a.save)
    if a.cmd == 'check':
        r = mupen_st(a.st)
        keys = ['timer', 'pos_x', 'pos_y', 'pos_z', 'forward_vel', 'action', 'face_yaw', 'level', 'rng', 'cam_yaw']
        bad = [k for k in keys if r[k] != m[k]]
        for k in keys:
            print(f"  {k:12} headless={m[k]!s:22} mupen={r[k]!s:22} {'' if r[k] == m[k] else '<-- DIFF'}")
        print('IDENTICAL' if not bad else 'DIFFERENT: ' + ', '.join(bad))
        sys.exit(0 if not bad else 1)


if __name__ == '__main__':
    main()
