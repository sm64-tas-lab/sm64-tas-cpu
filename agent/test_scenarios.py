"""The tools played the way a model plays them, on real cases that once broke them (Qwen's runs, 2026-10-28).

    python agent/test_scenarios.py          (a few minutes; one line a check, a non-zero exit if one fails)
"""
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[0:0] = [HERE, os.path.join(os.path.dirname(HERE), 'headless')]
import sm64_headless_mcp as S  # noqa: E402

FAILS = []


def check(name, ok, info=''):
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, (': %s' % info) if info else ''), flush=True)
    if not ok:
        FAILS.append(name)


def to_hall(P):
    r = P.t_goto({'x': 0, 'z': -3155, 'style': 'longjump'})
    check('goto stopped by a text says blocked at the top', r.get('blocked') is True, r['why'])
    P.t_close_dialog({})
    r = P.t_goto({'x': 0, 'z': -3155})
    check('the castle door: the level changed', r['why'] == 'the level changed', r['why'])
    s = P.t_close_dialog({})
    check('in the hall, its text closed', s['level_name'] == 'Castle inside (ground floor)' and not s.get('blocked'),
          s['level_name'])
    n = P.t_close_dialog({})
    check('close_dialog with nothing to close plays nothing', n['frames_played'] == 0, n['frames_played'])


def into_bob(P):
    w = P.t_warps({'to': 'bob-omb'})['warps']
    check('warps finds the Bob-omb painting', bool(w) and w[0]['kind'] == 'painting' and 'front' in w[0])
    w = w[0]
    pr = P.t_path({'x': w['front']['x'], 'z': w['front']['z']})
    check('path to the painting goes through the hall doors', pr.get('found') and len(pr['turns']) > 1)
    for t in pr['turns']:
        P.t_goto({'x': t['x'], 'z': t['z'], 'stop': 120 if t is not pr['turns'][-1] else 50})
    P.t_walk({'x': w['x'], 'z': w['z'], 'frames': 14, 'buttons': ['A']})
    s = P.t_close_dialog({})
    check('a jump into the painting: Bob-omb Battlefield, Mario free', s['level_name'] == 'Bob-omb Battlefield'
          and s['action_name'] == 'IDLE', '%s %s at frame %d' % (s['level_name'], s['action_name'], s['frames']))


def coins(P):
    P.t_checkpoint({'name': 'bob'})
    got = 0
    for c in [(4934, 1352, 861), (-5500, 768, 2400), (-6500, 768, 2400)]:
        pr = P.t_path({'x': c[0], 'z': c[2], 'y': c[1]})
        took = {}
        for t in pr.get('turns', []):
            r = P.t_goto({'x': t['x'], 'z': t['z'], 'y': t['y'], 'frames': 1500,
                          'stop': 120 if t is not pr['turns'][-1] else 50})
            for k, v in (r.get('took') or {}).items():
                took[k] = took.get(k, 0) + v
        if not took.get('red_coins'):
            r = P.t_collect({'name': 'RedCoin', 'near_x': c[0], 'near_z': c[2]})
            took = r.get('took') or {}
        got += took.get('red_coins', 0)
    s = P.t_state({})
    check('three red coins on foot one after the other (path + goto), took says so', s['red_coins'] == 3 and got == 3,
          'red_coins %d, took %d' % (s['red_coins'], got))
    P.t_restore({'name': 'bob'})
    cage = P.t_path({'x': -2755, 'z': -4083, 'y': 0})
    check('path sees the cage gate (an object): no straight way through it', not (cage.get('found') and
          len(cage.get('turns', [])) <= 5 and all(-2400 < t['x'] < -1700 for t in cage['turns'][-2:-1])))
    isl = P.t_path({'x': 4100, 'z': 1629, 'y': 3672})
    check('the floating island coin: floor_under says no walk reaches it', isl.get('floor_under') == 3072,
          isl.get('floor_under'))
    post = P.t_path({'x': 250, 'z': 1920, 'y': 1000})
    check('the coin on the post: the nearest reachable point is given', 'nearest' in post,
          (post.get('nearest') or {}).get('left'))
    col = P.t_collect({'name': 'RedCoin', 'near_x': 0, 'near_z': 0})
    check('collect far from any red coin says not there', col['collected'] is False and 'why' in col)
    for t in [(-5575, 5675), (-2975, 3075), (-1275, 1075), (225, -425), (225, -475), (-25, -725)]:
        P.t_goto({'x': t[0], 'z': t[1], 'stop': 120})
    sl = P.t_goto({'x': 1325, 'z': -1525, 'frames': 1500})
    check('up a slope too steep: goto says slid back', sl['why'].startswith('slid'), sl['why'])


def cli_bom():
    d = tempfile.mkdtemp()
    f = os.path.join(d, 'run.txt')
    open(f, 'wb').write(b'\xef\xbb\xbfstart\nstate\n')
    out = subprocess.run([sys.executable, os.path.join(HERE, 'play.py'), f], capture_output=True, text=True).stdout
    check('play.py reads a file with a BOM', out.startswith('start -> {"pos_x"'), out[:60])


def main():
    t0 = time.time()
    P = S.Play()
    P.t_start({})
    to_hall(P)
    into_bob(P)
    coins(P)
    cli_bom()
    print('%d check(s) failed, %.0f s' % (len(FAILS), time.time() - t0))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
