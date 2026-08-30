import { defineConfig } from "astro/config";

// Case D app — static build; deployed independently of every other case.
export default defineConfig({
  output: "static",
  base: "/narrative",
  devToolbar: { enabled: false },
});
