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
      className="w-screen h-screen flex items-center justify-center select-none overflow-hidden bg-transparent"
      style={{
        WebkitAppRegion: "drag",
        userSelect: "none",
      } as React.CSSProperties}
    >
      <div
        style={{
          WebkitAppRegion: "no-drag",
        } as React.CSSProperties}
        title="Mamba AI — Click to activate"
      >
        <MambaPresence
          state={presenceState}
          variant="orb"
          size={175}
          showLabel={false}
          onClick={handleClick}
        />
      </div>
    </div>
  );
};

export default FloatingOrb;
