import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

with open(os.path.join(HERE, '_actions.json'), encoding='utf-8') as _f:
    _T = json.load(_f)
ACT = _T['actions']
FLAG = _T['flags']
NAME = {v: k for k, v in ACT.items()}

B3313_ALIASES = {'SLIDE_KICK': [0x018008A5], 'SLIDE_KICK_SLIDE': ['BUTT_SLIDE'], 'TRIPLE_JUMP': ['TWIRLING'],
                 'TRIPLE_JUMP_LAND': ['TWIRL_LAND'],
                 'BACKFLIP_LAND': ['DOUBLE_JUMP_LAND'], 'BACKFLIP_LAND_STOP': ['DOUBLE_JUMP_LAND_STOP']}
B3313_IMPOSSIBLE = {'triple_dive': 'no dive out of the twirl (B3313 triple jump = TWIRLING)',
                    'slidekick_rollout': 'no rollout out of the slide kick landing (BUTT_SLIDE ignores A/B/Z in B3313)',
                    'slidekick_chain': 'no rollout out of the slide kick landing (BUTT_SLIDE ignores A/B/Z in B3313)'}
B3313_DIVE_SLIDE_WINDOW = 4
B3313_MODE = False
for _n, _vs in B3313_ALIASES.items():
    for _v in _vs:
        if isinstance(_v, int):
            NAME.setdefault(_v, _n)
B3313_UNLOCK = {'LONG_JUMP': 1, 'SLIDE_KICK': 1, 'BACKFLIP': 2, 'PUNCHING': None}
B3313_PARAMS = {'jump_dive': {'hold': 3}}
B3313_SAVE_FILES, B3313_FILE_SIZE, B3313_CUR_FILE = 0x8020770C, 0x4C, 0x8032DDF4


def b3313_red_stars(g):
    base = B3313_SAVE_FILES + (g.read(B3313_CUR_FILE, 'h') - 1) * B3313_FILE_SIZE
    return bin(g.read(base + 0x26, 'B') & 0xF8).count('1') + bin(g.read(base + 0x27, 'B')).count('1')


def b3313_spec(name):
    spec = SPECS[resolve(name)]
    if resolve(name) == 'slope_dive_rollout':
        spec = dict(spec, phases=[dict(p, min=0, max=B3313_DIVE_SLIDE_WINDOW) if 'while_fv' in p else p
                                  for p in spec['phases']])
    return spec


def b3313_locked(name, red_stars):
    if resolve(name) in B3313_IMPOSSIBLE:
        return B3313_IMPOSSIBLE[resolve(name)]
    for ph in SPECS[resolve(name)]['phases']:
        for a in ph.get('expect', []):
            if a in B3313_UNLOCK:
                need = B3313_UNLOCK[a]
                if need is None:
                    return f'{a} does not exist in B3313'
                if red_stars < need:
                    return f'{a} needs {need} red star(s) in B3313 (save has {red_stars})'
    return None


A, B, Z = 0x80, 0x40, 0x20
BTN = {'A': A, 'B': B, 'Z': Z}

SPECS = {
    'dive_rollout': {
        'doc': 'dive -> dive slide -> forward rollout (A on the 1st DIVE_SLIDE frame); `cycles` times, A+B out of the '
               'landing starts the next dive',
        'task6_ok': True, 'src': 'SM64 Island 696 T 41-42, 75-86', 'params': {'cycles': 1},
        'require': {'fv_min': 29.0},
        'phases': [
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['DIVE']},
            {'until': ['DIVE_SLIDE'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['FORWARD_ROLLOUT']},
            {'until': ['FREEFALL_LAND_STOP'], 'btn': '', 'stick': 'aim', 'max': 60},
            {'btn': 'AB', 'stick': 'aim', 'frames': 1, 'expect': ['DIVE'], 'last_skip': True},
        ],
        'repeat': {'from': 1, 'times': 'cycles'}},
    'rollout_kick': {
        'doc': 'dive -> rollout with A HELD, then B new on FREEFALL_LAND_STOP (A still held, stick neutral) -> JUMP_KICK, '
               'A on the landing -> DOUBLE_JUMP, B -> DIVE; `cycles` times',
        'task6_ok': True, 'src': 'SM64 Island 696 T 101-102, 188-201; vanilla (Task 6 688 rows 193 / 277 / 375)',
        'params': {'cycles': 1}, 'require': {'fv_min': 29.0},
        'phases': [
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['DIVE']},
            {'until': ['DIVE_SLIDE'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['FORWARD_ROLLOUT']},
            {'until': ['FREEFALL_LAND_STOP'], 'btn': 'A', 'stick': 'aim', 'max': 60},
            {'btn': 'AB', 'stick': 'neutral', 'frames': 1, 'expect': ['JUMP_KICK']},
            {'until': ['FREEFALL_LAND'], 'btn': '', 'stick': 'aim', 'max': 40},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['DOUBLE_JUMP']},
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['DIVE'], 'last_skip': True},
        ],
        'repeat': {'from': 1, 'times': 'cycles'}},
    'wall_kick': {
        'doc': 'facing kept until AIR_HIT_WALL / SOFT_BONK, a new A press every other frame (the kick window) -> '
               'WALL_KICK_AIR, stick toward `aim` for `hold` frames, then B -> DIVE',
        'task6_ok': True, 'src': 'SM64 Island 696 T 497-510 (AIR_HIT_WALL), T 254-263 (SOFT_BONK)', 'params': {'hold': 2},
        'phases': [
            {'until': ['AIR_HIT_WALL', 'SOFT_BONK'], 'btn': '', 'stick': 'keep0', 'max': 40},
            {'until': ['WALL_KICK_AIR'], 'btn': 'alt:A', 'stick': 'keep0', 'max': 6},
            {'btn': 'A', 'stick': 'aim', 'frames': 'param:hold'},
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['DIVE']},
        ]},
    'side_flip': {
        'doc': 'stick reversed (facing + 180 deg) while running -> TURNING_AROUND, A toward `aim` -> SIDE_FLIP, hold, '
               'then B -> DIVE',
        'task6_ok': True, 'src': 'SM64 Island 696 T 144-166, 358-369', 'params': {'hold': 5},
        'require': {'fv_min': 16.0},
        'phases': [
            {'until': ['TURNING_AROUND'], 'btn': '', 'stick': 'back0', 'max': 6},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['SIDE_FLIP']},
            {'btn': '', 'stick': 'aim', 'frames': 'param:hold'},
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['DIVE']},
        ]},
    'crouch_lj': {
        'doc': 'running: Z -> CROUCH_SLIDE, A on its 1st frame -> LONG_JUMP, stick toward `aim` until LONG_JUMP_LAND',
        'task6_ok': True, 'src': 'census crouch_lj / walk_crouch_lj (Task 6 686 T 323-365)', 'require': {'fv_min': 10.0},
        'params': {'wait': 0},
        'phases': [
            {'btn': 'Z', 'stick': 'aim', 'frames': 1, 'expect': ['CROUCH_SLIDE']},
            {'btn': '', 'stick': 'aim', 'frames': 'param:wait', 'expect': ['CROUCH_SLIDE']},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['LONG_JUMP']},
            {'until': ['LONG_JUMP_LAND'], 'btn': '', 'stick': 'aim', 'max': 80},
        ]},
    'lj_chain': {
        'doc': 'crouch_lj, then Z + A on the 1st LONG_JUMP_LAND frame -> a new LONG_JUMP; `cycles` landings',
        'task6_ok': True, 'src': 'census lj_land_lj (Task 6 686 T 622-651)', 'require': {'fv_min': 10.0},
        'params': {'wait': 0, 'land': 0, 'cycles': 1},
        'phases': [
            {'btn': 'Z', 'stick': 'aim', 'frames': 1, 'expect': ['CROUCH_SLIDE']},
            {'btn': '', 'stick': 'aim', 'frames': 'param:wait', 'expect': ['CROUCH_SLIDE']},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['LONG_JUMP']},
            {'until': ['LONG_JUMP_LAND'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'btn': '', 'stick': 'aim', 'frames': 'param:land', 'expect': ['LONG_JUMP_LAND']},
            {'btn': 'ZA', 'stick': 'aim', 'frames': 1, 'expect': ['LONG_JUMP']},
            {'until': ['LONG_JUMP_LAND'], 'btn': '', 'stick': 'aim', 'max': 80},
        ],
        'repeat': {'from': 4, 'times': 'cycles'}},
    'crouch_slide': {
        'doc': 'running: Z -> CROUCH_SLIDE, Z held with the stick toward `aim` for `hold` frames (the building block of '
               'the LJ and the slide kick)',
        'task6_ok': True, 'src': 'census WALKING -> CROUCH_SLIDE', 'params': {'hold': 1},
        'phases': [
            {'btn': 'Z', 'stick': 'aim', 'frames': 1, 'expect': ['CROUCH_SLIDE']},
            {'btn': 'Z', 'stick': 'aim', 'frames': 'param:hold', 'expect': ['CROUCH_SLIDE']},
        ]},
    'punch_walk': {
        'doc': 'standing: B -> PUNCHING, stick toward `aim` -> WALKING (a start from idle)',
        'task6_ok': True, 'src': 'census punch_walk (0star_complet T 10364)', 'require': {'actions': ['IDLE']},
        'phases': [
            {'btn': 'B', 'stick': 'neutral', 'frames': 1, 'expect': ['PUNCHING']},
            {'until': ['WALKING'], 'btn': '', 'stick': 'aim', 'max': 10},
        ]},
    'punch_walk_lj': {
        'doc': 'standing: punch_walk, `hold` walking frames, then Z -> CROUCH_SLIDE, A -> LONG_JUMP until the landing',
        'task6_ok': True, 'src': 'census punch_walk_lj (0star_complet T 6139-6174)', 'require': {'actions': ['IDLE']},
        'params': {'wait': 0, 'hold': 5},
        'phases': [
            {'btn': 'B', 'stick': 'neutral', 'frames': 1, 'expect': ['PUNCHING']},
            {'until': ['WALKING'], 'btn': '', 'stick': 'aim', 'max': 10},
            {'btn': '', 'stick': 'aim', 'frames': 'param:hold'},
            {'btn': 'Z', 'stick': 'aim', 'frames': 1, 'expect': ['CROUCH_SLIDE']},
            {'btn': '', 'stick': 'aim', 'frames': 'param:wait', 'expect': ['CROUCH_SLIDE']},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['LONG_JUMP']},
            {'until': ['LONG_JUMP_LAND'], 'btn': '', 'stick': 'aim', 'max': 80},
        ]},
    'jump_dive': {
        'doc': 'running (fv > 28): A -> JUMP, A held `hold` frames, B -> DIVE, until the belly landing (DIVE_SLIDE)',
        'task6_ok': True, 'src': 'census jump_dive (Island 780 T 24-55)', 'require': {'fv_min': 28.0},
        'params': {'hold': 0},
        'phases': [
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['JUMP']},
            {'btn': 'A', 'stick': 'aim', 'frames': 'param:hold'},
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['DIVE']},
            {'until': ['DIVE_SLIDE'], 'btn': '', 'stick': 'aim', 'max': 80},
        ]},
    'jump_dive_rollout': {
        'doc': 'the speed start from (nearly) standing: A -> JUMP, A held until fv 29, B -> DIVE in the air (a high '
               'fv right away), until DIVE_SLIDE, A -> FORWARD_ROLLOUT until FREEFALL_LAND_STOP: from there dive_rollout '
               'cycles climb past 48 without walking, then slidekick_chain keeps and adds speed',
        'task6_ok': True, 'src': 'B3313 0.7 1 Star record, level 9 corridor (frames 776-819: fv 8 -> 46 -> 49.7)',
        'params': {},
        'phases': [
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['JUMP']},
            {'until_fv': 29.0, 'btn': 'A', 'stick': 'aim', 'max': 40},   # B dives only from 28 up (a kick below)
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['DIVE']},
            {'until': ['DIVE_SLIDE'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['FORWARD_ROLLOUT']},
            {'until': ['FREEFALL_LAND_STOP'], 'btn': '', 'stick': 'aim', 'max': 60},
        ]},
    'jump_kick': {
        'doc': 'running at fv <= 28: A -> JUMP, A held `hold` frames, B -> JUMP_KICK, until the landing',
        'task6_ok': True, 'src': 'census jump_kick (f14_1key_jp T 6532-6550)', 'require': {'fv_max': 28.0},
        'params': {'hold': 13},
        'phases': [
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['JUMP']},
            {'btn': 'A', 'stick': 'aim', 'frames': 'param:hold'},
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['JUMP_KICK']},
            {'until': ['FREEFALL_LAND', 'FREEFALL_LAND_STOP'], 'btn': '', 'stick': 'aim', 'max': 80},
        ]},
    'double_jump': {
        'doc': 'running: A -> JUMP, A again on the 1st JUMP_LAND frame -> DOUBLE_JUMP, until DOUBLE_JUMP_LAND',
        'task6_ok': True, 'src': 'census JUMP_LAND -> DOUBLE_JUMP (1 f)',
        'params': {'land': 0, 'air': 'strain'},
        'phases': [
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['JUMP']},
            {'until': ['JUMP_LAND'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'btn': '', 'stick': 'aim', 'frames': 'param:land', 'expect': ['JUMP_LAND']},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['DOUBLE_JUMP']},
            {'until': ['DOUBLE_JUMP_LAND'], 'btn': '', 'stick': 'aim', 'max': 80},
        ]},
    'triple_jump': {
        'doc': 'double_jump, then A on the 1st DOUBLE_JUMP_LAND frame -> TRIPLE_JUMP (fv > 20), until TRIPLE_JUMP_LAND',
        'task6_ok': True, 'src': 'census DOUBLE_JUMP_LAND -> TRIPLE_JUMP (1 f)', 'require': {'fv_min': 20.0},
        'params': {'land': 0, 'air': 'strain'},
        'phases': [
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['JUMP']},
            {'until': ['JUMP_LAND'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'btn': '', 'stick': 'aim', 'frames': 'param:land', 'expect': ['JUMP_LAND']},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['DOUBLE_JUMP']},
            {'until': ['DOUBLE_JUMP_LAND'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'btn': '', 'stick': 'aim', 'frames': 'param:land', 'expect': ['DOUBLE_JUMP_LAND']},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['TRIPLE_JUMP']},
            {'until': ['TRIPLE_JUMP_LAND'], 'btn': '', 'stick': 'aim', 'max': 100},
        ]},
    'double_dive': {
        'doc': 'double_jump up to the DOUBLE_JUMP, A held `hold` frames, B -> DIVE, until DIVE_SLIDE',
        'task6_ok': True, 'src': 'census double_dive (Island 696 T 201-232)', 'params': {'land': 0, 'hold': 4},
        'phases': [
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['JUMP']},
            {'until': ['JUMP_LAND'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'btn': '', 'stick': 'aim', 'frames': 'param:land', 'expect': ['JUMP_LAND']},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['DOUBLE_JUMP']},
            {'btn': 'A', 'stick': 'aim', 'frames': 'param:hold'},
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['DIVE']},
            {'until': ['DIVE_SLIDE'], 'btn': '', 'stick': 'aim', 'max': 80},
        ]},
    'triple_dive': {
        'doc': 'triple_jump up to the TRIPLE_JUMP, A held `hold` frames, B -> DIVE, until DIVE_SLIDE',
        'task6_ok': True, 'src': 'census triple_dive (Task 6 755 T 740-765)', 'params': {'land': 0, 'hold': 3},
        'require': {'fv_min': 20.0},
        'phases': [
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['JUMP']},
            {'until': ['JUMP_LAND'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'btn': '', 'stick': 'aim', 'frames': 'param:land', 'expect': ['JUMP_LAND']},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['DOUBLE_JUMP']},
            {'until': ['DOUBLE_JUMP_LAND'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'btn': '', 'stick': 'aim', 'frames': 'param:land', 'expect': ['DOUBLE_JUMP_LAND']},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['TRIPLE_JUMP']},
            {'btn': 'A', 'stick': 'aim', 'frames': 'param:hold'},
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['DIVE']},
            {'until': ['DIVE_SLIDE'], 'btn': '', 'stick': 'aim', 'max': 80},
        ]},
    'backflip': {
        'doc': 'standing: Z -> crouch, A with Z held -> BACKFLIP, until the landing (stick neutral: straight back)',
        'task6_ok': True, 'src': 'decomp act_crouching', 'require': {'actions': ['IDLE']},
        'phases': [
            {'until': ['CROUCHING', 'START_CROUCHING'], 'btn': 'Z', 'stick': 'neutral', 'max': 4},
            {'btn': 'ZA', 'stick': 'neutral', 'frames': 1, 'expect': ['BACKFLIP']},
            {'until': ['BACKFLIP_LAND', 'BACKFLIP_LAND_STOP'], 'btn': '', 'stick': 'neutral', 'max': 80},
        ]},
    'turnaround': {
        'doc': 'running: stick toward `aim` (behind) -> TURNING_AROUND -> FINISH_TURNING_AROUND -> WALKING toward aim',
        'task6_ok': True, 'src': 'census WALKING -> TURNING_AROUND', 'require': {'fv_min': 16.0},
        'phases': [
            {'until': ['TURNING_AROUND'], 'btn': '', 'stick': 'aim', 'max': 6},
            {'until': ['WALKING'], 'btn': '', 'stick': 'aim', 'max': 60},
        ]},
    'arc_turn': {
        'doc': 'running: partial-stick arc toward `aim`, the stick at most `turn` yaw units from the facing (no '
               'TURNING_AROUND, the speed is kept), until the facing is within 0x800 of the aim',
        'task6_ok': True, 'src': 'partial stick (Task 6 686 / 689)', 'params': {'turn': 0x4000},
        'phases': [
            {'until_face': 0x800, 'btn': '', 'stick': 'turn', 'max': 60},
        ]},
    'side_flip_dive': {
        'doc': 'side_flip with B on the same frame as A (found better by the side_flip bench)',
        'task6_ok': True, 'src': 'moveset_bench (side_flip, B shifted -1 frame)', 'require': {'fv_min': 16.0},
        'phases': [
            {'until': ['TURNING_AROUND'], 'btn': '', 'stick': 'back0', 'max': 6},
            {'btn': 'AB', 'stick': 'aim', 'frames': 1, 'expect': ['SIDE_FLIP', 'DIVE']},
            {'until': ['DIVE'], 'btn': 'B', 'stick': 'aim', 'max': 3},
        ]},
    'dive_recover': {
        'doc': 'dive -> dive slide -> B (not A) on the 1st DIVE_SLIDE frame -> FORWARD_ROLLOUT (A and B both roll out; '
               'B leaves A free for the next jump) until FREEFALL_LAND_STOP',
        'task6_ok': True, 'src': 'decomp act_dive_slide (A or B pressed)', 'require': {'fv_min': 29.0},
        'phases': [
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['DIVE']},
            {'until': ['DIVE_SLIDE'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['FORWARD_ROLLOUT']},
            {'until': ['FREEFALL_LAND_STOP'], 'btn': '', 'stick': 'aim', 'max': 60},
        ]},
    'slide_kick': {
        'doc': 'running: Z -> CROUCH_SLIDE, B after `wait` frames -> SLIDE_KICK, until SLIDE_KICK_SLIDE',
        'task6_ok': True, 'src': 'census CROUCH_SLIDE -> SLIDE_KICK (1 f)', 'params': {'wait': 0},
        'phases': [
            {'btn': 'Z', 'stick': 'aim', 'frames': 1, 'expect': ['CROUCH_SLIDE']},
            {'btn': '', 'stick': 'aim', 'frames': 'param:wait', 'expect': ['CROUCH_SLIDE']},
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['SLIDE_KICK']},
            {'until': ['SLIDE_KICK_SLIDE'], 'btn': '', 'stick': 'aim', 'max': 80},
        ]},
    'slidekick_rollout': {
        'doc': 'slide_kick, then A on the SLIDE_KICK_SLIDE (after `land` frames) -> FORWARD_ROLLOUT until '
               'FREEFALL_LAND_STOP',
        'task6_ok': True, 'src': 'census slidekick_rollout (f14_1key_jp T 5705-5742, fv 56.45)',
        'params': {'wait': 0, 'land': 0},
        'phases': [
            {'btn': 'Z', 'stick': 'aim', 'frames': 1, 'expect': ['CROUCH_SLIDE']},
            {'btn': '', 'stick': 'aim', 'frames': 'param:wait', 'expect': ['CROUCH_SLIDE']},
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['SLIDE_KICK']},
            {'until': ['SLIDE_KICK_SLIDE'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'btn': '', 'stick': 'aim', 'frames': 'param:land', 'expect': ['SLIDE_KICK_SLIDE']},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['FORWARD_ROLLOUT']},
            {'until': ['FREEFALL_LAND_STOP'], 'btn': '', 'stick': 'aim', 'max': 60},
        ]},
    'slope_dive_rollout': {
        'doc': 'dive, then the DIVE_SLIDE held while it builds speed (down a steep slope: until fv drops `drop` under '
               'its peak), then A -> FORWARD_ROLLOUT (keeps the speed) until FREEFALL_LAND_STOP',
        'task6_ok': True,
        'src': "Hugo's JP movie 'vitesse conservation.m64' (dive slide 43 -> 88); US engine, THI (warp 13/1/0x0A) "
               "big mountain side (floor normal y 0.83): 48 -> 86.6 (test_dive_slide_builds_speed)",
        'params': {'drop': 1.0}, 'require': {'fv_min': 29.0},
        'phases': [
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['DIVE']},
            {'until': ['DIVE_SLIDE'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'while_fv': ['DIVE_SLIDE'], 'btn': '', 'stick': 'aim', 'min': 6, 'drop': 1.0, 'max': 300},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['FORWARD_ROLLOUT']},
            {'until': ['FREEFALL_LAND_STOP'], 'btn': '', 'stick': 'aim', 'max': 60},
        ]},
    'slidekick_chain': {
        'doc': 'the speed-conserving chain: Z -> CROUCH_SLIDE, B -> SLIDE_KICK (floors fv at 32, no cap), A on the '
               'SLIDE_KICK_SLIDE -> FORWARD_ROLLOUT (keeps the speed), and from its FREEFALL_LAND_STOP Z again; '
               '`cycles` times. Above 48 it beats dive / long jump chains (a DIVE resets to min(fv + 15, 48), a '
               'LONG_JUMP to min(fv x 1.5, 48)); below it, dive_rollout is faster (the +15)',
        'task6_ok': True,
        'src': "Hugo's JP movie 'vitesse conservation.m64' (slide kick 85 -> rollout 86, max 93.9); US engine, "
               "SSL test.st: from 54.8 fv, 1 cycle 39 f = 54.0 u/f (54.8 -> 57.2) vs dive_rollout 48.8 u/f; "
               "from 31 fv: 37.8 u/f vs dive_rollout 50.5",
        'params': {'cycles': 1}, 'require': {'fv_min': 10.0},
        'phases': [
            {'btn': 'Z', 'stick': 'aim', 'frames': 1, 'expect': ['CROUCH_SLIDE']},
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['SLIDE_KICK']},
            {'until': ['SLIDE_KICK_SLIDE'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['FORWARD_ROLLOUT']},
            {'until': ['FREEFALL_LAND_STOP'], 'btn': '', 'stick': 'aim', 'max': 60},
        ],
        'repeat': {'from': 0, 'times': 'cycles'}},
    'ground_pound': {
        'doc': 'A -> JUMP, A held `hold` frames, Z -> GROUND_POUND, then the cancel: the stick on the 1st '
               'BUTT_SLIDE_STOP frame -> WALKING (the ground pound itself cannot be cancelled: decomp)',
        'task6_ok': True, 'src': 'census ground_pound (Task 6 686 T 102-127)', 'params': {'hold': 14},
        'phases': [
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['JUMP']},
            {'btn': 'A', 'stick': 'aim', 'frames': 'param:hold'},
            {'btn': 'Z', 'stick': 'aim', 'frames': 1, 'expect': ['GROUND_POUND']},
            {'until': ['BUTT_SLIDE_STOP'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'until': ['WALKING'], 'btn': '', 'stick': 'aim', 'max': 3},
        ]},
    'gp_jump': {
        'doc': 'ground_pound cancelled by A on the 1st BUTT_SLIDE_STOP frame -> JUMP, until the landing',
        'task6_ok': True, 'src': 'decomp act_butt_slide_stop (check_common_action_exits)', 'params': {'hold': 7},
        'phases': [
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['JUMP']},
            {'btn': 'A', 'stick': 'aim', 'frames': 'param:hold'},
            {'btn': 'Z', 'stick': 'aim', 'frames': 1, 'expect': ['GROUND_POUND']},
            {'until': ['BUTT_SLIDE_STOP'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['JUMP']},
            {'until': ['JUMP_LAND', 'FREEFALL_LAND'], 'btn': '', 'stick': 'aim', 'max': 80},
        ]},
    'lj_again': {
        'doc': 'already in LONG_JUMP_LAND: Z + A on its 1st frame -> a new LONG_JUMP (the landing ignores A without Z '
               'and has no B cancel), until LONG_JUMP_LAND; `cycles` times',
        'task6_ok': True, 'src': 'Task 6 686 T 165-166 (the LJ before the first blue coin)', 'params': {'cycles': 1, 'air': 'strain'},
        'require': {'actions': ['LONG_JUMP_LAND']},
        'phases': [
            {'btn': 'ZA', 'stick': 'aim', 'frames': 1, 'expect': ['LONG_JUMP']},
            {'until': ['LONG_JUMP_LAND'], 'btn': '', 'stick': 'aim', 'max': 80},
        ],
        'repeat': {'from': 0, 'times': 'cycles'}},
    'jump_speed_kick': {
        'doc': 'running: A -> JUMP with A HELD through the landing, then B on the 1st WALKING frame with the stick at '
               'magnitude `kmag` (<= 48): B -> MOVE_PUNCHING, which becomes a JUMP_KICK at once with A held '
               '(act_move_punching: actionState 0 and A_DOWN) -> a "held" jump kick that keeps the speed (a jump or '
               'a "pressed" kick cuts 20%); JUMP_LAND itself has no B cancel',
        'task6_ok': True, 'src': 'TASVideos 2873S "forward jump kick trick"; 5237S (held vs pressed kicks)',
        'params': {'kmag': 16.0},
        'phases': [
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['JUMP']},
            {'until': ['WALKING'], 'btn': 'A', 'stick': 'aim', 'max': 80},
            {'btn': 'AB', 'stick': 'aim', 'mag': 'param:kmag', 'frames': 1, 'expect': ['JUMP_KICK']},
            {'until': ['FREEFALL_LAND', 'FREEFALL_LAND_STOP', 'JUMP_LAND'], 'btn': '', 'stick': 'aim', 'max': 80},
        ]},
    'blj': {
        'doc': 'backwards long jump: a long jump, stick toward `aim` (BEHIND Mario) in the air until the speed is '
               'negative, then Z + A on each 1st LONG_JUMP_LAND frame: every new LJ multiplies the negative speed by '
               '1.5 with no cap. On flat ground each LJ lasts ~30 frames and the speed does not grow (bench: exit fv ~ -14): '
               'it needs stairs or a slope where Mario lands every frame. FORBIDDEN in Task 6',
        'task6_ok': False, 'src': 'decomp act_long_jump / set_jumping_action (the cap only applies forward)',
        'require': {'fv_min': 10.0}, 'params': {'cycles': 3},
        'phases': [
            {'btn': 'Z', 'stick': 'keep0', 'frames': 1, 'expect': ['CROUCH_SLIDE']},
            {'btn': 'A', 'stick': 'keep0', 'frames': 1, 'expect': ['LONG_JUMP']},
            {'until': ['LONG_JUMP_LAND'], 'btn': '', 'stick': 'aim', 'max': 80},
            {'btn': 'ZA', 'stick': 'aim', 'frames': 1, 'expect': ['LONG_JUMP']},
            {'until': ['LONG_JUMP_LAND'], 'btn': '', 'stick': 'aim', 'max': 80},
        ],
        'repeat': {'from': 3, 'times': 'cycles'}},
    'ledge_dive': {
        'doc': 'run toward `aim` off a ledge; B on the 1st FREEFALL frame -> DIVE (+15 forward speed: 32 -> 47), or '
               'DIVE_SLIDE at once when the ground is that close; until the dive lands',
        'task6_ok': True, 'src': 'Task 1 FearOfCats 1119-1122 (31.9 -> 46.8), Elbertito 168-170 (low: DIVE_SLIDE 23 -> 38); '
               'FREEFALL -> DIVE in 36 of 80 runs', 'require': {'fv_min': 0.0},
        'phases': [
            {'until': ['FREEFALL'], 'btn': '', 'stick': 'aim', 'max': 30},
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['DIVE', 'DIVE_SLIDE']},
            {'until': ['DIVE_SLIDE'], 'btn': '', 'stick': 'aim', 'max': 80},
        ]},
    'backward_rollout': {
        'doc': 'negative speed: B -> DIVE (or already diving), A on the 1st DIVE_SLIDE frame with the stick behind -> '
               'BACKWARD_ROLLOUT, which keeps the negative speed (-174 for Kieran), until it lands',
        'task6_ok': True, 'src': 'Task 5 Kieran 440-446 (-176 -> -171), elbertito 387-393, FearOfCats 1375-1380; '
               'DIVE_SLIDE -> BACKWARD_ROLLOUT in 10 runs', 'require': {'fv_max': -1.0},
        'phases': [
            {'until': ['DIVE_SLIDE'], 'btn': 'B', 'stick': 'back', 'max': 60},
            {'btn': 'A', 'stick': 'back', 'frames': 1, 'expect': ['BACKWARD_ROLLOUT']},
            {'until': ['FREEFALL_LAND_STOP', 'FREEFALL_LAND'], 'btn': '', 'stick': 'back', 'max': 60},
        ]},
    'neg_kick_chain': {
        'doc': "haribo's negative speed chain (Task 6, -215 kept for 130 frames): A+B with the stick behind -> JUMP_KICK "
               '(no speed loss), A held through the landing with the stick neutral for `land` frames, then walking '
               'backward steered toward `aim` (stick at most `turn` from the facing, the facing turned to aim + 180 deg) '
               'for `walk` frames, A still held (no new jump); `cycles` kicks',
        'task6_ok': True, 'src': 'Task 6 haribo39 202-226 (-215.7 -> -191.8), 224-307 (5 more kicks)',
        'require': {'fv_max': -20.0}, 'params': {'land': 3, 'walk': 10, 'turn': 0x0E00, 'cycles': 1},
        'ranges': {'walk': (1, 14), 'turn': (0x0400, 0x2000)},
        'phases': [
            {'btn': 'AB', 'stick': 'back', 'frames': 1, 'expect': ['JUMP_KICK']},
            {'until': ['FREEFALL_LAND'], 'btn': 'A', 'stick': 'back', 'max': 40},
            {'btn': 'A', 'stick': 'neutral', 'frames': 'param:land', 'expect': ['FREEFALL_LAND']},
            {'until': ['WALKING'], 'btn': 'A', 'stick': 'turn', 'off': 0x8000, 'max': 6},
            {'btn': 'A', 'stick': 'turn', 'off': 0x8000, 'frames': 'param:walk', 'expect': ['WALKING']},
            {'btn': 'AB', 'stick': 'back', 'frames': 1, 'expect': ['JUMP_KICK'], 'last_skip': True},
        ],
        'repeat': {'from': 1, 'times': 'cycles'}},
    'butt_slide_jump': {
        'doc': 'sliding on the butt (a slope): A toward `aim` -> JUMP keeping most of the speed (79 -> 64), A held '
               'until the landing',
        'task6_ok': True, 'src': 'Task 1 ERGCXander 134-139, BlazzingMo 460-465; BUTT_SLIDE -> JUMP in 20 runs',
        'require': {'actions': ['BUTT_SLIDE']},
        'phases': [
            {'btn': 'A', 'stick': 'aim', 'frames': 1, 'expect': ['JUMP']},
            {'until': ['JUMP_LAND', 'FREEFALL_LAND', 'JUMP_LAND_STOP', 'FREEFALL_LAND_STOP'], 'btn': 'A', 'stick': 'aim',
             'max': 80},
        ]},
    'steep_jump_dive': {
        'doc': 'a long jump landing on a steep floor: Z + A -> STEEP_JUMP (not a new long jump), `wait` frames, '
               'B -> DIVE (42 -> 47), until it lands',
        'task6_ok': True, 'src': 'Task 5 Frame 131-136, DanielR 248-253; STEEP_JUMP -> DIVE in 11 runs',
        'require': {'actions': ['LONG_JUMP_LAND']}, 'params': {'wait': 1},
        'phases': [
            {'btn': 'ZA', 'stick': 'aim', 'frames': 1, 'expect': ['STEEP_JUMP']},
            {'btn': '', 'stick': 'aim', 'frames': 'param:wait', 'expect': ['STEEP_JUMP']},
            {'btn': 'B', 'stick': 'aim', 'frames': 1, 'expect': ['DIVE']},
            {'until': ['DIVE_SLIDE'], 'btn': '', 'stick': 'aim', 'max': 80},
        ]},
    'stop_turn': {
        'doc': 'land with the stick neutral (JUMP_LAND -> JUMP_LAND_STOP sets the speed to 0 at once), until IDLE, '
               'then the stick toward `aim`: the 1st WALKING frame faces the stick (a turn of any angle in one frame)',
        'task6_ok': True, 'src': 'Task 1 BlazzingMo 559-565 (+95 deg), Elbertito 213-219 (+103), Fritzafella 145-151; '
               'WALKING -> DECELERATING in 47 runs',
        'phases': [
            {'until': ['IDLE'], 'btn': '', 'stick': 'neutral', 'max': 40},
            {'btn': '', 'stick': 'aim', 'frames': 1, 'expect': ['WALKING']},
        ]},
}
ALIASES = {'dive_kick': 'rollout_kick', 'rollout_land_kick': 'rollout_kick', 'walk_crouch_lj': 'crouch_lj',
           'long_jump': 'crouch_lj', 'lj_land_lj': 'lj_chain', 'dive_rollout_chain': 'dive_rollout',
           'wall_kick_dive': 'wall_kick', 'punch_cancel': 'punch_walk', 'gp_cancel': 'ground_pound',
           'long_dive': 'jump_dive'}


def resolve(name):
    return ALIASES.get(name, name)
MACROS = tuple(SPECS)


_NAMES = {}


def _names(lst):
    """the action ids of a phase's list of names (with the B3313 aliases in B3313 mode); kept per list: the macros ask
    for them every frame (the specs' lists never change)"""
    key = (id(lst), B3313_MODE)
    hit = _NAMES.get(key)
    if hit is not None and hit[0] is lst:
        return hit[1]
    out = _names_of(lst)
    _NAMES[key] = (lst, out)
    return out


def _names_of(lst):
    out = {ACT[n] for n in lst}
    for n in lst:
        for v in B3313_ALIASES.get(n, ()):
            if isinstance(v, int):
                out.add(v)
            elif B3313_MODE:
                out.add(ACT[v])
    return out


def strain_stick(fv, face, aim, long_jump=False):
    import math as _m
    barrier = 48.0 if long_jump else 32.0
    side = ((aim - face + 0x8000) & 0xFFFF) - 0x8000
    if fv >= barrier - 0.15 or fv + 1.5 - 0.35 <= barrier - 0.01:
        f = 1.0 if abs(side) < 0x4000 else 0.0
    else:
        f = max(0.0, min(1.0, (barrier - 0.01 - fv + 0.35) / 1.5))
    s_side = _m.sqrt(max(0.0, 1.0 - f * f)) * (1 if side > 0 else -1 if side < 0 else 0)
    if abs(side) < 0x100:
        s_side = 0.0
    m = _m.hypot(f, s_side)
    if m < 1e-6:
        return None, 0.0
    ang = int(round(_m.atan2(s_side, f) * 32768 / _m.pi))
    return (face + ang) & 0xFFFF, 32.0 * min(1.0, m)


class Plan:

    def __init__(self, name_or_spec, **params):
        self.spec = SPECS[resolve(name_or_spec)] if isinstance(name_or_spec, str) else name_or_spec
        self.params = dict(self.spec.get('params', {}), **params)
        rep = self.spec.get('repeat')
        ph = list(self.spec['phases'])
        if rep:
            times = int(self.params.get(rep['times'], 1))
            body = ph[rep['from']:]
            ph = ph[:rep['from']]
            for c in range(times):
                ph += [p for p in body if not (p.get('last_skip') and c == times - 1)]
        self.phases = ph
        self.i, self.n, self.frame = 0, 0, 0
        self.face0, self.status, self.why = None, 'running', ''

    def _frames(self, ph):
        v = ph.get('frames', 1)
        return int(self.params[v.split(':', 1)[1]]) if isinstance(v, str) else int(v)

    def _emit(self, ph, face, aim):
        b = ph.get('btn', '')
        if b.startswith('alt:'):
            hi = sum(BTN[c] for c in b[4:]) if self.n % 2 == 0 else 0
        else:
            hi = sum(BTN[c] for c in b)
        mode = ph.get('stick', 'aim')
        mag = ph.get('mag', self.params.get('mag', 32.0) if mode in ('aim', 'turn') else 32.0)
        if isinstance(mag, str):
            mag = float(self.params[mag.split(':', 1)[1]])
        off = int(ph.get('off', 0)) + (int(self.params.get('off', 0)) if mode in ('aim', 'turn') else 0)
        if mode == 'rel0' and 'mag' not in ph:
            mag = 32.0
        if mode == 'aim' and self.params.get('air') == 'strain' and self._air and self._fv is not None:
            yaw, mag = strain_stick(self._fv, face, (int(aim) + off) & 0xFFFF, self._lj)
            if yaw is None:
                mag = 0.0
        elif mode == 'neutral' or mag <= 0:
            yaw = None
        elif mode == 'aim':
            yaw = (int(aim) + off) & 0xFFFF
        elif mode == 'turn':
            d = ((int(aim) + off - face + 0x8000) & 0xFFFF) - 0x8000
            t = int(self.params.get('turn', 0x4000))
            yaw = (face + max(-t, min(t, d))) & 0xFFFF
        elif mode == 'face':
            yaw = face & 0xFFFF
        elif mode == 'keep0':
            yaw = self.face0
        elif mode == 'rel0':
            yaw = (self.face0 + int(ph.get('off', 0)) + int(self.params.get('roff', 0))) & 0xFFFF
            mag = max(0.0, min(32.0, mag * float(self.params.get('mscale', 1.0))))
        elif mode == 'back0':
            yaw = (self.face0 + 0x8000) & 0xFFFF
        elif mode == 'back':
            yaw = (face + 0x8000) & 0xFFFF
        else:
            raise ValueError(mode)
        self.n += 1
        self.frame += 1
        return hi, yaw, mag

    def step(self, action, face, aim, fv=None):
        self._fv, self._air, self._lj = fv, bool(action & 0x800), action == ACT.get('LONG_JUMP')
        if self.face0 is None:
            self.face0 = face & 0xFFFF
        while True:
            if self.status != 'running':
                return None
            if self.i >= len(self.phases):
                self.status = 'ok'
                return None
            ph = self.phases[self.i]
            if 'until_face' in ph:
                if abs(((int(aim) - face + 0x8000) & 0xFFFF) - 0x8000) <= ph['until_face']:
                    self.i, self.n = self.i + 1, 0
                    continue
                if self.n >= ph.get('max', 60):
                    self.status, self.why = 'fail', f"phase {self.i}: facing not within {ph['until_face']} of the aim"
                    return None
                return self._emit(ph, face, aim)
            if 'while_fv' in ph:
                if self.n == 0:
                    self._peak = fv or 0.0
                self._peak = max(self._peak, fv or 0.0)
                if (action not in _names(ph['while_fv']) or self.n >= ph.get('max', 300)
                        or (self.n >= ph.get('min', 6) and fv is not None and fv < self._peak - float(self.params.get('drop', ph.get('drop', 1.0))))):
                    self.i, self.n = self.i + 1, 0
                    continue
                return self._emit(ph, face, aim)
            if 'until_fv' in ph:                        # this phase's input until the forward speed reaches a value
                if fv is not None and fv >= float(ph['until_fv']):
                    self.i, self.n = self.i + 1, 0
                    continue
                if self.n >= ph.get('max', 60):
                    self.status, self.why = 'fail', f"phase {self.i}: speed {ph['until_fv']} not reached in {ph.get('max', 60)} f"
                    return None
                return self._emit(ph, face, aim)
            if 'until' in ph:
                if action in _names(ph['until']):
                    self.i, self.n = self.i + 1, 0
                    continue
                if self.n >= ph.get('max', 60):
                    self.status, self.why = 'fail', f"phase {self.i}: {ph['until']} not reached in {ph.get('max', 60)} f"
                    return None
                return self._emit(ph, face, aim)
            if self.n < self._frames(ph):
                return self._emit(ph, face, aim)
            if ph.get('expect') and action not in _names(ph['expect']):
                self.status, self.why = 'fail', f"phase {self.i}: expected {ph['expect']}, got {NAME.get(action, hex(action))}"
                return None
            self.i, self.n = self.i + 1, 0


class Stepper:

    def __init__(self, name_or_spec, stick_raw, **params):
        self.plan, self.stick_raw = Plan(name_or_spec, **params), stick_raw

    @property
    def status(self):
        return self.plan.status

    def step(self, action, face, aim, cam, fv=None):
        r = self.plan.step(action, face, aim, fv)
        if r is None:
            return None
        hi, yaw, mag = r
        x, y = (0, 0) if yaw is None else self.stick_raw((yaw - cam) & 0xFFFF, mag)
        return hi, x, y


class Runner:

    N64_MARIO_STATE, N64_CAMERA_PTR = 0x8033B170, 0x8033CBD0
    N64_MS = {'action': 0x0C, 'forwardVel': 0x54, 'faceAngle': 0x2C, 'pos': 0x3C, 'squishTimer': 0xB4}

    def __init__(self, g, stick_raw):
        self.g, self.stick_raw = g, stick_raw
        self.n64 = getattr(g, 'n64', False)
        if self.n64:
            M = self.N64_MS
            self.ms = self.N64_MARIO_STATE
            self.cam_off = 2
        else:
            import n64mem as NM
            M = {m[3]: m[1] for m in NM.members(NM.find_type('MarioState'))}
            self.ms = g.addr('gMarioStates')
            self.cam_off = {m[3]: m[1] for m in NM.members(NM.find_type('Camera'))}['yaw']
        self.o_act, self.o_fv, self.o_face = M['action'], M['forwardVel'], M['faceAngle'] + 2
        self.o_pos, self.o_squish = M['pos'], M['squishTimer']

    def action(self):
        return self.g.read(self.ms, 'I', self.o_act)

    def fv(self):
        return self.g.read(self.ms, 'f', self.o_fv)

    def face(self):
        return self.g.read(self.ms, 'h', self.o_face) & 0xFFFF

    def squish(self):
        return self.g.read(self.ms, 'B', self.o_squish)

    def pos(self):
        return [self.g.read(self.ms, 'f', self.o_pos + 4 * k) for k in range(3)]

    def cam(self):
        c = self.g.read(self.N64_CAMERA_PTR, 'I') if self.n64 else self.g.read('gCamera', 'Q')
        return self.g.read(c, 'h', self.cam_off) & 0xFFFF if c else 0

    def press(self, yaw, buttons=0, mag=32.0):
        x, y = (0, 0) if yaw is None else self.stick_raw((int(yaw) - self.cam()) & 0xFFFF, mag)
        f = (x, y, buttons & 0xFF, 0)
        self.g.step(f)
        return f

    def run(self, name_or_spec, aim, max_frames=400, **params):
        # b3313_rules False: an N64-address game whose moves are vanilla (B3313 v0.7 has none of the later B3313 rules:
        # no locked moves, no red-star unlocks, no renamed actions, no changed timings)
        rules = self.n64 and getattr(self.g, 'b3313_rules', True)
        if rules and isinstance(name_or_spec, str):
            why = b3313_locked(name_or_spec, b3313_red_stars(self.g))
            if why:
                self.why = why
                return [], 'locked'
        global B3313_MODE
        if rules and isinstance(name_or_spec, str):
            params = {**B3313_PARAMS.get(resolve(name_or_spec), {}), **params}
            name_or_spec = b3313_spec(name_or_spec)
        p = Plan(name_or_spec, **params)
        out = []
        B3313_MODE = rules
        try:
            while len(out) < max_frames:
                r = p.step(self.action(), self.face(), aim, self.fv())
                if r is None:
                    break
                out.append(self.press(r[1], r[0], r[2]))
        finally:
            B3313_MODE = False
        self.why = p.why
        return out, p.status

    def dive_rollout(self, yaw, cycles=1, exit='dive', max_frames=200):
        return self.run('rollout_kick' if exit == 'kick' else 'dive_rollout', yaw, max_frames, cycles=cycles)[0]

    def wall_kick(self, yaw_away, hold=7, dive=True, wait=40):
        return self.run('wall_kick', yaw_away, hold=hold)[0]

    def side_flip(self, yaw_new, hold=10, dive=True, wait=6):
        return self.run('side_flip', yaw_new, hold=hold)[0]


PARAM_RANGES = {'hold': (0, 20), 'wait': (0, 4), 'land': (0, 4), 'cycles': (1, 4), 'turn': (0x1000, 0x4700)}
STICK_RANGES = {'mag': (16.0, 32.0), 'off': (-0x1000, 0x1000)}


def param_space(name, stick=True):
    spec = SPECS[resolve(name)]
    rng = dict(PARAM_RANGES, **spec.get('ranges', {}))
    sp = {k: rng[k] for k in spec.get('params', {}) if k in rng}
    if stick and any(ph.get('stick', 'aim') in ('aim', 'turn') for ph in spec['phases']):
        sp.update(STICK_RANGES)
    return sp


def sample_params(name, rng, keep=0.5, stick=True):
    """parameters of a macro, half of them moved within their range: rng is a quasi.Quasi (computed values, no random
    draw); an int gives the Quasi of that seed"""
    if isinstance(rng, int):
        from quasi import Quasi
        rng = Quasi(rng)
    out = dict(SPECS[resolve(name)].get('params', {}))
    for k, (lo, hi) in param_space(name, stick).items():
        if rng.random() < keep:
            continue
        out[k] = rng.randint(lo, hi) if isinstance(lo, int) and isinstance(hi, int) else round(rng.uniform(lo, hi), 1)
    return out


def run_named(runner, name, yaw, k=1, params=None):
    name = resolve(name)
    if params is not None:
        return runner.run(name, yaw, **params)[0]
    spec = SPECS[name]
    key = 'cycles' if 'cycles' in spec.get('params', {}) else 'hold'
    val = max(1, k) if key == 'cycles' else max(2, min(20, k))
    return runner.run(name, yaw, **{key: val})[0]


# forward speed on entering an airborne action (decomp mario.c set_mario_action_airborne): dive and long jump cap at 48,
# slide kick only has a floor, jump kick and rollout keep it, jumps cost 20%
_ENTRY = {'JUMP': lambda v: v * 0.8, 'HOLD_JUMP': lambda v: v * 0.8, 'DOUBLE_JUMP': lambda v: v * 0.8,
          'TRIPLE_JUMP': lambda v: v * 0.8, 'SIDE_FLIP': lambda v: 8.0, 'BACKFLIP': lambda v: -16.0,
          'WALL_KICK_AIR': lambda v: max(v, 24.0), 'DIVE': lambda v: min(v + 15.0, 48.0),
          'LONG_JUMP': lambda v: min(v * 1.5, 48.0), 'SLIDE_KICK': lambda v: max(v, 32.0)}


def entry_speed(name, fv):
    """the forward speed the macro keeps through the actions it enters (its steps' expect), drag left out: what a dive or
    a long jump costs above 48 and what a slide kick, a jump kick or a rollout keep"""
    v = fv
    for ph in SPECS[resolve(name)].get('phases', ()):
        acts = [a for a in list(ph.get('expect') or ()) + list(ph.get('until') or ()) if a in _ENTRY]
        if acts:                                        # either of several actions: the lower speed
            v = min(_ENTRY[a](v) for a in acts)
    return v


def in_domain(name, action, fv, g=None):
    if g is not None and getattr(g, 'n64', False):
        why = b3313_locked(name, b3313_red_stars(g))
        if why:
            return False, why
    req = SPECS[resolve(name)].get('require', {})
    a = NAME.get(action, hex(action)) if isinstance(action, int) else action
    if req.get('actions') and a not in req['actions']:
        return False, f'action {a} not in {req["actions"]}'
    if fv < req.get('fv_min', -1e9):
        return False, f'fv {fv:.1f} < {req["fv_min"]}'
    if fv > req.get('fv_max', 1e9):
        return False, f'fv {fv:.1f} > {req["fv_max"]}'
    return True, ''


def plan_moves(path, state):
    import move_planner
    return move_planner.plan_moves(path, state)


def select(state, segment):
    import move_planner
    return move_planner.select(state, segment)


def play_plan(runner, plan, target_check=None, path=None, **kw):
    import move_planner
    return move_planner.play_plan(runner, plan, target_check, path, **kw)


def task6_names():
    return [n for n, s in SPECS.items() if s.get('task6_ok', True)]


def export_json(path=None):
    d = {'_doc': "Movement technique bank (moveset): DECLARATIVE closed-loop macros, one engine for the CPU and a step-by-step API for\nframe-by-frame policies (Blue's t6_waypoint) and GPU rule tables.", 'buttons': {'A': A, 'B': B, 'Z': Z}, 'macros': SPECS,
         'aliases': ALIASES}
    if path:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(d, f, indent=1)
    return d
