// Draws a live field (livefield.ts) on the canvas over the viewer's map, on the
// 100 m grid of the rendered maps. The terrain is loaded as soon as the viewer
// starts, while the API is still answering; until the field is ready the
// rendered map of the last full hour stays in view. Relief, outline and places
// come from the same images the rendered maps are made of.
import { decodeGrid, field, paint, relativeHumidity, sample, type Extent, type Grid, type Point } from "./livefield";

type Scale = { levels: number[]; colors: string[] };
export type LiveInput = { kind: "temperature"; t: Point[] } | { kind: "humidity"; t: Point[]; td: Point[] };

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

  /** Draws the field; false if it cannot be drawn. A newer call cancels an older one. */
  async show(input: LiveInput, scale: Scale): Promise<boolean> {
    const job = ++this.job;
    try {
      const { grid, base, overlay } = await this.load();
      if (job !== this.job) return false;
      let values = sample(field(input.t, this.extent), grid);
      if (input.kind === "humidity") {
        const td = sample(field(input.td, this.extent), grid);
        values = values.map((v, k) => (Number.isNaN(v) ? v : relativeHumidity(v, td[k])));
      }
      this.draw(grid, paint(values, grid, scale.levels, scale.colors), base, overlay);
      return true;
    } catch {
      return false;
    }
  }

  cancel() {
    this.job++;
  }

  private draw(g: Grid, px: Uint8ClampedArray, base: ImageBitmap, overlay: ImageBitmap) {
    const { width: w, height: h } = this.canvas;
    const ctx = this.canvas.getContext("2d")!;
    const off = new OffscreenCanvas(g.w, g.h);
    off.getContext("2d")!.putImageData(new ImageData(px, g.w, g.h), 0, 0);
    ctx.drawImage(base, 0, 0, w, h);
    ctx.drawImage(off, 0, 0, w, h);
    ctx.drawImage(overlay, 0, 0, w, h);
  }
}
