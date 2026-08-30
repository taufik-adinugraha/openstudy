import { defineConfig } from "astro/config";

// Case E app — static build; served independently of every other case (port 4328).
export default defineConfig({
  output: "static",
  base: "/airquality",
});
