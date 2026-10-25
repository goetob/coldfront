// Draws a live field (livefield.ts) on the canvas over the viewer's map: first
// on the 250 m grid, which loads and computes in a moment, then on the 100 m
// grid of the rendered maps as soon as that is ready. Relief, outline and
// places come from the same images the rendered maps are made of.
import { decodeGrid, field, paint, relativeHumidity, sample, type Extent, type Grid, type Point } from "./livefield";

type Scale = { levels: number[]; colors: string[] };
export type LiveInput = { kind: "temperature"; t: Point[] } | { kind: "humidity"; t: Point[]; td: Point[] };
const GRIDS = ["geo-250.png", "geo-100.png"];

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
  private meta: Promise<{ shade: Record<string, [number, number]> }> | null = null;
  private grids = new Map<string, Promise<Grid>>();
  private layers: Promise<[ImageBitmap, ImageBitmap]> | null = null;

  constructor(canvas: HTMLCanvasElement, base: string, extent: Extent) {
    this.canvas = canvas;
    this.base = base;
    this.extent = extent;
  }

  grid(name: string) {
    let g = this.grids.get(name);
    if (!g) {
      this.meta ??= fetch(`${this.base}/live.json`).then((r) => r.json());
      g = Promise.all([this.meta, bitmap(`${this.base}/${name}`)]).then(([meta, img]) => {
        const c = new OffscreenCanvas(img.width, img.height);
        const ctx = c.getContext("2d")!;
        ctx.drawImage(img, 0, 0);
        const px = ctx.getImageData(0, 0, img.width, img.height).data;
        return decodeGrid(px, img.width, img.height, this.extent, meta.shade[name]);
      });
      g.catch(() => this.grids.delete(name));
      this.grids.set(name, g);
    }
    return g;
  }

  /**
   * Draws the field; resolves once the coarse picture shows (false if it
   * cannot be drawn) and calls onFine when the 100 m picture has replaced it.
   * A newer call cancels what is left of an older one.
   */
  async show(input: LiveInput, scale: Scale, onFine?: () => void): Promise<boolean> {
    const job = ++this.job;
    try {
      this.layers ??= Promise.all([bitmap(`${this.base}/base.webp`), bitmap(`${this.base}/overlay.png`)]);
      const fine = this.grid(GRIDS[1]); // starts loading right away
      const t = field(input.t, this.extent);
      const td = input.kind === "humidity" ? field(input.td, this.extent) : null;
      const values = (g: Grid) => {
        const tv = sample(t, g);
        if (!td) return tv;
        const dv = sample(td, g);
        return tv.map((v, k) => (Number.isNaN(v) ? v : relativeHumidity(v, dv[k])));
      };
      const [layers, coarse] = await Promise.all([this.layers, this.grid(GRIDS[0])]);
      if (job !== this.job) return false;
      this.draw(coarse, paint(values(coarse), coarse, scale.levels, scale.colors), layers);
      fine.then(async (g) => {
        // Let the coarse picture paint before the longer computation.
        await new Promise((ok) => setTimeout(ok, 30));
        if (job !== this.job) return;
        this.draw(g, paint(values(g), g, scale.levels, scale.colors), layers);
        onFine?.();
      }).catch(() => {}); // the coarse picture stays
      return true;
    } catch {
      return false;
    }
  }

  cancel() {
    this.job++;
  }

  private draw(g: Grid, px: Uint8ClampedArray, [base, overlay]: [ImageBitmap, ImageBitmap]) {
    const { width: w, height: h } = this.canvas;
    const ctx = this.canvas.getContext("2d")!;
    const off = new OffscreenCanvas(g.w, g.h);
    off.getContext("2d")!.putImageData(new ImageData(px, g.w, g.h), 0, 0);
    ctx.drawImage(base, 0, 0, w, h);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(off, 0, 0, w, h);
    ctx.drawImage(overlay, 0, 0, w, h);
    this.canvas.dataset.grid = String(Math.round((this.extent[1] - this.extent[0]) / g.w)); // metres per cell
  }
}
