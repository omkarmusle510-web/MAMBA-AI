import React, { useState, useRef } from "react";
import { Mic, MicOff, Send } from "lucide-react";

interface ComposerProps {
  onMessageSubmit: (message: string) => void;
  voiceActive: boolean;
  onToggleVoice: () => void;
  disabled?: boolean;
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
}) => {
  const [message, setMessage] = useState("");
  const [isSending, setIsSending] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

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
    el.style.height = `${Math.min(el.scrollHeight, 128)}px`;
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
    </div>
  );
};

export default Composer;
