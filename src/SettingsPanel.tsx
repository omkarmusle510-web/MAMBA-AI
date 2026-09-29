import React from "react";
import { motion, AnimatePresence } from "motion/react";
import { X, Power, Mic } from "lucide-react";

interface SettingsPanelProps {
  isOpen: boolean;
  onClose: () => void;
  autoStart: boolean;
  onAutoStartChange: (enabled: boolean) => void;
  /** True when running inside the Electron desktop shell. */
  isDesktop: boolean;
  /** Wake-word listening (orb renderer hosts the listener). */
  wakeWordEnabled: boolean;
  wakePhrase: string;
  onWakeWordChange: (enabled: boolean) => void;
}

export const SettingsPanel: React.FC<SettingsPanelProps> = ({
  isOpen,
  onClose,
  autoStart,
  onAutoStartChange,
  isDesktop,
  wakeWordEnabled,
  wakePhrase,
  onWakeWordChange,
}) => {
  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.15 }}
          className="absolute top-16 right-6 z-30 w-80 rounded-2xl border border-white/10 bg-slate-900/95 p-5 shadow-2xl backdrop-blur"
        >
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-bold tracking-widest text-slate-200">
              SETTINGS
            </h2>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/10 transition"
              title="Close settings"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Windows startup toggle */}
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-sm text-slate-200">
                <Power className="w-4 h-4 text-cyan-400" />
                Start Mamba with Windows
              </div>
              <p className="mt-1 text-xs leading-relaxed text-slate-500">
                Launch the desktop shell at login. Mamba opens dormant — the
                tray, hotkey, and Orb stay available while the backend starts
                only when you activate it.
              </p>
              {!isDesktop && (
                <p className="mt-1 text-xs text-amber-400/80">
                  Applies to the Electron desktop app.
                </p>
              )}
            </div>
            <button
              role="switch"
              aria-checked={autoStart}
              aria-label="Start Mamba with Windows"
              onClick={() => onAutoStartChange(!autoStart)}
              className={`relative mt-1 h-6 w-11 shrink-0 rounded-full transition ${
                autoStart ? "bg-cyan-500" : "bg-white/10"
              }`}
            >
              <span
                className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${
                  autoStart ? "left-[22px]" : "left-0.5"
                }`}
              />
            </button>
          </div>

          {/* Wake-word toggle (desktop shell only: the orb renderer hosts it) */}
          <div className="mt-5 flex items-start justify-between gap-4 border-t border-white/10 pt-5">
            <div>
              <div className="flex items-center gap-2 text-sm text-slate-200">
                <Mic className="w-4 h-4 text-emerald-400" />
                Wake word
              </div>
              <p className="mt-1 text-xs leading-relaxed text-slate-500">
                Listen for &ldquo;{wakePhrase}&rdquo; while dormant. Detection
                runs locally in the Orb; nothing is recorded or sent until
                the phrase is heard.
              </p>
              {!isDesktop && (
                <p className="mt-1 text-xs text-amber-400/80">
                  Applies to the Electron desktop app.
                </p>
              )}
            </div>
            <button
              role="switch"
              aria-checked={wakeWordEnabled}
              aria-label="Wake word listening"
              onClick={() => onWakeWordChange(!wakeWordEnabled)}
              className={`relative mt-1 h-6 w-11 shrink-0 rounded-full transition ${
                wakeWordEnabled ? "bg-emerald-500" : "bg-white/10"
              }`}
            >
              <span
                className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${
                  wakeWordEnabled ? "left-[22px]" : "left-0.5"
                }`}
              />
            </button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default SettingsPanel;
