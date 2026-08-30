import { defineConfig } from "astro/config";

// Case J app — static build; deployed independently of every other case.
// Port 4334, base /haze.
export default defineConfig({
  output: "static",
  base: "/haze",
});
