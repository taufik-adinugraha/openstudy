import { defineConfig } from "astro/config";

// Landing page — static build, deployed to Cloudflare Pages.
export default defineConfig({
  output: "static",
});
