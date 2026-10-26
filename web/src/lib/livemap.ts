// Draws a live field (livefield.ts) on the canvas over the viewer's map, on the
// 100 m grid of the rendered maps. The terrain is loaded as soon as the viewer
// starts, while the API is still answering; until the field is ready the
// rendered map of the last full hour stays in view. Relief, outline and places
// come from the same images the rendered maps are made of.
import { decodeGrid, field, isolines, paint, precipitationField, probe, relativeHumidity, sample, type Extent, type Grid, type Point } from "./livefield";

type Scale = { levels: number[]; colors: string[] };
export type LiveInput =
  | { kind: "temperature"; t: Point[] }
  | { kind: "humidity"; t: Point[]; td: Point[] }
  | { kind: "precipitation"; p: Point[] }; // mm per hour
type Clear = { below: number; color: string };
const INK = "#25282b";
// Isotherms as on the rendered maps: every 4 degrees thin, the freezing level bold
// (matplotlib line widths in points at 100 dpi, in pixels of the 1690 px map).
const THIN = [-12, -8, -4, 4, 8, 12, 16, 20, 24];

async function bitmap(url: string) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  // The pixels are numbers, not colours: no colour management.
  return createImageBitmap(await res.blob(), { colorSpaceConversion: "none", premultiplyAlpha: "none" });
}

export class LiveMap {
  canvas: HTMLCanvasElement;
  base: string;
  extent: Extent;
  job = 0;
  private assets: Promise<{ grid: Grid; base: ImageBitmap; overlay: ImageBitmap }> | null = null;
  private shown: { values: Float32Array; grid: Grid } | null = null;

  constructor(canvas: HTMLCanvasElement, base: string, extent: Extent) {
    this.canvas = canvas;
    this.base = base;
    this.extent = extent;
  }

  /** Starts loading terrain and map layers; a failed load is tried again next time. */
  load() {
    if (!this.assets) {
      const meta: Promise<{ shade: [number, number] }> = fetch(`${this.base}/live.json`).then((r) => r.json());
      this.assets = Promise.all([meta, bitmap(`${this.base}/geo.png`), bitmap(`${this.base}/base.webp`),
        bitmap(`${this.base}/overlay.png`)]).then(([m, img, base, overlay]) => {
        const c = new OffscreenCanvas(img.width, img.height);
        const ctx = c.getContext("2d")!;
        ctx.drawImage(img, 0, 0);
        const px = ctx.getImageData(0, 0, img.width, img.height).data;
        return { grid: decodeGrid(px, img.width, img.height, this.extent, m.shade), base, overlay };
      });
      this.assets.catch(() => (this.assets = null));
    }
    return this.assets;
  }

  /**
   * Draws the field; false if it cannot be drawn. A newer call cancels an older
   * one. With clear, values below clear.below show the paper.
   */
  async show(input: LiveInput, scale: Scale, clear?: Clear): Promise<boolean> {
    const job = ++this.job;
    try {
      const { grid, base, overlay } = await this.load();
      if (job !== this.job) return false;
      let values: Float32Array;
      if (input.kind === "precipitation") {
        values = sample(precipitationField(input.p, this.extent), grid).map((v) => Math.max(0, v));
      } else {
        values = sample(field(input.t, this.extent), grid);
        if (input.kind === "humidity") {
          const td = sample(field(input.td, this.extent), grid);
          values = values.map((v, k) => (Number.isNaN(v) ? v : relativeHumidity(v, td[k])));
        }
      }
      const lines = input.kind === "temperature" ? isolines(values, grid, [...THIN, 0]) : null;
      this.draw(grid, paint(values, grid, scale.levels, scale.colors, clear), base, overlay, lines);
      this.shown = { values, grid };
      return true;
    } catch {
      return false;
    }
  }

  cancel() {
    this.job++;
    this.shown = null;
  }

  /** The drawn field's value and the elevation under a point of the map (map pixels). */
  probe(x: number, y: number) {
    if (!this.shown) return null;
    const { values, grid } = this.shown;
    return probe(values, grid, (x / this.canvas.width) * grid.w, (y / this.canvas.height) * grid.h);
  }

  private draw(g: Grid, px: Uint8ClampedArray, base: ImageBitmap, overlay: ImageBitmap, lines: Map<number, number[]> | null) {
    const { width: w, height: h } = this.canvas;
    const ctx = this.canvas.getContext("2d")!;
    const off = new OffscreenCanvas(g.w, g.h);
    off.getContext("2d")!.putImageData(new ImageData(px, g.w, g.h), 0, 0);
    ctx.drawImage(base, 0, 0, w, h);
    ctx.drawImage(off, 0, 0, w, h);
    if (lines) {
      ctx.save();
      ctx.scale(w / g.w, h / g.h);
      ctx.strokeStyle = INK;
      ctx.lineCap = "round";
      for (const [level, seg] of lines) {
        ctx.globalAlpha = level === 0 ? 0.9 : 0.45;
        ctx.lineWidth = (level === 0 ? 1.6 : 0.45) * (100 / 72) * (g.w / w);
        ctx.beginPath();
        for (let i = 0; i < seg.length; i += 4) {
          ctx.moveTo(seg[i], seg[i + 1]);
          ctx.lineTo(seg[i + 2], seg[i + 3]);
        }
        ctx.stroke();
      }
      ctx.restore();
    }
    ctx.drawImage(overlay, 0, 0, w, h);
  }
}
