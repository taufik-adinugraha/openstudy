import { defineConfig } from "astro/config";

// Flagship B app — static build; deployed independently of every other case.
export default defineConfig({
  output: "static",
  base: "/trade",
});
