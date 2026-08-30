import { defineConfig } from "astro/config";

// Case F app — static build; deployed independently of every other case.
// Port 4332: the scaffold said 4328, but Case E (air quality) took it first.
export default defineConfig({
  output: "static",
  base: "/poverty",
  server: { port: 4332, host: true },
});
