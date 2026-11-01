"""The game DLL frame by frame, as a list of memory checksums: the proof that two platforms run the same game.

Plays a movie from power-on with sm64hl.Game and writes, for every frame, the CRC32 of the game's whole memory (.data and
.bss), Mario's action and the global timer. Run it on Windows (ctypes) and on Linux (winload) and compare the files: one
differing bit anywhere in the game's memory shows as a different line at the first frame it happens.

    python winload_check.py VERSION MOVIE.m64 OUT.txt [--frames N] [--skip RVA:LEN,...]      (VERSION: us or jp)
    python winload_check.py --compare A.txt B.txt

The memory holds absolute pointers: compare two runs with the image at the same base (on Linux WINLOAD_BASE=0x... sets
it; the header line gives each run's base). --skip leaves bytes out of the checksum: the C runtime's own state, which
Windows' DllMain fills and winload never starts (winpthreads' key table, the CRT's atexit list): no game code reads it.

Standard library only (it also runs in the plain Windows embeddable Python, under Wine or not). Prints the frame of the
Grand Star (action 0x1909) when the movie gets there.
"""
import os
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _pieces(ranges, base, skip):
    """the (address, size) pieces of the memory ranges once the skipped RVA ranges are cut out"""
    out = []
    for a, sz in ranges:
        cur = a
        for r, n in sorted(skip):
            s0, s1 = base + r, base + r + n
            if s1 <= cur or s0 >= a + sz:
                continue
            if s0 > cur:
                out.append((cur, s0 - cur))
            cur = max(cur, s1)
        if cur < a + sz:
            out.append((cur, a + sz - cur))
    return out


def run(version, movie, out, frames=None, skip=()):
    import ctypes
    from sm64hl import Game, read_m64
    inputs = read_m64(movie)
    if frames:
        inputs = inputs[:frames]
    g = Game(version=version)
    ranges = _pieces(g._ranges, g.base, skip)
    t0 = time.time()
    star = None
    with open(out, 'w') as f:
        f.write('# %s %s %s %d frames, base 0x%x\n' % (sys.platform, version, os.path.basename(movie), len(inputs), g.base))
        for n, inp in enumerate(inputs, 1):
            g.step(inp)
            c = 0
            for a, sz in ranges:
                c = zlib.crc32(ctypes.string_at(a, sz), c)
            m = g.mario()
            f.write('%d %08x %08x %d\n' % (n, c, m['action'], m['timer']))
            if star is None and m['action'] == 0x1909:
                star = n
    dt = time.time() - t0
    print('%s: %d frames in %.1fs, Grand Star at %s' % (version, len(inputs), dt, star))
    g.close()


def compare(a, b):
    la = [x for x in open(a) if not x.startswith('#')]
    lb = [x for x in open(b) if not x.startswith('#')]
    n = min(len(la), len(lb))
    for i in range(n):
        if la[i] != lb[i]:
            print('DIFFER at line %d:\n  %s  %s' % (i + 1, la[i].strip(), lb[i].strip()))
            return 1
    print('IDENTICAL over %d frames%s' % (n, '' if len(la) == len(lb) else ' (lengths %d / %d)' % (len(la), len(lb))))
    return 0 if len(la) == len(lb) else 1


def main(argv):
    if argv[:1] == ['--compare']:
        return compare(argv[1], argv[2])
    frames = None
    if '--frames' in argv:
        i = argv.index('--frames')
        frames = int(argv[i + 1])
        del argv[i:i + 2]
    skip = []
    if '--skip' in argv:
        i = argv.index('--skip')
        skip = [tuple(int(v, 16) for v in x.split(':')) for x in argv[i + 1].split(',') if x]
        del argv[i:i + 2]
    run(argv[0], argv[1], argv[2], frames, skip)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
