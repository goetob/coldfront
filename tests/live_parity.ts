// Runs web/src/lib/livefield.ts on a fixture from test_live_parity.py and
// prints its results as JSON. Node strips the types itself.
import { readFileSync } from "node:fs";
import { decodeGrid, field, precipitationField, valueAt, type Extent, type Point } from "../web/src/lib/livefield.ts";

const fx = JSON.parse(readFileSync(process.argv[2], "utf8")) as {
  kind?: "precipitation";
  extent: Extent;
  points: Point[];
  cells: [number, number, number][];
  crop: { w: number; h: number; rgba: number[]; range: [number, number] };
};
const f = fx.kind === "precipitation" ? precipitationField(fx.points, fx.extent) : field(fx.points, fx.extent);
const g = decodeGrid(Uint8ClampedArray.from(fx.crop.rgba), fx.crop.w, fx.crop.h, [0, fx.crop.w * 100, 0, fx.crop.h * 100], fx.crop.range);
console.log(JSON.stringify({
  hinge: f.hinge,
  top: f.top,
  values: fx.cells.map(([x, y, z]) => (fx.kind === "precipitation" ? Math.max(0, valueAt(f, x, y, z)) : valueAt(f, x, y, z))),
  shade: Array.from(g.shade),
}));
