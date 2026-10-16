import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// POC frontend config — talks to every backend microservice through the
// Traefik gateway (see docker-compose.yml), never directly to a service.
export default defineConfig({
  plugins: [react()],
  server: { host: "0.0.0.0", port: 5173 },
});
