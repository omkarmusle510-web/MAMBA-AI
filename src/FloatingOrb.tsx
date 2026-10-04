import React, { useState, useEffect, useRef } from "react";
import { MambaPresence, MambaPresenceState } from "./MambaPresence";
import { WakeController } from "./wake/controller";
import { wakeDiag } from "./wake/diag";
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
          "executing",
          "verifying",
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
    // TEMP DIAG: is the bridge present and is wake enabled at orb boot?
    wakeDiag(`orb boot: canHostWake=${canHostWake}`);
    if (canHostWake) {
      const settings = loadSettings();
      wakeEnabledRef.current = settings.wakeWordEnabled !== false;
      wakeDiag(`orb boot: wakeWordEnabled=${wakeEnabledRef.current} (phrase="${settings.wakePhrase || "hey mamba"}")`);
      const controller = new WakeController({
        phrase: settings.wakePhrase || "hey mamba",
        sensitivity: settings.sensitivity ?? 60,
        onWake: () => {
          setWakeArmed(false);
          // TEMP DIAG: trigger reached the orb host; notifying the shell.
          wakeDiag(`orb onWake: calling notifyWakeDetected()`);
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
        wakeDiag(`orb boot: invoking controller.start()`);
        controller.start();
      } else {
        wakeDiag(`orb boot: wake disabled in settings — controller NOT started`);
      }

      // Runtime toggle from the main-window Settings panel.
      const cleanupWakeSetting = window.mambaDesktop?.onWakeSetting?.((enabled) => {
        wakeDiag(`orb: wake setting changed -> ${enabled}`);
        wakeEnabledRef.current = enabled;
        if (enabled) {
          controller.rearm();
        } else {
          controller.stop();
          setWakeArmed(false);
        }
      });

      // Runtime phrase/sensitivity from the main-window Settings panel.
      const cleanupWakeOptions = window.mambaDesktop?.onWakeOptions?.((opts) => {
        const phrase =
          typeof opts?.phrase === "string" && opts.phrase.trim()
            ? opts.phrase.trim()
            : undefined;
        const sensitivity =
          typeof opts?.sensitivity === "number"
            ? Math.min(100, Math.max(0, opts.sensitivity))
            : undefined;
        const patch: { phrase?: string; sensitivity?: number } = {};
        if (phrase !== undefined) patch.phrase = phrase;
        if (sensitivity !== undefined) patch.sensitivity = sensitivity;
        controller.updateOptions(patch);
      });

      return () => {
        cleanupState?.();
        cleanupWakeSetting?.();
        cleanupWakeOptions?.();
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
      className="orb-window"
      style={{
        WebkitAppRegion: "drag",
        userSelect: "none",
      } as React.CSSProperties}
    >
      {/* Orb visual layer - pointer-events: none so it never blocks dragging */}
      <div className="orb-canvas-layer">
        <MambaPresence state={presenceState} size={208} />
      </div>

      {/* Mic-armed indicator: visible while the wake listener is armed. */}
      {wakeArmed && (
        <div
          className="wake-armed-dot"
          title="Wake-word listening is on"
        />
      )}

      {/* Central interactive click target right over the core and inner particle area */}
      <div
        onClick={handleClick}
        className="orb-click-target"
        style={{
          WebkitAppRegion: "no-drag",
        } as React.CSSProperties}
        title="Mamba AI — Click to activate"
      />
    </div>
  );
};

export default FloatingOrb;
