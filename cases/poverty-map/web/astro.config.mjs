import { defineConfig } from "astro/config";

// Case F app — static build; deployed independently of every other case.
export default defineConfig({
  output: "static",
  base: "/poverty",
});
