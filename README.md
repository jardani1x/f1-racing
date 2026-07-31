# Ember Drift

A top-down arena survivor for the browser. You are an ember that is going out;
everything in the dark is there to help that along. Kill things, collect the heat
they drop, pick an upgrade every level, and last as long as you can.

Zero runtime dependencies. No build step required to play. No external assets:
every sprite and every sound is generated procedurally at load, so there is
nothing to fetch and no licensing question about any of it.

## Run it

```bash
# Single file, opens straight from disk
open dist/ember-drift.html

# Or serve the unbundled source (ES modules need an HTTP origin)
npm run serve      # http://localhost:8080/

npm test           # 91 tests; 64 of them need no dependencies at all
npm run build      # regenerate dist/ember-drift.html
node tools/bench.js
```

`npm install` pulls exactly one development dependency (jsdom) to run the DOM
suites. Everything else runs on a bare checkout.

Append `?seed=12345` to make a run reproducible.

## Controls

| | Move | Aim | Pause | Choose upgrade |
|---|---|---|---|---|
| Keyboard | `WASD` / arrows | mouse | `Esc` / `P` | `1` `2` `3`, or arrows + `Enter` |
| Gamepad | left stick | right stick | Start | D-pad + `A` |
| Touch | drag anywhere | automatic | on-screen | tap a card |

`R` restarts, `M` mutes, `F3` toggles the performance overlay. Aiming is
optional: the weapons target the nearest enemy on their own, so the game is
completable with movement alone.

## Architecture

```
src/
  config.js          every tunable, deep-frozen at load
  core/              math, seeded rng, event bus, pools, broadphase, loop, entity store
  game/              content tables, simulation, particles, world-to-drawlist view
  render/            camera, draw list, quality controller, sprite atlas, Canvas2D backend
  input/             keyboard, mouse, touch and gamepad resolved to one snapshot
  audio/             procedural synthesis
  ui/                DOM heads-up display, dialogs, safe storage
  main.js            the only module that knows about all the others
```

The decisions worth knowing about:

**Fixed-timestep simulation with render interpolation.** Gameplay advances in
whole 60 Hz ticks and the renderer receives a fractional `alpha`, so behaviour is
identical at 60, 90, 120 and 144 Hz. Frame deltas are snapped onto whole tick
periods when they are already within a millisecond, which removes the periodic
micro-stutter caused by a 60 Hz display and a 60 Hz simulation disagreeing in the
last floating-point bit.

**Structure-of-arrays entity storage, not a general ECS.** The component set is
fixed and known, so archetype tables and a query planner would add indirection
without adding capability. Parallel typed arrays give the same cache locality.
Destruction is deferred to the tick boundary, so a system can kill entities while
iterating.

**A CSR uniform grid for broadphase.** Collider radii and density are near
uniform and the arena is bounded, so a grid beats a quadtree: O(1) placement, no
rebalancing, direct indexing instead of hashing. It is built by counting sort and
allocates nothing. The first version used per-cell arrays and measured ~10 kB of
allocation per tick, which triggered a minor GC roughly every 100 ticks.

**The renderer sits behind a command list.** The view layer describes what to draw
in world coordinates; the backend decides how. Canvas2D is the shipped backend
because at this entity budget the bottleneck is state changes rather than fill
rate, and it avoids shader compilation stalls and context-loss handling on mobile
Safari. A WebGL2 backend can replace it without touching a line of gameplay.

**The simulation is deterministic and headless.** It holds no reference to the
DOM, canvas, audio or input hardware; it takes an input snapshot and advances.
Same seed plus same inputs gives a bit-identical run, which is what makes the
balance measurements below meaningful and bug reports replayable.

**Content is data.** Enemies, weapons and upgrades are entries in arrays in
`src/game/content.js`, with spawn weights and availability expressed as small pure
functions. Adding an enemy or an upgrade means appending to an array. Only a
genuinely new *behaviour* or firing pattern needs code, and those are integer tags
switched on in the simulation rather than per-entity closures, because a virtual
call per enemy per tick at 420 enemies is measurable and a switch is not.

## Performance

Measured with `tools/bench.js`, worst realistic load, saturated particle buffer:

| Scenario | Simulation p99 | Draw-list build p99 | Share of a 16.67 ms frame |
|---|---|---|---|
| 60 enemies | 212 µs | 114 µs | 2.0% |
| 250 enemies | 354 µs | 134 µs | 2.9% |
| 420 enemies (ceiling) | 406 µs | 131 µs | 3.2% |

**These figures exclude GPU rasterisation**, which cannot be measured off a real
device and is the genuine unknown. The headroom is deliberate: it is the budget
for compositing on a mid-range phone. If a device cannot keep up anyway, the
quality controller sheds additive blending, then particle volume, then floating
combat text, in that order, and never touches anything needed to read the game
state. Demotions latch, because oscillating between tiers is worse than sitting
one tier low.

Steady state allocates nothing per tick. Every buffer is sized at construction.

## Balance

Tuned against a scripted bot across five seeds, ten simulated minutes each. The
process found one real defect: 33% of all experience dropped was expiring on the
floor, which starved progression badly enough that the player never out-scaled the
spawn director. Orbs now converge on the player from any distance, and when the
orb ceiling is hit the excess is credited directly rather than deleted, so the
economy is exact and the entity count is still bounded.

After the fix, across seeds `0xBEEF 0xC0DE 0x1234 0xFACE 0x5EED`: 98–99% of
experience collected, level 11–13, 194–230 seconds survived. Low variance across
seeds was the goal; a run should be lost to a decision, not to a seed.

## Testing

91 tests, no framework. The interesting ones are not the unit tests:

- **Determinism** replays two worlds tick by tick and compares a state hash,
  which is the property the whole architecture rests on.
- **The broadphase is cross-checked against brute force** over 300 randomised
  trials, verifying by independent re-derivation rather than by re-reading the
  implementation.
- **The shell suite drives the real game in a real DOM**: starting, pausing,
  levelling, dying, restarting, touch dragging, focus trapping, context loss.
- **A non-finite value reaching the canvas fails the build.** A NaN coordinate
  makes the browser skip the draw silently, so the symptom is a blank screen with
  a clean console. Forty-five seconds of gameplay is scanned for it.
- **The built single file is booted and simulated**, not just the sources it came
  from.

## Accessibility

Keyboard-complete, including every dialog. Focus is trapped in modals and
returned on close. `prefers-reduced-motion` replaces screen shake and the
invulnerability strobe with a constant dim. Touch targets are at least 64 px.
Safe-area insets are respected on notched devices. Level-ups and run endings are
announced politely to screen readers. Health is readable three ways: the corona
ring around the player, the bar, and the number.

## Known limitations

- **Nothing here has been confirmed on real hardware.** Everything is verified
  headlessly. The rendering is validated structurally, not visually.
- GPU rasterisation cost is unmeasured, as noted above.
- The difficulty ramp is a single curve over ten minutes; there are no discrete
  boss encounters or wave events yet. `DIRECTOR` in `src/config.js` is where that
  would go.
- Progress does not persist between sessions beyond the best time.

## Licence

MIT. All art, audio and code are original and generated at runtime.
