import { defineConfig } from "astro/config";

// Case I app — static build; deployed independently of every other case.
// Port 4333, base /rice.  (4330 is the Pustaka API; 4331 forest; 4332 reserved.)
export default defineConfig({
  output: "static",
  base: "/rice",
});
