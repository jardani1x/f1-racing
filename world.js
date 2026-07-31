import { ARENA, DIRECTOR, FX, LIMITS, PHYS, PLAYER, PROJECTILE, XP } from '../config.js';
import { EntityStore, FLAG, KIND } from '../core/entities.js';
import { EV, EventBus } from '../core/events.js';
import { TAU, clamp, dist2, len } from '../core/math.js';
import { Rng } from '../core/rng.js';
import { SpatialGrid } from '../core/spatialgrid.js';
import {
  BEHAVIOR,
  ENEMY_TYPES,
  MAX_ENEMY_RADIUS,
  PATTERN,
  SPRITE,
  UPGRADES,
  UPGRADE_BY_KEY,
  WEAPONS,
  WEAPON_BY_KEY,
  computeStats,
  weaponCooldown,
  weaponCount,
  weaponDamage,
  weaponRadius,
  xpForLevel,
} from './content.js';
import { FloatTextSystem, ParticleSystem, TINT } from './fx.js';

/** Candidate buffer for broadphase queries. Sized above the worst realistic cell occupancy. */
const QUERY_CAP = 512;
/** Cap on neighbours considered for separation, bounding the O(n*k) term. */
const SEPARATION_NEIGHBOURS = 6;
/** How far auto-aim will look for a target. */
const TARGET_RANGE = 660;
/** Hoisted: a literal here would allocate an array every tick. */
const PROJECTILE_KINDS = Object.freeze([KIND.BULLET, KIND.ENEMY_BULLET]);

/**
 * The simulation.
 *
 * Contains no reference to the DOM, canvas, audio or input hardware: it takes a
 * plain input snapshot and advances state. That is what makes it testable
 * headlessly, deterministic under a seed, and replaceable behind a network layer
 * later without touching a system.
 *
 * Presentation observes it two ways: pull (read the entity arrays each frame,
 * see game/view.js) and push (subscribe to the event bus for one-shot cues).
 */
export class World {
  /**
   * @param {object} [opts]
   * @param {number} [opts.seed]
   * @param {EventBus} [opts.bus]
   */
  constructor(opts = {}) {
    this.bus = opts.bus ?? new EventBus();
    this.rng = new Rng(opts.seed ?? 1);
    this.entities = new EntityStore(LIMITS.maxEntities);
    this.grid = new SpatialGrid(PHYS.cellSize, ARENA.half, LIMITS.maxEntities);
    /** Compacted alive-enemy list handed to the broadphase each build. */
    this._gridIds = new Int32Array(LIMITS.maxEntities);
    this.particles = new ParticleSystem(LIMITS.maxParticles);
    this.floaters = new FloatTextSystem(LIMITS.maxFloatText);

    this._query = new Int32Array(QUERY_CAP);
    this._weights = new Float64Array(ENEMY_TYPES.length);
    this._choicePool = new Int32Array(UPGRADES.length);
    this._choiceWeights = new Float64Array(UPGRADES.length);

    /** Stack count per upgrade, parallel to UPGRADES. */
    this.stacks = new Int32Array(UPGRADES.length);
    /** @type {{index: number, level: number, cooldown: number}[]} */
    this.weapons = [];
    this.stats = computeStats(this.stacks);

    /**
     * Shared event payload. Reused rather than allocated because kill and hit
     * cues fire hundreds of times a second; subscribers must read it during the
     * dispatch and never retain it.
     */
    this.ev = { x: 0, y: 0, amount: 0, elite: false, typeId: 0, crit: false, level: 0 };

    this.player = -1;
    /** Half-diagonal of the visible area in world units; sets the spawn ring. */
    this.viewHalfDiag = 520;

    this.run = {
      time: 0,
      level: 1,
      xp: 0,
      xpNeeded: xpForLevel(1),
      kills: 0,
      damageDealt: 0,
      damageTaken: 0,
      /** Level-ups banked but not yet spent on a card. */
      pendingLevelUps: 0,
      over: false,
      /** Set once, when the player dies. */
      endTime: 0,
    };

    this._spawnBudget = 0;
    /** Interpolated camera target; the renderer reads this, the sim owns it. */
    this.focusX = 0;
    this.focusY = 0;
    this.shake = 0;

    this.reset(opts.seed ?? 1);
  }

  // --------------------------------------------------------------------------
  // Lifecycle
  // --------------------------------------------------------------------------

  /** @param {number} [seed] */
  reset(seed) {
    if (seed !== undefined) this.rng.seed(seed);
    this.entities.reset();
    this.particles.clear();
    this.floaters.clear();
    this.grid.clear();
    this.stacks.fill(0);
    this.weapons.length = 0;
    this.stats = computeStats(this.stacks);
    this._spawnBudget = 0;
    this.shake = 0;

    const r = this.run;
    r.time = 0;
    r.level = 1;
    r.xp = 0;
    r.xpNeeded = xpForLevel(1);
    r.kills = 0;
    r.damageDealt = 0;
    r.damageTaken = 0;
    r.pendingLevelUps = 0;
    r.over = false;
    r.endTime = 0;

    const e = this.entities;
    const id = e.create(KIND.PLAYER);
    this.player = id;
    e.place(id, 0, 0);
    e.radius[id] = PLAYER.radius;
    e.maxHp[id] = PLAYER.maxHp;
    e.hp[id] = PLAYER.maxHp;
    e.sprite[id] = SPRITE.PLAYER;
    e.rot[id] = -Math.PI / 2;
    e.cooldown[id] = 0;
    this.focusX = 0;
    this.focusY = 0;

    this.grantWeapon('bolt');
    this.bus.emit(EV.RUN_START, this.run);
  }

  /** @param {number} halfDiag world units from screen centre to a corner */
  setViewExtent(halfDiag) {
    this.viewHalfDiag = Math.max(160, halfDiag);
  }

  get maxHp() {
    return PLAYER.maxHp + this.stats.maxHpBonus;
  }

  get hp() {
    return this.entities.hp[this.player];
  }

  /** True while the simulation should be held for an upgrade choice. */
  get awaitingUpgrade() {
    return this.run.pendingLevelUps > 0;
  }

  // --------------------------------------------------------------------------
  // Tick
  // --------------------------------------------------------------------------

  /**
   * Advances one fixed step. Callers must not tick while `run.over` or
   * `awaitingUpgrade` is set; both are gates the shell owns.
   * @param {number} dt seconds, always SIM tick period
   * @param {{moveX: number, moveY: number, aimX: number, aimY: number, aimActive: boolean}} input
   */
  tick(dt, input) {
    if (this.run.over) return;
    const e = this.entities;

    e.snapshotTransforms();
    this.run.time += dt;

    this._updatePlayer(dt, input);
    // Built from last tick's positions: separation and target acquisition both
    // tolerate one frame of staleness, and rebuilding once here rather than per
    // system keeps the cost at a single O(n) pass.
    this._rebuildGrid();
    this._fireWeapons(dt, input);
    this._updateEnemies(dt);
    this._integrateProjectiles(dt);
    // Rebuilt against post-movement positions, because collision must not be
    // stale: a darter covers ~2.2 units per tick and clipping through a
    // projectile is the kind of bug players notice and cannot describe.
    this._rebuildGrid();
    this._collideProjectiles(dt);
    this._collidePlayer(dt);
    this._updateOrbs(dt);
    this._runDirector(dt);

    this.particles.update(dt);
    this.floaters.update(dt);
    // Exponential decay is frame-rate independent and cannot overshoot; the
    // epsilon cut stops an asymptotic tail from jittering the camera forever.
    this.shake *= Math.exp(-FX.shakeDecay * dt);
    if (this.shake < 0.05) this.shake = 0;

    e.flush();
  }

  _rebuildGrid() {
    const e = this.entities;
    const ids = e.groups[KIND.ENEMY];
    const n = e.groupCount[KIND.ENEMY];
    const live = this._gridIds;
    let m = 0;
    for (let i = 0; i < n; i++) {
      const id = ids[i];
      // Destruction is deferred to the tick boundary, so the group list can
      // still contain entities killed earlier this tick. They must not occupy
      // the broadphase or a bullet would spend its pierce on a corpse.
      if ((e.flags[id] & FLAG.ALIVE) === 0) continue;
      live[m++] = id;
    }
    // Compacting into a scratch buffer rather than passing a predicate keeps the
    // grid free of callbacks, which matters: a megamorphic closure here would
    // deoptimise the hottest loop in the frame.
    this.grid.build(live, m, e.x, e.y);
  }

  // --------------------------------------------------------------------------
  // Player
  // --------------------------------------------------------------------------

  _updatePlayer(dt, input) {
    const e = this.entities;
    const id = this.player;
    const s = this.stats;

    // `flash` doubles as the invulnerability clock: one timer means the visual
    // blink and the actual immunity window can never disagree.
    if (e.flash[id] > 0) e.flash[id] -= dt;

    if (s.regen > 0 && e.hp[id] > 0) {
      const cap = this.maxHp;
      if (e.hp[id] < cap) e.hp[id] = Math.min(cap, e.hp[id] + s.regen * dt);
    }

    const maxV = PLAYER.speed * s.moveSpeedMul;
    let mx = input.moveX;
    let my = input.moveY;
    const mag = len(mx, my);
    if (mag > 1) {
      // Normalise rather than clamp per axis, or diagonal input would be faster.
      mx /= mag;
      my /= mag;
    }

    const tvx = mx * maxV;
    const tvy = my * maxV;
    // Vector-space approach: per-axis approach would let a diagonal turn
    // accelerate faster on one axis and visibly bend the path.
    const rate = mag > 0.001 ? PLAYER.accel : PLAYER.friction * maxV;
    let dvx = tvx - e.vx[id];
    let dvy = tvy - e.vy[id];
    const dv = len(dvx, dvy);
    const step = rate * dt;
    if (dv <= step || dv < 1e-5) {
      e.vx[id] = tvx;
      e.vy[id] = tvy;
    } else {
      e.vx[id] += (dvx / dv) * step;
      e.vy[id] += (dvy / dv) * step;
    }

    e.x[id] += e.vx[id] * dt;
    e.y[id] += e.vy[id] * dt;

    const bound = ARENA.half - PLAYER.radius;
    if (e.x[id] < -bound) { e.x[id] = -bound; e.vx[id] = 0; }
    else if (e.x[id] > bound) { e.x[id] = bound; e.vx[id] = 0; }
    if (e.y[id] < -bound) { e.y[id] = -bound; e.vy[id] = 0; }
    else if (e.y[id] > bound) { e.y[id] = bound; e.vy[id] = 0; }

    this.focusX = e.x[id];
    this.focusY = e.y[id];

    // Facing follows aim when the player is aiming, otherwise travel direction,
    // otherwise it holds: snapping to a default angle on release looks broken.
    if (input.aimActive && (input.aimX || input.aimY)) {
      e.rot[id] = Math.atan2(input.aimY, input.aimX);
    } else if (mag > 0.001) {
      e.rot[id] = Math.atan2(my, mx);
    }
  }

  _collidePlayer(dt) {
    const e = this.entities;
    const id = this.player;
    if (e.hp[id] <= 0) return;

    const px = e.x[id];
    const py = e.y[id];
    const reach = PLAYER.radius + MAX_ENEMY_RADIUS;
    const n = this.grid.query(px, py, reach, this._query);

    for (let i = 0; i < n; i++) {
      const eid = this._query[i];
      if (!e.isAlive(eid)) continue;
      if (e.cooldown[eid] > 0) continue;
      const r = PLAYER.radius + e.radius[eid];
      if (dist2(px, py, e.x[eid], e.y[eid]) > r * r) continue;
      const type = ENEMY_TYPES[e.typeId[eid]];
      e.cooldown[eid] = type.contactCooldown;
      this._hurtPlayer(e.damage[eid], e.x[eid], e.y[eid]);
      if (this.run.over) return;
    }

    // Enemy projectiles are tested here rather than in their own pass so a
    // single frame cannot deal both contact and projectile damage through iframes.
    const bIds = e.groups[KIND.ENEMY_BULLET];
    const bn = e.groupCount[KIND.ENEMY_BULLET];
    for (let i = 0; i < bn; i++) {
      const bid = bIds[i];
      if (!e.isAlive(bid)) continue;
      const r = PLAYER.radius + e.radius[bid];
      if (dist2(px, py, e.x[bid], e.y[bid]) > r * r) continue;
      e.destroy(bid);
      this.particles.burst(this.rng, e.x[bid], e.y[bid], 5, 40, 150, 0.3, 3, TINT.COAL);
      this._hurtPlayer(e.damage[bid], e.x[bid], e.y[bid]);
      if (this.run.over) return;
    }
  }

  _hurtPlayer(rawAmount, srcX, srcY) {
    const e = this.entities;
    const id = this.player;
    if (e.flash[id] > 0 || this.run.over) return; // flash doubles as the iframe timer
    // Armor is subtractive with a floor, so stacking it trivialises chip damage
    // but never grants immunity to a bulwark.
    const amount = Math.max(1, rawAmount - this.stats.armor);
    e.hp[id] = Math.max(0, e.hp[id] - amount);
    e.flash[id] = PLAYER.iframes;
    this.run.damageTaken += amount;
    this.shake = Math.min(24, this.shake + FX.shakeOnHurt);

    const dx = e.x[id] - srcX;
    const dy = e.y[id] - srcY;
    const d = len(dx, dy) || 1;
    this.particles.burst(this.rng, e.x[id], e.y[id], 10, 70, 240, 0.4, 4, TINT.BLOOD, dx / d, dy / d, 1.1);
    this.floaters.spawn(e.x[id], e.y[id] - 18, -amount, TINT.BLOOD, false);

    this.ev.x = e.x[id];
    this.ev.y = e.y[id];
    this.ev.amount = amount;
    this.bus.emit(EV.PLAYER_HURT, this.ev);

    if (e.hp[id] <= 0) this._endRun();
  }

  _endRun() {
    if (this.run.over) return;
    const e = this.entities;
    const id = this.player;
    this.run.over = true;
    this.run.endTime = this.run.time;
    this.particles.burst(this.rng, e.x[id], e.y[id], 48, 60, 420, 1.1, 7, TINT.EMBER);
    this.shake = 24;
    this.bus.emit(EV.RUN_END, this.run);
  }

  // --------------------------------------------------------------------------
  // Weapons
  // --------------------------------------------------------------------------

  /** @param {string} key @returns {boolean} false if already owned */
  grantWeapon(key) {
    const index = WEAPON_BY_KEY[key];
    if (index === undefined) return false;
    if (this.weapons.some((w) => w.index === index)) return false;
    this.weapons.push({ index, level: 1, cooldown: 0 });
    this.bus.emit(EV.WEAPON_GAINED, WEAPONS[index]);
    return true;
  }

  ownsWeapon(key) {
    const index = WEAPON_BY_KEY[key];
    return index !== undefined && this.weapons.some((w) => w.index === index);
  }

  weaponLevel(key) {
    const index = WEAPON_BY_KEY[key];
    const w = this.weapons.find((s) => s.index === index);
    return w ? w.level : 0;
  }

  _fireWeapons(dt, input) {
    const s = this.stats;
    for (let i = 0; i < this.weapons.length; i++) {
      const slot = this.weapons[i];
      slot.cooldown -= dt;
      // Bounded catch-up: a very short cooldown must not fire an unbounded
      // number of times in one tick, which would spike allocation and audio.
      let shots = 0;
      while (slot.cooldown <= 0 && shots < 3) {
        const cd = weaponCooldown(slot.index, slot.level) / Math.max(0.05, s.fireRateMul);
        slot.cooldown += cd;
        this._fireWeapon(slot, input);
        shots++;
      }
      if (slot.cooldown < 0) slot.cooldown = 0;
    }
  }

  _fireWeapon(slot, input) {
    const e = this.entities;
    const pid = this.player;
    const def = WEAPONS[slot.index];
    const s = this.stats;
    const px = e.x[pid];
    const py = e.y[pid];

    let dirX = Math.cos(e.rot[pid]);
    let dirY = Math.sin(e.rot[pid]);
    if (!input.aimActive) {
      const target = this._nearestEnemy(px, py, TARGET_RANGE);
      if (target !== -1) {
        const dx = e.x[target] - px;
        const dy = e.y[target] - py;
        const d = len(dx, dy) || 1;
        dirX = dx / d;
        dirY = dy / d;
        e.rot[pid] = Math.atan2(dirY, dirX);
      }
    }

    const damage = weaponDamage(slot.index, slot.level) * s.damageMul;

    if (def.pattern === PATTERN.SWEEP) {
      this._fireSweep(slot, def, px, py, dirX, dirY, damage);
      this.bus.emit(EV.SHOT_FIRED, def);
      return;
    }

    const count = weaponCount(slot.index, slot.level) + (def.pattern === PATTERN.AIMED ? s.extraProjectiles : 0);
    const speed = PROJECTILE.speed * def.speed * s.projectileSpeedMul;
    const pierce = def.pierce + s.extraPierce;
    const baseAngle = Math.atan2(dirY, dirX);

    for (let i = 0; i < count; i++) {
      let angle;
      if (def.pattern === PATTERN.RADIAL) {
        // Rotate the ring by the run clock so successive bursts do not stack
        // into identical spoke patterns.
        angle = baseAngle + (i / count) * TAU + this.run.time * 0.9;
      } else {
        const offset = count === 1 ? 0 : (i - (count - 1) / 2) * def.spread;
        angle = baseAngle + offset;
      }
      this._spawnBullet(px, py, angle, speed, damage, pierce, def, slot.level);
    }
    this.bus.emit(EV.SHOT_FIRED, def);
  }

  _spawnBullet(x, y, angle, speed, damage, pierce, def, level) {
    const e = this.entities;
    const id = e.create(KIND.BULLET);
    if (id === -1) return -1;
    const r = weaponRadius(WEAPON_BY_KEY[def.key], level);
    e.place(id, x + Math.cos(angle) * 10, y + Math.sin(angle) * 10);
    e.vx[id] = Math.cos(angle) * speed;
    e.vy[id] = Math.sin(angle) * speed;
    e.rot[id] = angle;
    e.radius[id] = def.pattern === PATTERN.SWEEP ? PROJECTILE.radius : r;
    e.damage[id] = damage;
    e.pierce[id] = pierce;
    e.ttl[id] = def.ttl;
    e.sprite[id] = def.sprite;
    e.scale[id] = 1;
    return id;
  }

  _fireSweep(slot, def, px, py, dirX, dirY, damage) {
    const e = this.entities;
    const reach = weaponRadius(slot.index, slot.level);
    const halfAngle = def.spread;
    const baseAngle = Math.atan2(dirY, dirX);
    const n = this.grid.query(px, py, reach + MAX_ENEMY_RADIUS, this._query);
    let hits = 0;
    for (let i = 0; i < n; i++) {
      const eid = this._query[i];
      if (!e.isAlive(eid)) continue;
      const dx = e.x[eid] - px;
      const dy = e.y[eid] - py;
      const rr = reach + e.radius[eid];
      if (dx * dx + dy * dy > rr * rr) continue;
      // Angular test uses a dot product against the facing vector: cheaper than
      // atan2 per candidate and free of seam handling.
      const d = len(dx, dy) || 1;
      const dot = (dx / d) * dirX + (dy / d) * dirY;
      if (dot < Math.cos(halfAngle)) continue;
      this._damageEnemy(eid, damage, px, py);
      hits++;
    }
    // The arc is drawn from particles, so it reads identically at any zoom and
    // needs no extra sprite or shader.
    const arcParticles = 16;
    for (let i = 0; i < arcParticles; i++) {
      const a = baseAngle + (i / (arcParticles - 1) - 0.5) * halfAngle * 2;
      const rad = reach * this.rng.float(0.72, 1);
      this.particles.spawn(
        px + Math.cos(a) * rad,
        py + Math.sin(a) * rad,
        Math.cos(a) * 60,
        Math.sin(a) * 60,
        this.rng.float(0.14, 0.26),
        this.rng.float(3, 6),
        TINT.QUENCH,
        9
      );
    }
    if (hits > 0) this.shake = Math.min(24, this.shake + 1.4);
  }

  /** @returns {number} entity id or -1 */
  _nearestEnemy(x, y, range) {
    const e = this.entities;
    const n = this.grid.query(x, y, range, this._query);
    let best = -1;
    let bestD = range * range;
    for (let i = 0; i < n; i++) {
      const id = this._query[i];
      if (!e.isAlive(id)) continue;
      const d = dist2(x, y, e.x[id], e.y[id]);
      if (d < bestD) {
        bestD = d;
        best = id;
      }
    }
    return best;
  }

  // --------------------------------------------------------------------------
  // Enemies
  // --------------------------------------------------------------------------

  _updateEnemies(dt) {
    const e = this.entities;
    const pid = this.player;
    const px = e.x[pid];
    const py = e.y[pid];
    const ids = e.groups[KIND.ENEMY];
    const n = e.groupCount[KIND.ENEMY];
    const bound = ARENA.half;

    for (let i = 0; i < n; i++) {
      const id = ids[i];
      if ((e.flags[id] & FLAG.ALIVE) === 0) continue;
      const type = ENEMY_TYPES[e.typeId[id]];
      if (e.cooldown[id] > 0) e.cooldown[id] -= dt;
      if (e.flash[id] > 0) e.flash[id] -= dt;
      e.phase[id] += dt;

      const dx = px - e.x[id];
      const dy = py - e.y[id];
      const dist = len(dx, dy) || 1;
      const nx = dx / dist;
      const ny = dy / dist;
      const speed = e.speed[id];
      let vx = 0;
      let vy = 0;

      switch (type.behavior) {
        case BEHAVIOR.DARTER: {
          // Lateral oscillation makes darters hard to lead without making them
          // dodge deterministically: the phase is seeded per entity on spawn.
          const wobble = Math.sin(e.phase[id] * 7.5) * 0.62;
          vx = (nx - ny * wobble) * speed;
          vy = (ny + nx * wobble) * speed;
          break;
        }
        case BEHAVIOR.SPITTER: {
          const R = type.range;
          let drive = 0;
          if (dist < R.tooClose) drive = -1;
          else if (dist > R.tooFar) drive = 1;
          vx = nx * speed * drive;
          vy = ny * speed * drive;
          // Strafe so a kiting spitter is not a stationary target.
          const strafe = Math.sin(e.phase[id] * 1.6) * 0.55;
          vx += -ny * speed * strafe;
          vy += nx * speed * strafe;
          if (e.ttl[id] > 0) e.ttl[id] -= dt;
          if (e.ttl[id] <= 0 && dist < R.fire) {
            e.ttl[id] = type.reload;
            this._spawnEnemyBullet(e.x[id], e.y[id], nx, ny, type);
          }
          break;
        }
        case BEHAVIOR.BULWARK:
        case BEHAVIOR.CHASE:
        default:
          vx = nx * speed;
          vy = ny * speed;
          break;
      }

      // Separation keeps a crowd readable: without it 200 chasers collapse into
      // one opaque blob occupying a single collider's worth of space.
      const sepR = e.radius[id] * 2.1;
      const qn = this.grid.query(e.x[id], e.y[id], sepR, this._query);
      let sx = 0;
      let sy = 0;
      let considered = 0;
      for (let k = 0; k < qn && considered < SEPARATION_NEIGHBOURS; k++) {
        const other = this._query[k];
        if (other === id || !e.isAlive(other)) continue;
        const ox = e.x[id] - e.x[other];
        const oy = e.y[id] - e.y[other];
        const minD = e.radius[id] + e.radius[other];
        const d2 = ox * ox + oy * oy;
        if (d2 >= minD * minD || d2 < 1e-6) continue;
        const d = Math.sqrt(d2);
        const push = (minD - d) / minD;
        sx += (ox / d) * push;
        sy += (oy / d) * push;
        considered++;
      }
      if (considered > 0) {
        vx += sx * speed * 1.35;
        vy += sy * speed * 1.35;
      }

      e.vx[id] = vx;
      e.vy[id] = vy;
      e.x[id] += vx * dt;
      e.y[id] += vy * dt;
      e.rot[id] = Math.atan2(ny, nx);

      if (e.x[id] < -bound) e.x[id] = -bound;
      else if (e.x[id] > bound) e.x[id] = bound;
      if (e.y[id] < -bound) e.y[id] = -bound;
      else if (e.y[id] > bound) e.y[id] = bound;
    }
  }

  _spawnEnemyBullet(x, y, nx, ny, type) {
    const e = this.entities;
    const id = e.create(KIND.ENEMY_BULLET);
    if (id === -1) return;
    e.place(id, x + nx * 14, y + ny * 14);
    e.vx[id] = nx * type.shotSpeed;
    e.vy[id] = ny * type.shotSpeed;
    e.rot[id] = Math.atan2(ny, nx);
    e.radius[id] = 6;
    e.damage[id] = type.shotDamage;
    e.ttl[id] = 3.2;
    e.sprite[id] = SPRITE.ENEMY_BOLT;
  }

  _damageEnemy(id, amount, srcX, srcY) {
    const e = this.entities;
    const crit = this.stats.critChance > 0 && this.rng.chance(this.stats.critChance);
    const dealt = crit ? amount * PLAYER.critMultiplier : amount;
    e.hp[id] -= dealt;
    e.flash[id] = 0.09;
    this.run.damageDealt += dealt;
    this.floaters.spawn(e.x[id], e.y[id] - e.radius[id], Math.round(dealt), crit ? TINT.BONE : TINT.EMBER, crit);

    const dx = e.x[id] - srcX;
    const dy = e.y[id] - srcY;
    const d = len(dx, dy) || 1;
    this.particles.burst(this.rng, e.x[id], e.y[id], FX.hitSparks, 40, 190, 0.3, 3.4, TINT.EMBER, dx / d, dy / d, 0.9);

    this.ev.x = e.x[id];
    this.ev.y = e.y[id];
    this.ev.amount = dealt;
    this.ev.crit = crit;
    this.ev.typeId = e.typeId[id];
    this.ev.elite = (e.flags[id] & FLAG.ELITE) !== 0;
    this.bus.emit(EV.ENEMY_HIT, this.ev);

    if (e.hp[id] <= 0) this._killEnemy(id);
  }

  _killEnemy(id) {
    const e = this.entities;
    if (!e.isAlive(id)) return;
    const elite = (e.flags[id] & FLAG.ELITE) !== 0;
    const x = e.x[id];
    const y = e.y[id];
    e.destroy(id);
    this.run.kills++;

    this.particles.burst(this.rng, x, y, elite ? FX.killSparks * 3 : FX.killSparks, 50, 300, 0.5, elite ? 6 : 4.4, elite ? TINT.COAL : TINT.EMBER);
    this.shake = Math.min(24, this.shake + (elite ? FX.shakeOnElite : FX.shakeOnKill));

    this._spawnOrb(x, y, e.xpValue[id]);

    this.ev.x = x;
    this.ev.y = y;
    this.ev.elite = elite;
    this.ev.typeId = e.typeId[id];
    this.bus.emit(EV.ENEMY_KILLED, this.ev);
  }

  _spawnOrb(x, y, value) {
    const e = this.entities;
    const id = e.create(KIND.ORB);
    if (id === -1) return;
    const a = this.rng.angle();
    const sp = this.rng.float(20, 70);
    e.place(id, x, y);
    e.vx[id] = Math.cos(a) * sp;
    e.vy[id] = Math.sin(a) * sp;
    e.radius[id] = 7;
    e.xpValue[id] = value;
    e.ttl[id] = XP.orbLifetime;
    e.sprite[id] = SPRITE.ORB;
    e.scale[id] = value >= 6 ? 1.5 : 1;
  }

  // --------------------------------------------------------------------------
  // Projectiles
  // --------------------------------------------------------------------------

  _integrateProjectiles(dt) {
    const e = this.entities;
    const bound = ARENA.half + 40;
    for (let ki = 0; ki < PROJECTILE_KINDS.length; ki++) {
      const kind = PROJECTILE_KINDS[ki];
      const ids = e.groups[kind];
      const n = e.groupCount[kind];
      for (let i = 0; i < n; i++) {
        const id = ids[i];
        if ((e.flags[id] & FLAG.ALIVE) === 0) continue;
        e.ttl[id] -= dt;
        if (e.cooldown[id] > 0) e.cooldown[id] -= dt;
        if (e.ttl[id] <= 0) {
          e.destroy(id);
          continue;
        }
        e.x[id] += e.vx[id] * dt;
        e.y[id] += e.vy[id] * dt;
        if (e.x[id] < -bound || e.x[id] > bound || e.y[id] < -bound || e.y[id] > bound) e.destroy(id);
      }
    }
  }

  _collideProjectiles(dt) {
    const e = this.entities;
    const ids = e.groups[KIND.BULLET];
    const n = e.groupCount[KIND.BULLET];

    for (let i = 0; i < n; i++) {
      const bid = ids[i];
      if (!e.isAlive(bid)) continue;
      const bx = e.x[bid];
      const by = e.y[bid];
      const br = e.radius[bid];
      const qn = this.grid.query(bx, by, br + MAX_ENEMY_RADIUS, this._query);

      for (let k = 0; k < qn; k++) {
        const eid = this._query[k];
        if (!e.isAlive(eid)) continue;
        // Generation check: without it a piercing bullet's "already hit" lock
        // could suppress a legitimate hit on a different enemy that recycled
        // the same slot within the re-hit window.
        if (e.lastHit[bid] === eid && e.lastHitGen[bid] === e.gen[eid] && e.cooldown[bid] > 0) continue;
        const r = br + e.radius[eid];
        if (dist2(bx, by, e.x[eid], e.y[eid]) > r * r) continue;

        this._damageEnemy(eid, e.damage[bid], bx, by);

        if (e.pierce[bid] > 0) {
          e.pierce[bid] -= 1;
          e.lastHit[bid] = eid;
          e.lastHitGen[bid] = e.gen[eid];
          e.cooldown[bid] = PROJECTILE.rehitDelay;
        } else {
          e.destroy(bid);
          break;
        }
      }
    }
  }

  // --------------------------------------------------------------------------
  // Pickups and progression
  // --------------------------------------------------------------------------

  _updateOrbs(dt) {
    const e = this.entities;
    this._capOrbs();
    const pid = this.player;
    const px = e.x[pid];
    const py = e.y[pid];
    const magnet = PLAYER.pickupRadius * this.stats.pickupRadiusMul;
    const magnet2 = magnet * magnet;
    const ids = e.groups[KIND.ORB];
    const n = e.groupCount[KIND.ORB];

    for (let i = 0; i < n; i++) {
      const id = ids[i];
      // Skipping the already-dead is not defensive padding: _capOrbs may have
      // credited and destroyed an orb moments ago, and without this guard an orb
      // sitting inside pickup range would be paid out a second time.
      if ((e.flags[id] & FLAG.ALIVE) === 0) continue;
      e.ttl[id] -= dt;
      if (e.ttl[id] <= 0) {
        e.destroy(id);
        continue;
      }
      const dx = px - e.x[id];
      const dy = py - e.y[id];
      const d2 = dx * dx + dy * dy;

      if (d2 < magnet2) {
        const d = Math.sqrt(d2) || 1;
        // Acceleration rather than a fixed speed, so orbs stream in with a
        // sense of mass instead of teleporting the moment they enter range.
        e.vx[id] += (dx / d) * PLAYER.magnetAccel * dt;
        e.vy[id] += (dy / d) * PLAYER.magnetAccel * dt;
      } else {
        // Outside pickup range orbs still converge, slowly. Without this the
        // player physically cannot reach everything a saturated field drops and
        // a third of all experience expires on the floor.
        const d = Math.sqrt(d2) || 1;
        const tvx = (dx / d) * PLAYER.orbDriftSpeed;
        const tvy = (dy / d) * PLAYER.orbDriftSpeed;
        const k = Math.exp(-PLAYER.orbDriftDamp * dt);
        e.vx[id] = tvx + (e.vx[id] - tvx) * k;
        e.vy[id] = tvy + (e.vy[id] - tvy) * k;
      }

      e.x[id] += e.vx[id] * dt;
      e.y[id] += e.vy[id] * dt;

      const pick = PLAYER.radius + e.radius[id];
      if (d2 < pick * pick) {
        const value = e.xpValue[id];
        const ox = e.x[id];
        const oy = e.y[id];
        e.destroy(id);
        this._collectXp(value, ox, oy);
      }
    }
  }

  /**
   * Enforces the orb ceiling by crediting the excess directly. Deleting them
   * would silently delete progression the player earned; crediting them keeps
   * the economy exact while bounding entity count and broadphase work.
   */
  _capOrbs() {
    const e = this.entities;
    const ids = e.groups[KIND.ORB];
    const total = e.groupCount[KIND.ORB];
    // Destruction is deferred to the tick boundary, so the group list still
    // holds orbs killed earlier this tick. Counting the list rather than the
    // living entities would under-report and let the ceiling drift upward.
    let live = 0;
    for (let i = 0; i < total; i++) if (e.isAlive(ids[i])) live++;
    let excess = live - LIMITS.maxOrbs;
    if (excess <= 0) return;
    for (let i = 0; i < total && excess > 0; i++) {
      const id = ids[i];
      if (!e.isAlive(id)) continue;
      const value = e.xpValue[id];
      const x = e.x[id];
      const y = e.y[id];
      e.destroy(id);
      this._collectXp(value, x, y);
      excess--;
    }
  }

  _collectXp(value, x, y) {
    const gained = value * this.stats.xpMul;
    this.run.xp += gained;
    this.ev.x = x;
    this.ev.y = y;
    this.ev.amount = gained;
    this.bus.emit(EV.XP_COLLECTED, this.ev);

    // A while loop, not an if: a late-game orb worth 36 XP can legitimately
    // clear two or three thresholds at once, and each owes the player a card.
    while (this.run.xp >= this.run.xpNeeded) {
      this.run.xp -= this.run.xpNeeded;
      this.run.level += 1;
      this.run.xpNeeded = xpForLevel(this.run.level);
      this.run.pendingLevelUps += 1;
      this.ev.level = this.run.level;
      this.bus.emit(EV.LEVEL_UP, this.ev);
    }
  }

  /**
   * Picks `count` distinct, currently-legal upgrades. Returns fewer than
   * requested only when the catalogue genuinely has fewer available, which is
   * why the caller must render from the returned length rather than assume 3.
   * @param {number} count
   * @returns {number[]} upgrade indices
   */
  rollUpgradeChoices(count) {
    const ctx = {
      ownsWeapon: (k) => this.ownsWeapon(k),
      weaponLevel: (k) => this.weaponLevel(k),
      stacks: (k) => this.stacks[UPGRADE_BY_KEY[k]] | 0,
      level: this.run.level,
    };
    let poolSize = 0;
    for (let i = 0; i < UPGRADES.length; i++) {
      const u = UPGRADES[i];
      if (this.stacks[i] >= u.max) continue;
      if (u.levelWeapon && !this.ownsWeapon(u.levelWeapon)) continue;
      if (u.available && !u.available(ctx)) continue;
      this._choicePool[poolSize] = i;
      this._choiceWeights[poolSize] = u.weight;
      poolSize++;
    }

    /** @type {number[]} */
    const out = [];
    for (let n = 0; n < count && poolSize > 0; n++) {
      const pick = this.rng.weightedIndex(this._choiceWeights, poolSize);
      if (pick === -1) break;
      out.push(this._choicePool[pick]);
      // Swap-remove from the candidate pool so one roll cannot offer duplicates.
      poolSize--;
      this._choicePool[pick] = this._choicePool[poolSize];
      this._choiceWeights[pick] = this._choiceWeights[poolSize];
    }
    return out;
  }

  /**
   * Consumes one banked level-up.
   * @param {number} index into UPGRADES
   * @returns {boolean} whether it applied
   */
  applyUpgrade(index) {
    if (this.run.pendingLevelUps <= 0) return false;
    const u = UPGRADES[index];
    if (!u) return false;
    if (this.stacks[index] >= u.max) return false;

    this.stacks[index] += 1;
    this.run.pendingLevelUps -= 1;

    if (u.grantWeapon) this.grantWeapon(u.grantWeapon);
    if (u.levelWeapon) {
      const wi = WEAPON_BY_KEY[u.levelWeapon];
      const slot = this.weapons.find((w) => w.index === wi);
      if (slot) slot.level += 1;
    }

    const previousMax = this.maxHp;
    this.stats = computeStats(this.stacks);
    const e = this.entities;
    // Max-HP increases grant the delta as current HP too, otherwise "+20 max"
    // reads as a downgrade at low health.
    const grew = this.maxHp - previousMax;
    if (grew > 0) e.hp[this.player] = Math.min(this.maxHp, e.hp[this.player] + grew);
    if (u.heals) {
      e.hp[this.player] = Math.min(this.maxHp, e.hp[this.player] + u.heals);
      this.bus.emit(EV.PLAYER_HEALED, this.ev);
    }
    e.maxHp[this.player] = this.maxHp;

    this.bus.emit(EV.UPGRADE_PICKED, u);
    return true;
  }

  // --------------------------------------------------------------------------
  // Spawn director
  // --------------------------------------------------------------------------

  /** Run progress in [0,1]; every difficulty curve is a function of this. */
  get ramp() {
    return clamp(this.run.time / DIRECTOR.rampSeconds, 0, 1);
  }

  _runDirector(dt) {
    const e = this.entities;
    const t = this.ramp;
    // Base rate ramps super-linearly, with a slow sine so pressure arrives in
    // waves; a flat rate reads as static noise rather than escalating threat.
    const pulse = 1 + 0.35 * Math.sin(this.run.time * 0.28);
    const rate = (1.15 + 9 * Math.pow(t, 1.35)) * pulse;
    this._spawnBudget += rate * dt;

    const alive = e.groupCount[KIND.ENEMY];
    if (alive >= LIMITS.maxEnemies) {
      // Hold the budget at a small positive value instead of accumulating: on
      // return from a saturated field, a large debt would dump 200 enemies at once.
      this._spawnBudget = Math.min(this._spawnBudget, 2);
      return;
    }

    let guard = 0;
    while (this._spawnBudget >= 1 && e.groupCount[KIND.ENEMY] < LIMITS.maxEnemies && guard < 40) {
      this._spawnBudget -= 1;
      guard++;
      this._spawnOne(t);
    }
  }

  _spawnOne(t) {
    const e = this.entities;
    let count = 0;
    for (let i = 0; i < ENEMY_TYPES.length; i++) {
      const w = ENEMY_TYPES[i].weight(t);
      this._weights[i] = w > 0 ? w : 0;
      count++;
    }
    const pick = this.rng.weightedIndex(this._weights, count);
    if (pick === -1) return;
    const type = ENEMY_TYPES[pick];

    const dist = Math.max(DIRECTOR.minSpawnDistance, this.viewHalfDiag * DIRECTOR.spawnRingScale);
    const px = e.x[this.player];
    const py = e.y[this.player];
    const bound = ARENA.half - type.radius * type.scale;

    // Try a handful of angles so a player pinned in a corner does not get
    // enemies materialising on top of them after the arena clamp.
    let sx = px;
    let sy = py;
    let ok = false;
    for (let attempt = 0; attempt < 8; attempt++) {
      const a = this.rng.angle();
      const cx = clamp(px + Math.cos(a) * dist, -bound, bound);
      const cy = clamp(py + Math.sin(a) * dist, -bound, bound);
      sx = cx;
      sy = cy;
      if (dist2(px, py, cx, cy) >= (dist * 0.72) * (dist * 0.72)) {
        ok = true;
        break;
      }
    }
    if (!ok) {
      // Fallback: the far side of the arena from the player, which is always
      // legal and always off screen.
      sx = clamp(-px * 0.9, -bound, bound);
      sy = clamp(-py * 0.9, -bound, bound);
    }

    const id = e.create(KIND.ENEMY);
    if (id === -1) return;
    const elite = this.rng.chance(DIRECTOR.maxEliteChance * t);
    // Health scales quadratically and speed only mildly: making late enemies
    // fast makes them unavoidable, making them tanky makes them a DPS check.
    const hpScale = 1 + 1.3 * t + 1.7 * t * t;
    const speedScale = 1 + 0.24 * t;

    e.place(id, sx, sy);
    e.typeId[id] = pick;
    e.sprite[id] = type.sprite;
    e.radius[id] = type.radius * type.scale * (elite ? DIRECTOR.eliteScaleUp : 1);
    e.scale[id] = type.scale * (elite ? DIRECTOR.eliteScaleUp : 1);
    e.maxHp[id] = type.hp * hpScale * (elite ? DIRECTOR.eliteHpScale : 1);
    e.hp[id] = e.maxHp[id];
    e.speed[id] = type.speed * speedScale * (elite ? DIRECTOR.eliteSpeedScale : 1);
    e.damage[id] = type.damage * (elite ? 1.5 : 1);
    e.xpValue[id] = type.xp * (elite ? DIRECTOR.eliteXpScale : 1);
    e.flags[id] = FLAG.ALIVE | (elite ? FLAG.ELITE : 0);
    // Randomised phase so a cohort spawned on the same tick does not oscillate
    // in lockstep, which reads as a single organism rather than a crowd.
    e.phase[id] = this.rng.float(0, 10);
    e.ttl[id] = type.reload ? this.rng.float(0.4, type.reload) : 0;
  }

  // --------------------------------------------------------------------------
  // Diagnostics
  // --------------------------------------------------------------------------

  /**
   * FNV-1a over quantised state. Used by the determinism test: two runs with the
   * same seed and inputs must agree here at every tick.
   */
  hash() {
    const e = this.entities;
    let h = 0x811c9dc5;
    const mix = (v) => {
      h ^= v | 0;
      h = Math.imul(h, 0x01000193) >>> 0;
    };
    mix(this.rng.state);
    mix(this.run.kills);
    mix(this.run.level);
    mix(Math.round(this.run.xp * 100));
    mix(Math.round(this.run.time * 1000));
    for (let k = 0; k < KIND.COUNT; k++) {
      const ids = e.groups[k];
      const n = e.groupCount[k];
      mix(n);
      for (let i = 0; i < n; i++) {
        const id = ids[i];
        mix(Math.round(e.x[id] * 32));
        mix(Math.round(e.y[id] * 32));
        mix(Math.round(e.hp[id] * 16));
      }
    }
    return h >>> 0;
  }
}
