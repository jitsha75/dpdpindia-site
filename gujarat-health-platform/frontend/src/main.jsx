import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext.jsx";
import { RevealProvider } from "./context/RevealContext.jsx";
import App from "./App.jsx";
import "./styles.css";

// Shell of the Gujarat Health Integrated Data Platform.
// Every screen talks to the backend exclusively through relative
// `/api/<service-name>/<route>` calls, resolved by the API gateway —
// see frontend/src/api.js. Open the app at the gateway origin
// (http://localhost:8300), not directly at the Vite dev server port,
// so those relative calls resolve correctly.

createRoot(document.getElementById("root")).render(
  <BrowserRouter>
    <AuthProvider>
      <RevealProvider>
        <App />
      </RevealProvider>
    </AuthProvider>
  </BrowserRouter>
);
