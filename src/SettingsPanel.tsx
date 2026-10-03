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
          className="settings-panel"
        >
          <div className="settings-head">
            <h2 className="settings-title">
              Settings
            </h2>
            <button
              onClick={onClose}
              className="icon-btn"
              title="Close settings"
              aria-label="Close settings"
            >
              <X />
            </button>
          </div>

          {/* Windows startup toggle */}
          <div className="settings-row">
            <div>
              <div className="label">
                <Power style={{ color: "var(--cyan)" }} />
                Start Mamba with Windows
              </div>
              <p className="desc">
                Launch the desktop shell at login. Mamba opens dormant — the
                tray, hotkey, and Orb stay available while the backend starts
                only when you activate it.
              </p>
              {!isDesktop && (
                <p className="desc" style={{ color: "rgba(251,191,36,0.75)" }}>
                  Applies to the Electron desktop app.
                </p>
              )}
            </div>
            <button
              role="switch"
              aria-checked={autoStart}
              aria-label="Start Mamba with Windows"
              onClick={() => onAutoStartChange(!autoStart)}
              className="switch"
            />
          </div>

          {/* Wake-word toggle (desktop shell only: the orb renderer hosts it) */}
          <div className="settings-row">
            <div>
              <div className="label">
                <Mic style={{ color: "var(--emerald)" }} />
                Wake word
              </div>
              <p className="desc">
                Listen for &ldquo;{wakePhrase}&rdquo; while dormant. Detection
                runs locally in the Orb; nothing is recorded or sent until
                the phrase is heard.
              </p>
              {!isDesktop && (
                <p className="desc" style={{ color: "rgba(251,191,36,0.75)" }}>
                  Applies to the Electron desktop app.
                </p>
              )}
            </div>
            <button
              role="switch"
              aria-checked={wakeWordEnabled}
              aria-label="Wake word listening"
              onClick={() => onWakeWordChange(!wakeWordEnabled)}
              className="switch accent-emerald"
            />
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default SettingsPanel;
