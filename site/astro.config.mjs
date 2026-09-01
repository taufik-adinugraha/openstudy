import { defineConfig } from "astro/config";

// The demo lab's own landing page. It served the site root until openstudy.id took
// that over; it now lives at /lab so it stays reachable while the FMV material finds a
// permanent home. The case links inside it are absolute paths from the root, which is
// correct — the instruments sit at /forest, /rice and so on, not beneath /lab.
export default defineConfig({
  output: "static",
  base: "/lab",
});
