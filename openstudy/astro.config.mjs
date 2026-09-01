import { defineConfig } from "astro/config";

// openstudy.id — the studio's own site. Static: it explains an arrangement and links
// to instruments, and nothing on it needs a server.
//
// Port 4340 in development. It is served at the site root in production, so no `base`
// — the ten instruments keep their own slugs beneath it.
export default defineConfig({
  output: "static",
});
