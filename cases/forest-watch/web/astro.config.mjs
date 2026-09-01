import { defineConfig } from "astro/config";

// Case H app — static build; deployed independently of every other case.
// Port 4331 (4330 is the Provenance API).
export default defineConfig({
  output: "static",
  base: "/forest",
});
