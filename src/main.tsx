import React from "react";
import ReactDOM from "react-dom/client";
import { MambaApp } from "./MambaApp";

const rootElement = document.getElementById("root");

if (rootElement) {
  ReactDOM.createRoot(rootElement).render(
    <React.StrictMode>
      <MambaApp />
    </React.StrictMode>
  );
}

