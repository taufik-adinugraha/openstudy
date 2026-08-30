import { defineConfig } from "astro/config";

// Case C app — static build; served independently of every other case (port 4327).
export default defineConfig({
  output: "static",
  base: "/jakarta",
});
