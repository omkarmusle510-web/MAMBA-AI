import React, { useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { X } from "lucide-react";
import { motionEnabled } from "./motionPrefs";

interface SettingsPanelProps {
  isOpen: boolean;
  onClose: () => void;
  /** True when running inside the Electron desktop shell. */
  isDesktop: boolean;

  autoStart: boolean;
  onAutoStartChange: (enabled: boolean) => void;
  animations: boolean;
  onAnimationsChange: (enabled: boolean) => void;

  /** Wake-word listening (the orb renderer hosts the listener). */
  wakeWordEnabled: boolean;
  onWakeWordChange: (enabled: boolean) => void;
  wakePhrase: string;
  onWakePhraseChange: (phrase: string) => void;
  sensitivity: number;
  onSensitivityChange: (sensitivity: number) => void;
}

const MIN = 0;
const MAX = 100;

/** Keeps a typed phrase on one line and lower-case, matching the matcher. */
function normalizePhrase(raw: string): string {
  return raw.trim().replace(/\s+/g, " ").toLowerCase();
}

export const SettingsPanel: React.FC<SettingsPanelProps> = ({
  isOpen,
  onClose,
  isDesktop,
  autoStart,
  onAutoStartChange,
  animations,
  onAnimationsChange,
  wakeWordEnabled,
  onWakeWordChange,
  wakePhrase,
  onWakePhraseChange,
  sensitivity,
  onSensitivityChange,
}) => {
  const animate = motionEnabled(animations);

  // Drafts commit on release (blur / Enter / pointer-up) so every keystroke or
  // drag tick does not write to localStorage and sync to the backend.
  const [phraseDraft, setPhraseDraft] = useState(wakePhrase);
  const [sensitivityDraft, setSensitivityDraft] = useState(sensitivity);

  useEffect(() => {
    setPhraseDraft(wakePhrase);
  }, [wakePhrase]);

  useEffect(() => {
    setSensitivityDraft(sensitivity);
  }, [sensitivity]);

  const commitPhrase = () => {
    const next = normalizePhrase(phraseDraft);
    if (!next || next === wakePhrase) {
      setPhraseDraft(wakePhrase);
      return;
    }
    onWakePhraseChange(next);
  };

  const commitSensitivity = () => {
    if (sensitivityDraft === sensitivity) return;
    onSensitivityChange(sensitivityDraft);
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={animate ? { opacity: 0, y: -8 } : false}
          animate={{ opacity: 1, y: 0 }}
          exit={animate ? { opacity: 0, y: -8 } : { opacity: 0 }}
          transition={{ duration: animate ? 0.18 : 0, ease: [0.16, 1, 0.3, 1] }}
          className="settings-panel"
          role="dialog"
          aria-label="Settings"
        >
          <div className="settings-head">
            <h2 className="settings-title">Settings</h2>
            <button
              onClick={onClose}
              className="icon-btn"
              title="Close settings"
              aria-label="Close settings"
            >
              <X />
            </button>
          </div>

          <section className="settings-section">
            <h3 className="label settings-section-title">General</h3>

            <div className="settings-row">
              <div className="settings-copy">
                <div className="label">Start Mamba with Windows</div>
                <p className="desc">
                  Launch the desktop shell at login. Mamba opens dormant — the
                  tray, hotkey, and Orb stay available while the backend starts
                  only when you activate it.
                </p>
                {!isDesktop && (
                  <p className="desc settings-note">
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

            <div className="settings-row">
              <div className="settings-copy">
                <div className="label">Motion</div>
                <p className="desc">
                  Short fades and glides across the interface. Turning it off
                  takes effect on the next interaction.
                </p>
              </div>
              <button
                role="switch"
                aria-checked={animations}
                aria-label="Motion"
                onClick={() => onAnimationsChange(!animations)}
                className="switch"
              />
            </div>
          </section>

          <section className="settings-section">
            <h3 className="label settings-section-title">Voice &amp; activation</h3>

            <div className="settings-row">
              <div className="settings-copy">
                <div className="label">Wake word</div>
                <p className="desc">
                  Listen for &ldquo;{wakePhrase}&rdquo; while dormant. Detection
                  runs locally in the Orb; nothing is recorded or sent until the
                  phrase is heard.
                </p>
                {!isDesktop && (
                  <p className="desc settings-note">
                    Applies to the Electron desktop app.
                  </p>
                )}
              </div>
              <button
                role="switch"
                aria-checked={wakeWordEnabled}
                aria-label="Wake word listening"
                onClick={() => onWakeWordChange(!wakeWordEnabled)}
                className="switch"
              />
            </div>

            <div className="settings-row is-stacked">
              <div className="settings-copy">
                <div className="label">Wake phrase</div>
                <p className="desc">
                  Say this to wake Mamba. Two or three plain words work best.
                </p>
              </div>
              <input
                type="text"
                className="field settings-input"
                value={phraseDraft}
                spellCheck={false}
                autoComplete="off"
                aria-label="Wake phrase"
                placeholder="hey mamba"
                onChange={(event) => setPhraseDraft(event.target.value)}
                onBlur={commitPhrase}
                onKeyDown={(event) => {
                  if (event.key !== "Enter") return;
                  event.preventDefault();
                  commitPhrase();
                  event.currentTarget.blur();
                }}
              />
            </div>

            <div className="settings-row is-stacked">
              <div className="settings-topline">
                <div className="label">Sensitivity</div>
                <span className="settings-value">{sensitivityDraft}</span>
              </div>
              <p className="desc">How ready Mamba should be to hear the wake word.</p>
              <input
                type="range"
                className="settings-range"
                min={MIN}
                max={MAX}
                step={1}
                value={sensitivityDraft}
                aria-label="Wake word sensitivity"
                style={{ "--range-fill": `${sensitivityDraft}%` } as React.CSSProperties}
                onChange={(event) => setSensitivityDraft(Number(event.target.value))}
                onPointerUp={commitSensitivity}
                onKeyUp={commitSensitivity}
                onBlur={commitSensitivity}
              />
            </div>
          </section>
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default SettingsPanel;
