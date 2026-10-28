---
name: sm64
description: Play, test, route or TAS Super Mario 64 - headless (no emulator, ~40,000 frames/s, bit-exact with Mupen64) through the sm64-headless MCP tools, and in the real emulator through the sm64 tools (Mupen64-rr-lua). Sets itself up on first use (clones the repos, unlocks the game with the user's own ROM, registers the MCP servers). Use whenever the user mentions Super Mario 64, SM64, a TAS, a .m64 / .st, Mupen, or asks the AI to play the game.
---

# Super Mario 64 for AIs

Two MCP servers:
- `sm64-headless` (repo sm64-tas-cpu): the decompiled game compiled natively, no emulator. Fast: use it to explore,
  try and search.
- `sm64` (repo sm64-mupen-bridge, optional): the real emulator driven live. Use it to check a movie for real.

## 1. Setup (only when the `sm64-headless` tools are not available)

Do it yourself, step by step, and tell the user what you are doing. Windows 10/11 x64 only.

1. Check the prerequisites: `git --version` and a 64-bit Python 3.9 or newer
   (`py -3 -c "import sys; print(sys.version, sys.maxsize > 2**32)"` or `python ...`). If one is missing, tell the
   user what to install (https://git-scm.com, https://www.python.org/downloads/ with "Add python.exe to PATH") and
   stop until it is there.
2. Pick a folder (ask the user, default `%USERPROFILE%\sm64`) and clone:
   `git clone -b public https://github.com/sm64-tas-lab/sm64-tas-cpu`
3. Ask the user for the path of **their own** Super Mario 64 (USA) ROM (.z64 / .n64 / .v64). Never download a ROM,
   never look for one on the internet: the game DLLs are locked and only open with the user's original dump. The
   Japanese ROM is optional (only for the JP version).
4. Run the installer without questions:
   `python sm64-tas-cpu/install.py --yes --rom-us "<ROM>" [--rom-jp "<JP ROM>"] [--mupen "<mupen64.exe>"]`
   It installs numpy and lz4, unlocks the DLLs, fetches the decompilation's sources (code headers only), and
   registers `sm64-headless` (Claude Code with this skill, Gemini CLI, `mcp.json` for other clients). Read its
   output: "is not the original Super Mario 64" means a wrong or modified ROM; ask for another dump.
5. Check it: `python sm64-tas-cpu/agent/sm64_headless_mcp.py --demo` must print a start, a scan, moves and an export.
6. Optional, the real emulator: `git clone -b public https://github.com/sm64-tas-lab/sm64-mupen-bridge` beside it, then
   `powershell -ExecutionPolicy Bypass -File sm64-mupen-bridge/install.ps1 -Yes -NoHeadless -Rom "<ROM>"`
   (it finds Mupen64-rr-lua or downloads the official release, and registers the `sm64` server).
7. MCP servers load when the AI client starts: tell the user to restart it (Claude Code: exit and run `claude`
   again), then continue below.

If the tools are still missing after a restart: `claude mcp list` (or the client's own list) shows whether
`sm64-headless` is registered; `mcp.json` in the repo holds the exact command to add by hand.

## 2. Playing headless (`sm64-headless`)

- `start()`: Mario standing in the castle grounds. `start(st=...)` a Mupen savestate, `start(movie=...)` a .m64 /
  .hls from power-on, `start(power_on=true)` the console just switched on; `version` us / jp.
- Perceive before acting: `state()`; `scan(radius)` objects by behaviour name (`bhvRedCoin`, `bhvStar`...), floors
  by height, walls; `scan(filter="coin")` every match in the level.
- Act: `goto(x, z)` (style `auto`: it races chained long jumps against running from where Mario is and keeps the
  faster, and swims once in the water; `legs` says what it did), `collect(name="RedCoin")` (trust `collected`; it
  swims to an underwater object too), `move(name, x, z, params)`, `swim(x, z, y)`, `walk`, `step`.
  A move the game refuses is taken back by itself (`kept=false`) and `warning` says why (too slow, wrong action).
  When `state()` says `blocked` (a sign, a dialog, a cutscene), call `close_dialog`. A failed `collect` returns
  `dy` / `dist` / `hint`: an object above Mario needs its platform reached first (scan floors, jump onto it).
- Units: positions in game units (Mario is about 160 tall; y is up), speeds in units a frame, 30 frames a second.
  Facings in degrees for the tools: `yaw_deg` 0 = +z, 90 = +x, 180 = -z, 270 = -x (`state().face_yaw_deg`).
- Use the speed: `try(...)` plays and takes back; `best(name or frames, x, z, spread, step, commit=true)` tries a
  move at dozens of facings in a fraction of a second and keeps the best: prefer it to guessing an angle.
  `checkpoint(name)` / `restore(name)` / `undo(n)`.
- Between levels: `state().level_name` says where Mario is ("Castle grounds", "Castle inside (ground floor)",
  "Bob-omb Battlefield"...). `warps()` lists where this area's doors, pipes and paintings lead, with their x, y, z
  and `how` to go in (`warps(to="bob-omb")` filters). You lead Mario there: a door is walked into (`goto` its x, z);
  a painting has a `front` point: `goto` it, then jump toward the painting (`walk` to its x, z with
  `buttons=["A"]` for ~12 frames: its warp is behind a 100-unit lip at its foot). Then `close_dialog` passes the
  fade, the star select and the course's opening text, and stops the frame Mario can move (it plays nothing when
  nothing holds the game). From the castle grounds: `goto(0, -3155)` (the front door; `close_dialog` for Lakitu's
  text on the way and the hall's text after).
- `goto` takes the straight line first; stuck on a wall, it takes the walking path around the walls (through open
  doors, up stairs) and keeps the better arrival (`legs` shows `route: N turns`). No path at all is said in `legs`:
  a jump, a locked door (star doors need stars) or another way.
- Finish: `export(path)` writes the run as a .m64 (with its .st when it started from one). Started from `start()`
  (no .st), the movie begins at power-on: `frames` counts the boot too, `run_frames` the run, and in Mupen it is
  played from power-on with `play_inputs(m64=...)`.

## Any model, any client

- A small or local model: register the server with `--simple`
  (`python agent/sm64_headless_mcp.py --simple`): 9 tools (start, state, scan, goto, collect, close_dialog, warps,
  undo, export), short descriptions, short answers. `goto` and `collect` do the TAS work inside (they race long jumps,
  running and a search over the TAS moves, and keep the fastest), so calling them is enough.
- No MCP at all: `python agent/play.py run.txt` plays a file of commands, one a line (`start`, `scan door`,
  `goto 0 1800`, `close_dialog`, `warps bob-omb`, `walk -5442 -460 12 buttons=[A]`, `collect RedCoin`,
  `export run.m64`), and prints one
  JSON answer a line;
  `python agent/play.py` reads them from the input. `--full` gives every tool.

## Play like a TASer, not like a walk

A TAS never just runs: it chains the moves that keep or gain speed, and every frame counts.
- `goto` (style auto) already tries a search over these moves (`search` seconds, default 4; 0 turns it off)
  and keeps it when it arrives sooner: `legs` names the moves (`tas: run 8 > lj_chain x2`).
- On the ground, running caps at 32 u/f. To cover distance, chain moves: `lj_chain` (long jumps, up to 48 u/f,
  `params={"cycles": N}`), `dive_rollout` (needs a speed of 29+: run first), `slide_kick` / `slidekick_chain`,
  `triple_jump` for height and distance. `goto` (auto) already races long jumps against running; for a precise
  route, play the moves yourself.
- Turning: `side_flip` reverses at speed, `arc_turn` turns without losing speed; a full stop and `turnaround`
  costs frames.
- Height: `triple_jump`, `backflip`, `wall_kick` (needs a wall: it waits for the bonk and kicks off frame perfect),
  `gp_jump`. Look at `scan()` floors (`dh` = height above Mario) before choosing.
- Each move's `params` are its frame-perfect settings (a wait before A, a hold, how many cycles): `moves()` gives
  them with their defaults. Use `best(name, x, z, spread, step)` to find the facing, and `try` to compare two
  ideas before you `commit`.
- Water: `swim(x, z, y)` swims as fast as the game allows: one A down at the right frame of each stroke builds the
  swim strength up to the cap of 28 u/f (mashing A is slower: a new press restarts the stroke at the lowest
  strength). The stick's x turns Mario, its y is the pitch (it handles both). Out of the water it does a water jump
  (62 u/f up) when the target is on land; then `goto` goes on.
- Compare with the clock: `state().frames` before and after; keep a `checkpoint` before each choice and `restore`
  the slower one.

The shape of a session (the coordinates are only an example):
```
start()                                    -> Mario standing, frames 0
scan(filter="RedCoin")                     -> bhvRedCoin at (1200, 640, -300), dy 400
checkpoint("a"); goto(x=900, z=-200)       -> reached, legs ["longjump: arrived", "raced: ..."]
best(name="triple_jump", x=1200, z=-300, spread=30, step=3, commit=true)
collect(name="RedCoin", near_x=1200, near_z=-300) -> collected=true
collect(name="Coin", near_x=3000, near_z=4000)    -> goto swims to it (legs ["...: in the water", "swim: arrived"])
export(path="run.m64")
```

## 3. Checking in the emulator (`sm64`, optional)

`start_mupen(savestate=...)` once, `get_state`, `step_frames`, `save_memstate` / `load_memstate`, `screenshot`,
`export_run`, then `replay_movie` replays the movie on a fresh Mupen and returns the final state to prove it.
`swim(x, z, y)` swims there with the same fastest strokes as headless. For a frame-perfect line, plan it headless
(the moves, `swim`), `export` it, then `play_inputs(m64=..., from_frame=...)` plays its exact frames in Mupen
(or `inputs=[[x, y, hi, lo], ...]`).

## When something goes wrong

Tell the user plainly what failed and what it means, in their language. Then:

1. Try the known fixes:
   - "is not the original Super Mario 64": the ROM is a modified or other version; ask for an original US dump.
   - `pip` fails: run `python -m pip install --upgrade pip`, then the installer again.
   - Python too old or 32-bit, git missing: say what to install (step 1) and wait.
   - The tools are missing after a restart: `claude mcp list`, or add the command from the repo's `mcp.json` by hand.
   - `git clone` fails: check the internet connection, then retry.
   - A tool returns an error during play: read it, `undo` or `restore` a checkpoint, try another approach.
2. If it still fails, write a ready-to-send report for the user: what they asked, the exact command or tool call,
   its full output, `python --version`, the Windows version, and the ROM's MD5
   (`python -c "import hashlib,sys; print(hashlib.md5(open(sys.argv[1],'rb').read()).hexdigest())" "<ROM>"`), never
   the ROM itself. Give them the link to open an issue with it:
   https://github.com/sm64-tas-lab/sm64-tas-cpu/issues/new (or https://github.com/sm64-tas-lab/sm64-mupen-bridge/issues/new
   for the emulator part).

Never hide a failure or work around it by downloading a ROM or editing the game files.

## Rules

- Only claim a result a tool returned (`collected=true`, `reached=true`, a counter up, a movie confirmed).
- Never copy, upload or share the ROM or the unlocked DLLs.
