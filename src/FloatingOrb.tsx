import React, { useState, useEffect, useRef } from "react";
import { MambaPresence, MambaPresenceState } from "./MambaPresence";
import { WakeController } from "./wake/controller";
import { loadSettings } from "./settingsStore";

/**
 * Floating Orb — presentation only.
 *
 * The Orb visualizes shell/app state. It TEMPORARILY hosts the desktop wake
 * listener (the only renderer alive while DORMANT), but the listener itself
 * is a separate desktop interaction service (src/wake/) — not Orb logic.
 */
export const FloatingOrb: React.FC = () => {
  const [presenceState, setPresenceState] = useState<MambaPresenceState>("idle");
  // Mic-armed indicator: true while the wake listener holds no mic but is
  // actively listening for the wake phrase.
  const [wakeArmed, setWakeArmed] = useState(false);
  const wakeRef = useRef<WakeController | null>(null);
  const wakeEnabledRef = useRef<boolean>(false);

  useEffect(() => {
    // Listen for state changes from Electron shell
    let cleanupState: (() => void) | undefined;
    if (window.mambaDesktop?.onState) {
      cleanupState = window.mambaDesktop.onState((state: string) => {
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
        // Re-arm the wake listener when the session goes quiet. Re-arm is
        // idempotent; the controller ignores it while already running.
        if (state === "idle" || state === "DORMANT") {
          const ctl = wakeRef.current;
          if (ctl && wakeEnabledRef.current && !ctl.isRunning) {
            ctl.rearm();
          }
        }
      });
    }

    // Host the wake listener (desktop shell only). Structured as a service;
    // the Orb only displays its state via the mic indicator.
    const canHostWake = Boolean(window.mambaDesktop?.notifyWakeDetected);
    if (canHostWake) {
      const settings = loadSettings();
      wakeEnabledRef.current = settings.wakeWordEnabled !== false;
      const controller = new WakeController({
        phrase: settings.wakePhrase || "hey mamba",
        sensitivity: settings.sensitivity ?? 60,
        onWake: () => {
          setWakeArmed(false);
          try {
            window.mambaDesktop?.notifyWakeDetected?.();
          } catch {
            /* shell unreachable — stay dormant */
          }
        },
        onState: (s) => {
          setWakeArmed(s === "listening");
        },
      });
      wakeRef.current = controller;
      if (wakeEnabledRef.current) {
        controller.start();
      }

      // Runtime toggle from the main-window Settings panel.
      const cleanupWakeSetting = window.mambaDesktop?.onWakeSetting?.((enabled) => {
        wakeEnabledRef.current = enabled;
        if (enabled) {
          controller.rearm();
        } else {
          controller.stop();
          setWakeArmed(false);
        }
      });

      return () => {
        cleanupState?.();
        cleanupWakeSetting?.();
        controller.stop();
        wakeRef.current = null;
      };
    }

    return () => {
      cleanupState?.();
    };
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

      {/* Mic-armed indicator: visible while the wake listener is armed. */}
      {wakeArmed && (
        <div
          className="absolute pointer-events-none"
          style={{ bottom: 18, right: 18 }}
          title="Wake-word listening is on"
        >
          <div
            className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse"
            style={{ boxShadow: "0 0 8px #34d399" }}
          />
        </div>
      )}

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
