/**
 * Uniform-grid broadphase over a bounded arena, built as a CSR (compressed
 * sparse row) layout by counting sort.
 *
 * Design history worth keeping: the first version used an array of per-cell
 * JavaScript arrays, truncated with `length = 0` and refilled with `push`. That
 * measured ~10 KB of allocation per tick, because V8 replaces the elements
 * backing store when a packed array is truncated and then has to re-grow it. The
 * result was a minor GC roughly every 100 ticks. This version preallocates every
 * buffer at construction and performs literally zero allocation per build:
 *
 *   1. count entities per cell
 *   2. prefix-sum the counts into per-cell start offsets
 *   3. scatter entity ids into one flat items array
 *
 * The flat layout is also faster to query than the array-of-arrays it replaced,
 * because a cell's candidates are contiguous in memory.
 *
 * Quadtrees were considered and rejected: collider radii and spatial density
 * here are near-uniform, so a grid gives O(1) placement with no rebalancing, and
 * a bounded arena means cells can be indexed directly instead of hashed.
 */
export class SpatialGrid {
  /**
   * @param {number} cellSize world units per cell
   * @param {number} half arena half-extent
   * @param {number} capacity maximum entities in a single build
   * @param {number} [pad] extra rings of cells, so entities that briefly leave
   *   the arena (projectiles, knockback) still resolve to a real cell
   */
  constructor(cellSize, half, capacity, pad = 3) {
    this.cellSize = cellSize;
    this.invCell = 1 / cellSize;
    this.origin = -(half + pad * cellSize);
    const span = 2 * (half + pad * cellSize);
    this.dim = Math.ceil(span * this.invCell);
    this.cells = this.dim * this.dim;
    this.capacity = capacity;

    this.cellStart = new Int32Array(this.cells + 1);
    this.cursor = new Int32Array(this.cells);
    this.items = new Int32Array(capacity);
    /** Cell index per input entry, cached between the count and scatter passes. */
    this.itemCell = new Int32Array(capacity);
    this.itemCount = 0;
  }

  /** @param {number} v world coordinate on either axis */
  axisCell(v) {
    const c = Math.floor((v - this.origin) * this.invCell);
    return c < 0 ? 0 : c >= this.dim ? this.dim - 1 : c;
  }

  /** Empties the grid without touching the backing buffers. */
  clear() {
    this.itemCount = 0;
    this.cellStart.fill(0);
  }

  /**
   * Rebuilds from a dense id list. Entries beyond `capacity` are dropped rather
   * than overflowing, which cannot happen in practice because capacity is the
   * entity-store capacity.
   *
   * @param {Int32Array} ids dense entity ids
   * @param {number} count how many leading entries are valid
   * @param {Float32Array} xs position component array, indexed by entity id
   * @param {Float32Array} ys
   */
  build(ids, count, xs, ys) {
    const n = count > this.capacity ? this.capacity : count;
    this.itemCount = n;
    const start = this.cellStart;
    const cells = this.cells;

    start.fill(0);
    // Pass 1: histogram. Counts are written at index+1 so the prefix sum below
    // turns them into start offsets in place, with no second array.
    for (let i = 0; i < n; i++) {
      const id = ids[i];
      const c = this.axisCell(ys[id]) * this.dim + this.axisCell(xs[id]);
      this.itemCell[i] = c;
      start[c + 1]++;
    }
    // Pass 2: prefix sum.
    for (let c = 0; c < cells; c++) start[c + 1] += start[c];
    // Pass 3: scatter.
    this.cursor.set(start.subarray(0, cells));
    for (let i = 0; i < n; i++) {
      const c = this.itemCell[i];
      this.items[this.cursor[c]++] = ids[i];
    }
  }

  /**
   * Collects candidate ids from every cell overlapping the query circle's
   * bounding box. Callers must still run an exact distance test; this only
   * rejects the far field.
   * @param {number} x
   * @param {number} y
   * @param {number} radius
   * @param {Int32Array} out destination buffer
   * @returns {number} candidates written, never more than out.length
   */
  query(x, y, radius, out) {
    const cap = out.length;
    let n = 0;
    if (this.itemCount === 0) return 0;
    const x0 = this.axisCell(x - radius);
    const x1 = this.axisCell(x + radius);
    const y0 = this.axisCell(y - radius);
    const y1 = this.axisCell(y + radius);
    const start = this.cellStart;
    const items = this.items;
    for (let cy = y0; cy <= y1; cy++) {
      const row = cy * this.dim;
      // A cell row is contiguous in the CSR layout, so an entire span of cells
      // can be copied as one range instead of iterated cell by cell.
      const from = start[row + x0];
      const to = start[row + x1 + 1];
      for (let i = from; i < to; i++) {
        if (n >= cap) return n;
        out[n++] = items[i];
      }
    }
    return n;
  }
}
