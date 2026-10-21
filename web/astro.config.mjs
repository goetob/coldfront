import { defineConfig } from "astro/config";

// GitHub Pages serves the project under /<repo>/; override locally with BASE=/.
export default defineConfig({
  site: process.env.SITE ?? "https://goetob.github.io",
  base: process.env.BASE ?? "/coldfront",
  trailingSlash: "ignore",
});
