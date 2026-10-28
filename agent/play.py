"""Play Super Mario 64 headless from a command line: for any AI that can run a command, with or without MCP.

    python agent/play.py run.txt                 the commands of run.txt, one a line, then their answers
    python agent/play.py                         the commands typed (or piped) one a line
    python agent/play.py --full ...              every tool and its whole answer (default: the short answers)

A command is a tool and its arguments, by position for the common ones or as key=value:
    start                        goto 0 1800                  collect RedCoin
    scan star                    swim 3000 4000 -600          move lj_chain yaw_deg=90 params={"cycles":2}
    state                        close_dialog                 export run.m64
Lines starting with # are skipped. Each answer is one line of JSON.
"""
import json
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sm64_headless_mcp as S

POSITIONAL = {'goto': ('x', 'z', 'y'), 'swim': ('x', 'z', 'y'), 'collect': ('name', 'near_x', 'near_z'),
              'scan': ('filter',), 'move': ('name', 'x', 'z'), 'walk': ('x', 'z', 'frames'), 'export': ('path',),
              'snapshot': ('path',), 'start': ('st',), 'undo': ('n',), 'checkpoint': ('name',), 'restore': ('name',),
              'step': ('frames',)}


def value(v):
    try:
        return json.loads(v)
    except ValueError:
        return v


def parse(line):
    words = shlex.split(line, posix=True)
    tool, args, pos = words[0], {}, list(POSITIONAL.get(words[0], ()))
    for w in words[1:]:
        if '=' in w and not w.startswith('{'):
            k, v = w.split('=', 1)
            args[k] = value(v)
        elif pos:
            args[pos.pop(0)] = value(w)
        else:
            raise ValueError('%s: too many values (%s); use key=value' % (tool, w))
    return tool, args


def main(argv):
    full = '--full' in argv
    argv = [a for a in argv if a != '--full']
    lines = open(argv[0], encoding='utf-8').read().splitlines() if argv else sys.stdin
    play = S.Play()
    out = sys.stdout
    sys.stdout = sys.stderr                     # (the engine's prints apart from the answers)
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        try:
            tool = line.split()[0]
            if not hasattr(play, 't_' + tool) or (not full and tool not in S.SIMPLE and tool not in POSITIONAL):
                raise ValueError('unknown command %s (commands: %s; --full for every tool)'
                                 % (tool, ', '.join(sorted(set(S.SIMPLE) | set(POSITIONAL)))))
            tool, args = parse(line)
            r = getattr(play, 't_' + tool)(args)
            r = r if full else S.compact(r)
        except Exception as e:
            r = {'error': '%s: %s' % (type(e).__name__, e)}
        out.write('%s -> %s\n' % (line, json.dumps(r, default=str)))
        out.flush()


if __name__ == '__main__':
    main(sys.argv[1:])
