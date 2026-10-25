// Temperature and humidity fields computed in the browser from the newest station
// values, the same way scripts/fields.py does it for the rendered maps:
// a height trend with an optional inversion hinge, plus ordinary kriging of
// what the trend leaves (exponential variogram, 30 km range) on the 1 km
// working grid, interpolated onto the terrain. tests/test_live_parity.py
// checks both give the same numbers.
//
// No DOM here, so node can run it for the parity test.

export type Point = { x: number; y: number; alt: number; v: number };
export type Extent = [xmin: number, xmax: number, ymin: number, ymax: number];
/** Elevation grid of the map (north up), with the province marked and its relief shade. */
export type Grid = { w: number; h: number; extent: Extent; dem: Float32Array; inside: Uint8Array; shade: Float32Array };
export type Field = { trend: (z: number) => number; hinge: number | null; coarse: Coarse };
type Coarse = { gx: Float64Array; gy: Float64Array; z: Float64Array }; // gy south -> north

export const COARSE = 1000;
const RANGE = 30000;
const NUGGET = 0.1;

/** Least squares through the normal equations; columns are the design's functions. */
function lstsq(cols: number[][], t: number[]) {
  const m = cols.length;
  const a = cols.map((ci) => cols.map((cj) => ci.reduce((s, x, k) => s + x * cj[k], 0)));
  const b = cols.map((ci) => ci.reduce((s, x, k) => s + x * t[k], 0));
  const coef = solve(a, b);
  let sse = 0;
  for (let k = 0; k < t.length; k++) {
    let p = 0;
    for (let j = 0; j < m; j++) p += coef[j] * cols[j][k];
    sse += (p - t[k]) ** 2;
  }
  return { coef, sse };
}

/** Gaussian elimination with partial pivoting; a and b are copied. */
export function solve(a0: number[][], b0: number[]) {
  const n = b0.length;
  const a = a0.map((r) => r.slice());
  const b = b0.slice();
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(a[r][c]) > Math.abs(a[p][c])) p = r;
    [a[c], a[p]] = [a[p], a[c]];
    [b[c], b[p]] = [b[p], b[c]];
    for (let r = c + 1; r < n; r++) {
      const f = a[r][c] / a[c][c];
      if (!f) continue;
      for (let k = c; k < n; k++) a[r][k] -= f * a[c][k];
      b[r] -= f * b[c];
    }
  }
  const x = new Array<number>(n).fill(0);
  for (let r = n - 1; r >= 0; r--) {
    let s = b[r];
    for (let k = r + 1; k < n; k++) s -= a[r][k] * x[k];
    x[r] = s / a[r][r];
  }
  return x;
}

/** t = a + b*z + c*max(z - h, 0), or a straight line; see fields.hinge_fit. */
export function hingeFit(z: number[], t: number[]) {
  const one = z.map(() => 1);
  const line = lstsq([one, z], t);
  let best = { f: (zz: number) => line.coef[0] + line.coef[1] * zz, sse: line.sse, h: null as number | null };
  for (let h = 600; h <= 2000; h += 100) {
    const above = z.filter((x) => x > h).length;
    if (above < 8 || z.length - above < 8) continue;
    const { coef, sse } = lstsq([one, z, z.map((x) => Math.max(x - h, 0))], t);
    if (sse < 0.8 * best.sse && sse < 0.85 * line.sse) {
      best = { f: (zz) => coef[0] + coef[1] * zz + coef[2] * Math.max(zz - h, 0), sse, h };
    }
  }
  return { trend: best.f, hinge: best.h };
}

const variance = (v: number[]) => {
  const m = v.reduce((a, b) => a + b, 0) / v.length;
  return v.reduce((a, b) => a + (b - m) ** 2, 0) / v.length;
};

/** Ordinary kriging on the coarse grid of the extent, as pykrige does it. */
export function krige(pts: { x: number; y: number }[], values: number[], extent: Extent): Coarse {
  const [xmin, xmax, ymin, ymax] = extent;
  const gx = Float64Array.from({ length: Math.ceil((xmax - xmin - COARSE / 2) / COARSE) }, (_, i) => xmin + COARSE / 2 + i * COARSE);
  const gy = Float64Array.from({ length: Math.ceil((ymax - ymin - COARSE / 2) / COARSE) }, (_, i) => ymin + COARSE / 2 + i * COARSE);
  const z = new Float64Array(gx.length * gy.length);
  const n = values.length;
  const sill = variance(values);
  if (sill < 1e-6) return { gx, gy, z: z.fill(values.reduce((a, b) => a + b, 0) / n) };
  const nugget = NUGGET * sill;
  const psill = sill - nugget;
  const gamma = (d: number) => psill * (1 - Math.exp(-d / (RANGE / 3))) + nugget;
  // pykrige's system: -gamma off the diagonal, 0 on it, a row and column of ones.
  const a: number[][] = [];
  for (let i = 0; i <= n; i++) {
    a.push([]);
    for (let j = 0; j <= n; j++) {
      a[i].push(i === n || j === n ? (i === j ? 0 : 1) : i === j ? 0 : -gamma(Math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y)));
    }
  }
  // The system is symmetric, so z(p) = alpha . b(p) with alpha = A^-1 [values, 0].
  const alpha = solve(a, [...values, 0]);
  let k = 0;
  for (let r = 0; r < gy.length; r++) {
    for (let c = 0; c < gx.length; c++) {
      let s = alpha[n];
      for (let i = 0; i < n; i++) {
        const d = Math.hypot(gx[c] - pts[i].x, gy[r] - pts[i].y);
        if (d > 1e-10) s -= alpha[i] * gamma(d);
      }
      z[k++] = s;
    }
  }
  return { gx, gy, z };
}

/** Trend and kriged residuals of one variable from its stations. */
export function field(points: Point[], extent: Extent): Field {
  const { trend, hinge } = hingeFit(points.map((p) => p.alt), points.map((p) => p.v));
  const resid = points.map((p) => p.v - trend(p.alt));
  return { trend, hinge, coarse: krige(points, resid, extent) };
}

/** Linear interpolation of the coarse grid, extrapolating past its edge cells. */
function coarseAt(c: Coarse, x: number, y: number) {
  const nx = c.gx.length;
  const ny = c.gy.length;
  const fx = Math.min(Math.max((x - c.gx[0]) / COARSE, 0), nx - 1);
  const fy = Math.min(Math.max((y - c.gy[0]) / COARSE, 0), ny - 1);
  const i = Math.min(Math.floor(fx), nx - 2);
  const j = Math.min(Math.floor(fy), ny - 2);
  const tx = (x - c.gx[i]) / COARSE;
  const ty = (y - c.gy[j]) / COARSE;
  const z00 = c.z[j * nx + i], z10 = c.z[j * nx + i + 1];
  const z01 = c.z[(j + 1) * nx + i], z11 = c.z[(j + 1) * nx + i + 1];
  return z00 * (1 - tx) * (1 - ty) + z10 * tx * (1 - ty) + z01 * (1 - tx) * ty + z11 * tx * ty;
}

/** The field on every inside cell of the grid (NaN outside), row by row from the north. */
export function sample(f: Field, g: Grid) {
  const [xmin, xmax, ymin, ymax] = g.extent;
  const dx = (xmax - xmin) / g.w;
  const dy = (ymax - ymin) / g.h;
  const out = new Float32Array(g.w * g.h).fill(NaN);
  for (let r = 0; r < g.h; r++) {
    const y = ymax - (r + 0.5) * dy;
    for (let c = 0; c < g.w; c++) {
      const k = r * g.w + c;
      if (!g.inside[k]) continue;
      out[k] = f.trend(g.dem[k]) + coarseAt(f.coarse, xmin + (c + 0.5) * dx, y);
    }
  }
  return out;
}

// Relief as render_maps.py draws it: LightSource(azdeg=315, altdeg=38).hillshade
// with vert_exag 1.6, stretched by the intensity range of the whole grid.
const AZ = ((90 - 315) * Math.PI) / 180;
const ALT = (38 * Math.PI) / 180;
const LIGHT = [Math.cos(AZ) * Math.cos(ALT), Math.sin(AZ) * Math.cos(ALT), Math.sin(ALT)];
const VERT_EXAG = 1.6;

/**
 * The grid from the pixels of geo-*.png (elevation in red and green, the
 * province in blue), with the shade of every inside cell.
 */
export function decodeGrid(rgba: Uint8ClampedArray, w: number, h: number, extent: Extent, range: [number, number]): Grid {
  const n = w * h;
  const dem = new Float32Array(n);
  const inside = new Uint8Array(n);
  for (let k = 0; k < n; k++) {
    dem[k] = rgba[4 * k] * 256 + rgba[4 * k + 1];
    inside[k] = rgba[4 * k + 2] > 127 ? 1 : 0;
  }
  const cell = (extent[1] - extent[0]) / w;
  const shade = new Float32Array(n);
  const [lo, hi] = range;
  // np.gradient: central differences, one-sided at the edge of the grid.
  const at = (r: number, c: number) => dem[r * w + c];
  for (let r = 0; r < h; r++) {
    for (let c = 0; c < w; c++) {
      const k = r * w + c;
      if (!inside[k]) continue;
      const r0 = Math.max(r - 1, 0), r1 = Math.min(r + 1, h - 1);
      const c0 = Math.max(c - 1, 0), c1 = Math.min(c + 1, w - 1);
      const ex = (VERT_EXAG * (at(r, c1) - at(r, c0))) / ((c1 - c0) * cell);
      const ey = (VERT_EXAG * (at(r1, c) - at(r0, c))) / ((r1 - r0) * -cell);
      const len = Math.hypot(ex, ey, 1);
      const i = (-ex * LIGHT[0] - ey * LIGHT[1] + LIGHT[2]) / len;
      shade[k] = Math.min(1, Math.max(0, (i - lo) / (hi - lo)));
    }
  }
  return { w, h, extent, dem, inside, shade };
}

/**
 * RGBA pixels of the field in its colour bands, multiplied with the relief as
 * Renderer.base does; transparent outside the province.
 */
export function paint(values: Float32Array, g: Grid, levels: number[], colors: string[]) {
  const rgb = colors.map((c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16) / 255));
  const out = new Uint8ClampedArray(g.w * g.h * 4);
  for (let k = 0; k < values.length; k++) {
    const v = values[k];
    if (!g.inside[k] || Number.isNaN(v)) continue;
    const col = rgb[band(levels, v, colors.length)];
    const lit = 0.62 + 0.45 * g.shade[k];
    out[4 * k] = Math.min(1, col[0] * lit) * 255;
    out[4 * k + 1] = Math.min(1, col[1] * lit) * 255;
    out[4 * k + 2] = Math.min(1, col[2] * lit) * 255;
    out[4 * k + 3] = 255;
  }
  return out;
}

// Magnus formula over water, as in meteo/derive.py.
const A = 17.625;
const B = 243.04;
export const dewpoint = (t: number, rh: number) => {
  if (rh <= 0) return null;
  const g = Math.log(Math.min(rh, 100) / 100) + (A * t) / (B + t);
  return (B * g) / (A - g);
};
export const relativeHumidity = (t: number, td: number) =>
  Math.min(100, Math.max(1, 100 * Math.exp((A * td) / (B + td) - (A * t) / (B + t))));

/** Colour band of v: band i starts at levels[i]; clamped to the n colours. */
export function band(levels: number[], v: number, n: number) {
  let i = 0;
  while (i < n - 1 && v >= levels[i + 1]) i++;
  return i;
}

/** The field at one point of the map with its elevation. */
export const valueAt = (f: Field, x: number, y: number, z: number) => f.trend(z) + coarseAt(f.coarse, x, y);
