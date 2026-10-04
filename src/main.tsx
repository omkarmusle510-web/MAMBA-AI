import React from "react";
import ReactDOM from "react-dom/client";
import "./index.css";
import { MambaApp } from "./MambaApp";
import { FloatingOrb } from "./FloatingOrb";
import { PresencePreview } from "./PresencePreview";

const rootElement = document.getElementById("root");
const params = new URLSearchParams(window.location.search);
const isOrbMode = params.get("mode") === "orb" || window.location.hash === "#orb";
const isPreviewMode = params.get("mode") === "preview";

if (rootElement) {
  ReactDOM.createRoot(rootElement).render(
    <React.StrictMode>
      {isPreviewMode ? (
        <PresencePreview />
      ) : isOrbMode ? (
        <FloatingOrb />
      ) : (
        <MambaApp />
      )}
    </React.StrictMode>
  );
}
