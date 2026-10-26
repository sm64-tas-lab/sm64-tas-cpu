---
name: sm64-headless
description: Play Super Mario 64 headless (no emulator) through the sm64-headless MCP tools - read Mario and the objects from RAM, move with the engine's moves, try many facings at once, keep checkpoints, export a .m64 for Mupen. Use whenever the user asks to play, test, route or TAS something in SM64.
---

# Playing SM64 with sm64-headless

The `sm64-headless` MCP server runs the decompiled game natively: no emulator, 35,000-55,000 frames/s, bit-exact
with Mupen64. Everything you read comes straight from RAM.

## Start

- `start(st=...)` from a Mupen savestate, or `start(movie=...)` (.m64 / .hls from power-on), or `start()` from
  power-on. `version` is `us` (default) or `jp`. Call it once.

## Perceive before acting

- `state()`: Mario's position, speeds, facing, action, level / area, coins, stars, health.
- `scan(radius)`: nearby objects (behaviour names like `bhvRedCoin`, `bhvStar`, `bhvGoomba`), floors by height, walls.
- `scan(filter="coin")`: every matching object in the level, sorted by distance.

## Act

- `goto(x, z, style="walk" | "longjump")`: travel; enemies are avoided.
- `collect(name="RedCoin")`: go and take an object; trust `collected`, never your own guess.
- `move(name, x, z)`: one of the engine's moves (`moves()` lists them). A move the game refuses comes back with a
  `warning` (B while walking under speed 29 punches instead of diving): read it and `undo`.
- `walk(x, z, frames)`, `step(...)`: finer control.

## Use the speed

- `try(...)` plays a walk / move and takes it back.
- `best(name or frames, x, z, spread, step, commit=true)` tries a move at dozens of facings in a fraction of a second
  and keeps the best: prefer it to guessing an angle.
- `checkpoint(name)` before anything risky, `restore(name)` to come back, `undo(n)`.

## Finish

- `export(path)`: the run as a `.m64` (with its `.st` when it started from one), to replay in Mupen64-rr-lua.
- Only claim a result a tool returned (`collected=true`, `reached=true`, a counter up).
