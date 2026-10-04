import React, { useEffect, useMemo, useRef, useState } from "react";
import { Mic, MicOff, Send, Square } from "lucide-react";

import { motionEnabled } from "./motionPrefs";
import { loadSettings } from "./settingsStore";

interface ComposerProps {
  onMessageSubmit: (message: string) => void;
  voiceActive: boolean;
  onToggleVoice: () => void;
  disabled?: boolean;
  /** A turn is in flight — offer a stop control instead of sending. */
  busy?: boolean;
  onCancelTurn?: () => void;
}

/**
 * Bottom chat composer: rounded dark input with integrated mic + send.
 * Presentation only — submission and voice wiring live in MambaApp.
 */
export const Composer: React.FC<ComposerProps> = ({
  onMessageSubmit,
  voiceActive,
  onToggleVoice,
  disabled = false,
  busy = false,
  onCancelTurn,
}) => {
  const [message, setMessage] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [cancelSent, setCancelSent] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const motionOn = useMemo(() => motionEnabled(loadSettings().animations), []);

  // The turn is resolved (complete/cancelled/error) — re-arm the stop button.
  useEffect(() => {
    if (!busy) setCancelSent(false);
  }, [busy]);

  const submit = () => {
    const trimmed = message.trim();
    if (!trimmed || isSending || disabled) return;
    setIsSending(true);
    onMessageSubmit(trimmed);
    setMessage("");
    if (inputRef.current) inputRef.current.style.height = "auto";
    setTimeout(() => setIsSending(false), 400);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const autoResize = () => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    // With motion off the field grows by itself (CSS caps it at 140px); the
    // scripted height only exists to make the growth smooth.
    if (motionOn) el.style.height = `${Math.min(el.scrollHeight, 128)}px`;
  };

  const cancel = () => {
    if (!onCancelTurn || cancelSent) return;
    setCancelSent(true);
    onCancelTurn();
  };

  return (
    <div className="composer">
      <textarea
        ref={inputRef}
        className="composer-input"
        value={message}
        onChange={(e) => {
          setMessage(e.target.value);
          autoResize();
        }}
        onKeyDown={handleKeyDown}
        placeholder="Ask Mamba..."
        aria-label="Ask Mamba"
        rows={1}
        disabled={disabled}
      />
      <button
        type="button"
        className={`composer-btn${voiceActive ? " mic-live" : ""}`}
        onClick={onToggleVoice}
        title={voiceActive ? "Stop voice session" : "Start voice session"}
        aria-label={voiceActive ? "Stop voice session" : "Start voice session"}
        aria-pressed={voiceActive}
      >
        {voiceActive ? <MicOff /> : <Mic />}
      </button>
      {busy && onCancelTurn ? (
        <button
          type="button"
          className="composer-btn is-stop"
          onClick={cancel}
          disabled={cancelSent}
          title={cancelSent ? "Cancelling…" : "Stop this turn"}
          aria-label={cancelSent ? "Cancelling" : "Stop this turn"}
        >
          <Square />
        </button>
      ) : (
        <button
          type="button"
          className="composer-btn send"
          onClick={submit}
          disabled={!message.trim() || isSending || disabled}
          title="Send message"
          aria-label="Send message"
        >
          <Send />
        </button>
      )}
    </div>
  );
};

export default Composer;
