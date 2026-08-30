import { defineConfig } from "astro/config";

// Case E app — static build; served independently of every other case (port 4328).
export default defineConfig({
  output: "static",
  base: "/airquality",
  // The service runs `astro dev` so the page picks up regenerated view-models
  // without a rebuild; the dev toolbar would otherwise float over the page for
  // every visitor.
  devToolbar: { enabled: false },
});
