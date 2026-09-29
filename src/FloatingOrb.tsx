import React, { useState, useEffect } from "react";
import { MambaPresence, MambaPresenceState } from "./MambaPresence";

export const FloatingOrb: React.FC = () => {
  const [presenceState, setPresenceState] = useState<MambaPresenceState>("idle");

  useEffect(() => {
    // Listen for state changes from Electron shell
    if (window.mambaDesktop?.onState) {
      const cleanup = window.mambaDesktop.onState((state: string) => {
        const validStates: MambaPresenceState[] = [
          "idle",
          "listening",
          "thinking",
          "speaking",
          "permission",
          "error",
        ];
        if (validStates.includes(state as MambaPresenceState)) {
          setPresenceState(state as MambaPresenceState);
        } else if (state === "DORMANT") {
          setPresenceState("idle");
        } else if (state === "STARTING") {
          setPresenceState("thinking");
        }
      });
      return cleanup;
    }
  }, []);

  const handleClick = () => {
    if (window.mambaDesktop?.activate) {
      window.mambaDesktop.activate();
    }
  };

  return (
    <div
      className="relative flex items-center justify-center select-none bg-transparent"
      style={{
        width: "100%",
        height: "100%",
        overflow: "hidden",
        WebkitAppRegion: "drag",
        userSelect: "none",
        background: "transparent",
      } as React.CSSProperties}
    >
      {/* 3D Orb Visual Canvas Layer - pointer-events: none so it doesn't block window dragging from transparent areas */}
      <div
        className="absolute inset-0 flex items-center justify-center pointer-events-none bg-transparent"
        style={{ width: "100%", height: "100%", overflow: "hidden" }}
      >
        <MambaPresence
          state={presenceState}
          variant="orb"
          size={220}
          showLabel={false}
        />
      </div>

      {/* Central interactive click target right over the core and inner particle area */}
      <div
        onClick={handleClick}
        style={{
          width: 120,
          height: 120,
          borderRadius: "50%",
          WebkitAppRegion: "no-drag",
          cursor: "pointer",
          zIndex: 10,
          background: "transparent",
        } as React.CSSProperties}
        title="Mamba AI — Click to activate"
      />
    </div>
  );
};

export default FloatingOrb;
