import { defineConfig } from "astro/config";

// Case G app — static build; deployed independently of every other case.
export default defineConfig({
  output: "static",
  base: "/transit",
});
