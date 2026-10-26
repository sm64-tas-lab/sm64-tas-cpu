import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import macros as MC

ACT_FLAG_AIR = 0x800
LJ_MIN = 900.0
LJ_REACH = 1500.0
TURN_ARC, TURN_FLIP = 0x1800, 0x6000
CLIMB = 120.0
RADIUS = 30.0
GP_HOLD = 2


def _xz(a, b):
    return math.hypot(b[0] - a[0], b[2] - a[2])


def yaw_to(a, b):
    return int(round(math.atan2(b[0] - a[0], b[2] - a[2]) * 32768 / math.pi)) & 0xFFFF


def _dyaw(a, b):
    return abs(((b - a + 0x8000) & 0xFFFF) - 0x8000)


def _node(p):
    return p if isinstance(p, dict) else {'pos': list(p), 'kind': 'walk'}


def simplify(path, turn_deg=20.0):
    nodes = [_node(p) for p in path]
    if len(nodes) < 3:
        return nodes
    out, prev = [], None
    for i, nd in enumerate(nodes):
        special = nd.get('kind', 'walk') != 'walk' or nd.get('check') or i == len(nodes) - 1
        if not special and prev is not None:
            a, b = prev['pos'], nd['pos']
            c = nodes[i + 1]['pos']
            if _xz(a, b) < 1 or _xz(b, c) < 1 or _dyaw(yaw_to(a, b), yaw_to(b, c)) * 360 / 65536 <= turn_deg:
                continue
        out.append(nd)
        prev = nd
    return out


FAST_FV = 48.0
SPEED_RESET = ('crouch_lj', 'lj_chain', 'lj_again', 'punch_walk_lj', 'blj', 'jump_dive', 'dive_rollout',
               'rollout_kick', 'double_dive', 'triple_dive', 'side_flip_dive')


SLOPE_MIN = 0.5
SLOPE_LEN = 1000.0


def select(state, segment, fast_rule=True, slope_rule=False):
    act = state.get('action')
    act = MC.NAME.get(act, act) if isinstance(act, int) else act
    fv, face = float(state.get('fv', 0.0)), state.get('face', 0)
    a, t = segment.get('from', state['pos']), segment['to']
    L, dy = _xz(a, t), t[1] - a[1]
    turn = _dyaw(face, yaw_to(a, t)) if L > 1 else 0
    kind = segment.get('kind', 'walk')
    if kind == 'switch':
        name, params, why = 'ground_pound', {'hold': GP_HOLD}, 'switch: run in, then the pound (hold 2, jump at ~fv x 4)'
    elif slope_rule and L >= SLOPE_LEN and -dy / L >= SLOPE_MIN:
        name, params, why = 'slope_dive_rollout', {}, f'downhill {-dy:.0f} u over {L:.0f} u: the dive slide builds speed'
    elif fast_rule and fv > FAST_FV and L > LJ_MIN:
        name, params, why = ('slidekick_chain', {'cycles': max(1, int(L // 2000))},
                             f'fv {fv:.0f} > {FAST_FV:g}: slide kick + rollout keep it (a dive / LJ resets to 48)')
    elif act == 'LONG_JUMP_LAND' and L > LJ_MIN / 2:
        name, params, why = 'lj_again', {}, 'already in LONG_JUMP_LAND: Z + A now'
    elif turn > TURN_FLIP and fv >= 16:
        name, params, why = 'side_flip_dive', {}, f'U-turn {turn * 360 / 65536:.0f} deg at fv {fv:.0f}'
    elif turn > TURN_ARC:
        name, params, why = 'arc_turn', {}, f'turn {turn * 360 / 65536:.0f} deg'
    elif dy > CLIMB and L < LJ_MIN:
        name, params, why = 'double_jump', {}, f'climb {dy:.0f} u'
    elif L > 2 * LJ_REACH:
        name, params, why = 'lj_chain', {'cycles': max(1, int(L // LJ_REACH) - 1)}, f'{L:.0f} u straight'
    elif L > LJ_MIN:
        name, params, why = 'crouch_lj', {}, f'{L:.0f} u'
    else:
        return {'macro': 'run', 'params': {}, 'runup': 0.0, 'in_domain': True, 'why': f'{L:.0f} u: run'}
    ok, reason = MC.in_domain(name, act, fv)
    runup = 0.0
    if not ok:
        req = MC.SPECS[MC.resolve(name)].get('require', {})
        if req.get('fv_min') is not None and fv < req['fv_min'] and act in ('WALKING', 'IDLE', 'DECELERATING'):
            runup = max(req['fv_min'] + 0.5, 14.0 if name in ('crouch_lj', 'lj_chain') else 0.0)
            why += f'; run-up to fv {runup:g} first ({reason})'
        else:
            return {'macro': 'run', 'params': {}, 'runup': 0.0, 'in_domain': False, 'why': f'{name} out of domain: {reason}'}
    return {'macro': name, 'params': params, 'runup': runup, 'in_domain': ok, 'why': why}


def path_from_ttg(idx, xyz, w=0, target=None, kind='switch', max_steps=2000, lj_reach=30):
    import numpy as np
    T = idx.TTG_min[idx.index_of(w)]
    c = int(idx.near[idx.cells(np.asarray(xyz, float).reshape(1, 3))[0]])
    pts = []
    for _ in range(max_steps):
        if c < 0 or not np.isfinite(T[c]):
            break
        pts.append([idx.x0 + (c % idx.nx + 0.5) * idx.cell, float(idx.Y[c]), idx.z0 + (c // idx.nx + 0.5) * idx.cell])
        if T[c] <= 0:
            break
        best, bc = T[c], -1
        for o in range(8):
            n = idx.cell_ahead(c, o, 1)
            if n >= 0 and idx.walk[n] and T[n] < best:
                best, bc = T[n], n
        if bc < 0:
            o = idx.next_move(w, pts[-1])[1]
            for k in range(2, lj_reach):
                n = idx.cell_ahead(c, o, k)
                if n >= 0 and idx.walk[n] and T[n] < best:
                    best, bc = T[n], n
            if bc < 0:
                break
        c = bc
    nodes = [{'pos': p, 'kind': 'walk'} for p in pts[1:-1]]
    return nodes + [{'pos': list(target) if target is not None else pts[-1], 'kind': kind}] if pts else nodes


def _subdivide(nodes, start, D):
    out, prev = [], start
    for nd in nodes:
        L = _xz(prev, nd['pos'])
        k = int(L // D)
        for j in range(1, k + 1):
            if L - j * D < D * 0.4:
                break
            f = j * D / L
            out.append({'pos': [prev[c] + (nd['pos'][c] - prev[c]) * f for c in range(3)], 'kind': 'walk'})
        out.append(nd)
        prev = nd['pos']
    return out


def plan_moves(path, state, slope_rule=False, fast_rule=True):
    nodes = simplify(path)
    act = state.get('action')
    act = MC.NAME.get(act, act) if isinstance(act, int) else act
    plan = []
    pos, face, fv = state['pos'], state.get('face', 0), state.get('fv', 0.0)
    for i, nd in enumerate(nodes):
        tgt = nd['pos']
        L, dy = _xz(pos, tgt), tgt[1] - pos[1]
        turn = _dyaw(face, yaw_to(pos, tgt)) if L > 1 else 0
        if i == 0 and act == 'IDLE':
            plan.append({'macro': 'run', 'params': {'stop': 'fv', 'fv': 10.5}, 'node': i, 'why': 'standing start'})
            fv = 12.0
        elif turn > TURN_FLIP and fv >= 16:
            plan.append({'macro': 'side_flip_dive', 'params': {}, 'node': i, 'why': f'U-turn {turn * 360 / 65536:.0f} deg'})
        elif turn > TURN_ARC:
            plan.append({'macro': 'arc_turn', 'params': {}, 'node': i, 'why': f'turn {turn * 360 / 65536:.0f} deg'})
        if nd.get('kind') == 'switch':
            plan.append({'macro': 'run', 'params': {'stop': 'gp'}, 'node': i, 'why': 'approach the switch'})
            plan.append({'macro': 'ground_pound', 'params': {'hold': GP_HOLD}, 'node': i, 'why': 'switch'})
            fv = 9.0
        elif dy > CLIMB and L < LJ_MIN:
            plan.append({'macro': 'double_jump', 'params': {}, 'node': i, 'why': f'climb {dy:.0f} u'})
        elif slope_rule and L >= SLOPE_LEN and -dy / L >= SLOPE_MIN:
            plan.append({'macro': 'slope_dive_rollout', 'params': {}, 'node': i,
                         'why': f'downhill {-dy:.0f} u over {L:.0f} u: the dive slide builds speed'})
            fv = 60.0
        elif fast_rule and fv > FAST_FV and L > LJ_MIN:
            plan.append({'macro': 'slidekick_chain', 'params': {'cycles': max(1, int(L // 2000))}, 'node': i,
                         'why': f'fv {fv:.0f} > {FAST_FV:g}: slide kick + rollout keep it'})
        elif L > 2 * LJ_REACH:
            plan.append({'macro': 'lj_chain', 'params': {'cycles': max(1, int(L // LJ_REACH) - 1)}, 'node': i,
                         'why': f'{L:.0f} u straight'})
            fv = 50.0
        elif L > LJ_MIN:
            plan.append({'macro': 'crouch_lj', 'params': {}, 'node': i, 'why': f'{L:.0f} u'})
            fv = 48.0
        plan.append({'macro': 'run', 'params': {}, 'node': i, 'why': 'reach the node'})
        face, pos = yaw_to(pos, tgt), tgt
    return plan


CANDIDATES = ([('crouch_lj', {'runup': v}) for v in (11, 14, 17)] +
              [('crouch_lj', {'runup': 14, 'mag': m}) for m in (16.0, 24.0)] +
              [('crouch_lj', {'runup': 14, 'off': o}) for o in (-0x800, -0x400, -0x200, 0x200, 0x400, 0x800)] +
              [('crouch_lj', {'runup': 14, 'mag': m}) for m in (28.0, 30.0)] +
              [('crouch_lj', {'runup': 14, 'air': 'strain'}), ('lj_chain', {'cycles': 1, 'runup': 14, 'air': 'strain'})] +
              [('lj_chain', {'cycles': c, 'runup': v}) for c in (1, 2) for v in (14, 17)] +
              [('lj_again', {'cycles': c}) for c in (1, 2)] +
              [('slide_kick', {'runup': 10.5}), ('slidekick_rollout', {'runup': 10.5}), ('jump_speed_kick', {}),
               ('jump_dive', {}), ('dive_rollout', {}), ('rollout_kick', {}), ('double_jump', {}), ('jump_kick', {}),
               ('side_flip', {}), ('side_flip', {'hold': 12}), ('double_dive', {}), ('triple_dive', {}),
               ('dive_rollout', {'cycles': 2}), ('dive_rollout', {'cycles': 3}), ('dive_rollout', {'cycles': 4}),
               ('rollout_kick', {'cycles': 2}),
               ('slidekick_chain', {}), ('slidekick_chain', {'cycles': 2}),
               ('slope_dive_rollout', {}),
               ('run', {})])
ALIGN_CANDIDATES = [('crouch_lj', {'align': 0x200}), ('crouch_lj', {'align': 0x400}),
                    ('crouch_lj', {'align': 0x200, 'air': 'strain'}), ('crouch_lj', {'align': 0x200, 'runup': 14})]
GP_HOLDS = (0, 1, 2, 3, 4)
GP_TRIGGERS = [('lead', k) for k in (1, 2, 3)] + [('dist', d) for d in (100, 140, 180)]
SEG_MAX = 400
COIN_R, COIN_UP, COIN_DOWN = 137.0, 64.0, 160.0


def _reached(runner, nd, p):
    if nd.get('check'):
        return bool(nd['check']())
    kind, t = nd.get('kind', 'walk'), nd['pos']
    if kind == 'coin':
        return _xz(p, t) < COIN_R and t[1] - COIN_DOWN <= p[1] <= t[1] + COIN_UP
    if kind == 'switch':
        return MC.NAME.get(runner.action()) in ('GROUND_POUND_LAND', 'BUTT_SLIDE_STOP') and _xz(p, t) < 100
    if kind == 'land':
        return (not runner.action() & ACT_FLAG_AIR
                and _xz(p, t) < nd.get('r', 120) and abs(p[1] - t[1]) <= nd.get('dy', 50))
    if _xz(p, t) <= RADIUS:
        return True
    f = nd.get('_from')
    if f is None:
        return False
    dx, dz = t[0] - f[0], t[2] - f[2]
    return (p[0] - t[0]) * dx + (p[2] - t[2]) * dz > 0 and _xz(p, t) < 400


def _attempt(runner, pos, nd, name, params, target_check, cap, then=None):
    t, out = nd['pos'], []

    def ok():
        return (target_check is not None and target_check()) or _reached(runner, nd, pos())

    def run_until(cond, limit):
        while len(out) < limit and not ok() and not cond():
            out.append(runner.press(yaw_to(pos(), t), 0))

    if name == 'solve_kick':
        g, S = runner.g, runner.g.save()
        met = False
        import local_solver as LS
        for x, y in LS.coarse_sticks():
            g.load(S)
            for k in range(params.get('frames', 3) + 1):
                g.step((x, y, MC.A if k % 2 else 0, 0))
                if MC.NAME.get(runner.action()) in ('AIR_HIT_WALL', 'SOFT_BONK', 'WALL_KICK_AIR'):
                    met = True
                    break
            if met:
                break
        g.load(S)
        if not met:
            return out, False
        n = int(params.get('frames', 3))
        r = LS.solve(g, LS.Cond('action', ['WALL_KICK_AIR']), frames=n, buttons=[['', 'A']] * (n - 1) + [['A']],
                     sticks=['coarse'] * n, beam=int(params.get('beam', 64)), procs=0,
                     score=LS.Score('action', {'AIR_HIT_WALL': 2, 'SOFT_BONK': 1, 'JUMP': 1}),
                     best=LS.Score('toward', [t[0], t[2]]))
        if not r['found']:
            return out, False
        for f in r['inputs']:
            g.step(f)
            out.append(tuple(f))
        while len(out) < cap and not ok():
            out.append(runner.press(yaw_to(pos(), t), MC.A if runner.action() & ACT_FLAG_AIR else 0))
        return out, ok()
    if name == 'gp':
        hold = params['hold']
        kind, k = params.get('trigger', ('lead', 2))
        trig = (lambda: runner.fv() * (hold + k)) if kind == 'lead' else (lambda: float(k))
        run_until(lambda: MC.NAME.get(runner.action()) in ('WALKING', 'IDLE', 'DECELERATING')
                  and runner.squish() == 0 and _xz(pos(), t) <= max(20.0, trig()), cap)
        p = MC.Plan('ground_pound', hold=hold)
    elif name == 'run':
        p = None
    else:
        req = MC.SPECS[name].get('require', {})
        runup = float(params.get('runup', 0.0))
        align = params.get('align')
        pound = params.get('pound')
        params = {k: v for k, v in params.items() if k not in ('runup', 'align', 'pound')}
        bz = any(c in ph.get('btn', '') for ph in MC.SPECS[name]['phases'] for c in 'BZ')
        if req.get('fv_min') or runup or align or 'IDLE' not in req.get('actions', ['IDLE']) or bz:
            fmin = max(req.get('fv_min', 0.0) + 0.5, runup)

            def faced():
                return align is None or abs(((yaw_to(pos(), t) - runner.face() + 0x8000) & 0xFFFF) - 0x8000) <= align
            run_until(lambda: MC.NAME.get(runner.action()) in ('WALKING', 'IDLE') and runner.fv() >= fmin
                      and (not bz or runner.squish() == 0) and faced(), min(cap, len(out) + 40))
        if not MC.in_domain(name, runner.action(), runner.fv())[0]:
            return out, ok()
        p = MC.Plan(name, **params)
    while p is not None and len(out) < cap and not ok():
        if (name != 'gp' and pound and nd.get('kind') == 'coin' and runner.action() & ACT_FLAG_AIR
                and MC.NAME.get(runner.action()) != 'LONG_JUMP'
                and _xz(pos(), t) < pound and pos()[1] > t[1] + COIN_UP):
            out.append(runner.press(None, MC.Z))
            while len(out) < cap and not ok() and runner.action() & ACT_FLAG_AIR:
                out.append(runner.press(None, 0))
            break
        r = p.step(runner.action(), runner.face(), yaw_to(pos(), t), runner.fv())
        if r is None:
            break
        out.append(runner.press(r[1], r[0], r[2]))
    if name == 'gp':
        return out, ok()
    run_until(lambda: False, cap)
    if then is not None and ok():
        while len(out) < cap and runner.action() & ACT_FLAG_AIR and not (target_check is not None and target_check()):
            out.append(runner.press(yaw_to(pos(), then['pos']), 0))
    if nd.get('kind') == 'coin' and _reached(runner, nd, pos()):
        for _ in range(2):
            if target_check is not None and target_check():
                break
            out.append(runner.press(yaw_to(pos(), t), 0))
    return out, ok()


def play_plan(runner, plan, target_check=None, path=None, max_frames=2000, pos_fn=None, log=None, search=True,
              lookahead=True, beam=5, land_before_switch=False, simplify_path=True, subdivide=None,
              per_parent=None, dedupe=True, align_lj=False, candidates=None, switch_clear=150.0,
              squish_cost=True, fast_rule=True, kick_solver=False, attempts=None):
    """attempts: None (each attempt on this runner's game, in turn) or a function (jobs, run_job) -> results that plays
    the same attempts elsewhere, e.g. on GPU games (gpu_romhack/gpu_attempts.py); run_job(runner, job) is this planner's
    own attempt (_attempt) and its measures, on whatever game the runner plays."""
    nodes = simplify(path or []) if simplify_path else [_node(p) for p in (path or [])]
    nodes = [dict(n) for n in nodes]
    if switch_clear:
        nodes = [n for i, n in enumerate(nodes)
                 if not (n.get('kind', 'walk') == 'walk' and i + 1 < len(nodes) and nodes[i + 1].get('kind') == 'switch'
                         and _xz(n['pos'], nodes[i + 1]['pos']) < switch_clear)]
    if subdivide:
        nodes = _subdivide(nodes, list(pos_fn() if pos_fn else runner.pos()), float(subdivide))
    if nodes and target_check is not None and not nodes[-1].get('check'):
        nodes[-1] = dict(nodes[-1], check=target_check)
    pos = pos_fn or runner.pos
    g = runner.g
    inputs, steps = [], []

    def done():
        return target_check is not None and target_check()

    if not search:
        return _play_steps(runner, plan, nodes, pos, done, max_frames, log)
    by_node = {}
    for st in plan:
        by_node.setdefault(st['node'], []).append(st)
    start = g.save()
    beams = [((0, 0.0, 0.0), [], [], start, True)]
    for i, nd in enumerate(nodes):
        nd['_from'] = None if i == 0 else nodes[i - 1]['pos']
        first = [s for s in by_node.get(i, []) if s['macro'] not in ('run',)]
        if nd.get('kind') == 'switch':
            cands = [('gp', {'hold': h, 'trigger': tr}) for h in GP_HOLDS for tr in GP_TRIGGERS]
        else:
            cands = ([(s['macro'], s['params']) for s in first if s['macro'] in MC.SPECS]
                     + (CANDIDATES if candidates is None else list(candidates)) + (ALIGN_CANDIDATES if align_lj else [])
                     + ([('solve_kick', {'frames': 3})] if kick_solver else []))
        nxt = nodes[i + 1]['pos'] if i + 1 < len(nodes) and lookahead else None
        new = []
        jobs, tried_of = [], {}
        for pi, (_, inp0, st0, S, ok0) in enumerate(beams):
            g.load(S)
            if i == 0:
                nd['_from'] = list(pos())
            if done() or len(inp0) >= max_frames:
                new.append(((0 if ok0 else 1, len(inp0), 0.0), inp0, st0, S, ok0, None, None, None, pi, ('done', pi)))
                continue
            tried_of[pi] = []
            fast = fast_rule and runner.fv() > FAST_FV
            for name, params in cands:
                if fast and name in SPEED_RESET:
                    continue
                cap = min(SEG_MAX, max_frames - len(inp0))
                then = (nodes[i + 1] if land_before_switch and i + 1 < len(nodes) and nodes[i + 1].get('kind') == 'switch'
                        else None)
                jobs.append({'pi': pi, 'S': S, 'inp0': inp0, 'name': name, 'params': params, 'cap': cap, 'then': then,
                             'slot': len(new)})
                new.append(None)                       # its place: the same order as one attempt after the other

        def run_job(rn, job, nd=nd):
            p_ = pos_fn or rn.pos
            out, ok = _attempt(rn, p_, nd, job['name'], job['params'], target_check, job['cap'], job['then'])
            return out, ok, {'pos': list(p_()), 'fv': rn.fv(), 'squish': rn.squish(), 'action': rn.action(),
                             'done': done()}
        if attempts is not None:
            results = attempts(jobs, run_job)
        else:
            results = []
            for job in jobs:
                g.load(job['S'])
                results.append(run_job(runner, job))
        for job, (out, ok, m) in zip(jobs, results):
            pi, name, params, inp0 = job['pi'], job['name'], job['params'], job['inp0']
            st0, S, ok0 = beams[pi][2], beams[pi][3], beams[pi][4]
            tried = tried_of[pi]
            tried.append((name, params, len(out) if ok else None))
            v = (25.0 if nodes[i + 1].get('kind') == 'switch' else max(10.0, abs(m['fv']))) if nxt else 1.0
            eta = _xz(m['pos'], nxt) / v if nxt and not m['done'] else 0.0
            if squish_cost and nxt and not m['done']:
                eta += m['squish']
            good = ok and ok0
            key = (0 if good else 1, len(inp0) + len(out) + eta if good else _xz(m['pos'], nd['pos']), -m['fv'])
            sig = (len(inp0) + len(out), tuple(round(c, 1) for c in m['pos']), round(m['fv'], 3), m['action'])
            step = {'node': i, 'kind': nd.get('kind', 'walk'), 'macro': name, 'params': params,
                    'frames': len(out), 'reached': ok, 'tried': tried}
            new[job['slot']] = (key, inp0 + out, st0 + [step], None, good, name, params, S, pi, sig)
        new.sort(key=lambda b: b[0])
        sel, per, seen = [], {}, set()
        for b in new:
            if len(sel) >= beam:
                break
            if per_parent and per.get(b[8], 0) >= per_parent:
                continue
            if dedupe and b[9] in seen:
                continue
            seen.add(b[9])
            per[b[8]] = per.get(b[8], 0) + 1
            sel.append(b)
        kept = []
        for b in sel:
            if b[3] is None:
                g.load(b[7])
                for f in b[1][len(b[1]) - b[2][-1]['frames']:]:
                    g.step(f)
                S2 = g.save()
            else:
                S2 = b[3]
            kept.append((b[0], b[1], b[2], S2, b[4]))
        beams = kept
        if log and kept:
            log(kept[0][2][-1] if kept[0][2] else None)
    best = beams[0]
    g.load(start)
    for f in best[1]:
        g.step(f)
    inputs, steps = best[1], best[2]
    return inputs, {'frames': len(inputs), 'reached': done(), 'steps': steps}


def _play_steps(runner, plan, nodes, pos, done, max_frames, log):
    inputs, steps = [], []
    for st in plan:
        if len(inputs) >= max_frames or done():
            break
        tgt = nodes[st['node']]['pos'] if nodes else None
        name, t0 = st['macro'], len(inputs)
        if name != 'run':
            ok, why = MC.in_domain(name, runner.action(), runner.fv())
            if not ok:
                steps.append({'macro': name, 'skipped': why, 'frame': t0})
                name = 'run'
        if name == 'run':
            while len(inputs) < max_frames and not done():
                p = pos()
                d = _xz(p, tgt)
                if st['params'].get('stop') == 'gp':
                    if d <= max(30.0, runner.fv() * (GP_HOLD + 2)):
                        break
                elif st['params'].get('stop') == 'fv':
                    if runner.fv() >= st['params']['fv']:
                        break
                elif d <= RADIUS:
                    break
                inputs.append(runner.press(yaw_to(p, tgt), 0))
            steps.append({'macro': 'run', 'node': st['node'], 'frames': len(inputs) - t0})
            continue
        p = MC.Plan(name, **st['params'])
        while len(inputs) < max_frames and not done():
            aim = yaw_to(pos(), tgt) if tgt else runner.face()
            r = p.step(runner.action(), runner.face(), aim, runner.fv())
            if r is None:
                break
            inputs.append(runner.press(r[1], r[0], r[2]))
        steps.append({'macro': name, 'node': st['node'], 'frames': len(inputs) - t0, 'status': p.status,
                      'why': p.why})
        if log:
            log(steps[-1])
    return inputs, {'frames': len(inputs), 'reached': done(), 'steps': steps}
