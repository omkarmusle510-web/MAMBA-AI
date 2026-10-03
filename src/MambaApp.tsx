import React, { useState, useEffect, useRef } from "react";
import { MessageSquare, Globe, Mic, MicOff, Settings } from "lucide-react";

import { MambaAudioSession, LiveState } from "./audio";
import { MambaPresence, MambaPresenceState } from "./MambaPresence";
import { SudoPopup } from "./SudoPopup";
import { SettingsPanel } from "./SettingsPanel";
import { TranscriptPanel } from "./TranscriptPanel";
import { Composer } from "./Composer";
import { ToastContainer, useToast } from "./Toast";
import { BrowserAgent } from "./BrowserAgent";
import { loadSettings, saveSettings, MambaSettings } from "./settingsStore";

interface TranscriptEntry {
  id: string;
  timestamp: string;
  role: "user" | "model";
  content: string;
  isError?: boolean;
}

export const MambaApp: React.FC = () => {
  // Runtime State
  const [liveState, setLiveState] = useState<LiveState>("idle");
  const [settings, setSettings] = useState<MambaSettings>(loadSettings);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [isTranscriptOpen, setIsTranscriptOpen] = useState(false);
  const [browserUrl, setBrowserUrl] = useState<string | null>(null);

  // Settings panel & Windows startup preference.
  // The OS login-item registration is the source of truth; the settings
  // store mirrors it for UI consistency.
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [autoStart, setAutoStart] = useState<boolean>(() => {
    try {
      if (window.mambaDesktop?.getAutoStart) {
        return window.mambaDesktop.getAutoStart() === true;
      }
    } catch {
      /* bridge unavailable — fall through to stored preference */
    }
    return loadSettings().autoStart === true;
  });

  // Sudo / Permission Requests from Mamba
  const [pendingRequests, setPendingRequests] = useState<any[]>([]);

  // Toast notifications hook
  const { toasts, addToast, dismiss } = useToast(6000);

  // Audio session ref (wake detection lives in the orb renderer — see src/wake/)
  const audioSessionRef = useRef<MambaAudioSession | null>(null);
  // Set when the shell requested a voice turn before the transport connected.
  const pendingTurnRef = useRef<boolean>(false);
  // True while a continuous (Phase 8) voice session holds the mic open.
  const [continuousVoiceActive, setContinuousVoiceActive] = useState(false);

  // Sync liveState to desktop shell (for Floating Orb synchronization)
  useEffect(() => {
    if (window.mambaDesktop?.reportState) {
      window.mambaDesktop.reportState(liveState);
    }
  }, [liveState]);

  // Keep the persisted settings mirror in sync with the OS startup registration.
  useEffect(() => {
    try {
      if (window.mambaDesktop?.getAutoStart) {
        const osState = window.mambaDesktop.getAutoStart() === true;
        setAutoStart(osState);
        setSettings((prev) =>
          prev.autoStart === osState ? prev : saveSettings({ autoStart: osState })
        );
      }
    } catch {
      /* non-desktop context — nothing to sync */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runVoiceTurn = () => {
    const session = audioSessionRef.current;
    if (!session || session.isVoiceTurnActive()) return;
    // Only start from a quiet state — never interrupt an in-flight turn.
    const s = session.getState();
    if (s !== "idle" && s !== "listening" && s !== "error") return;
    try {
      window.mambaDesktop?.reportVoiceState?.(true);
    } catch {
      /* non-desktop context */
    }
    addToast("Listening… speak your command.", "info");
    session.startVoiceTurn().catch((err) => {
      addToast(err?.message || "Voice turn failed.", "error");
      try {
        window.mambaDesktop?.reportVoiceState?.(false);
      } catch {
        /* ignore */
      }
    });
  };
  // Keep a stable reference for the mount-once effect below.
  const runVoiceTurnRef = useRef<() => void>(() => {});
  useEffect(() => {
    runVoiceTurnRef.current = runVoiceTurn;
  });

  // Phase 8: start a continuous voice session — the mic stays open and the
  // session returns to LISTENING after every turn until stopped. Used for
  // wake-word activation and the mic toggle. Push-to-talk (orb click) keeps
  // the one-turn runVoiceTurn above.
  const runContinuousSession = () => {
    const session = audioSessionRef.current;
    if (!session || session.isContinuousSessionActive() || session.isVoiceTurnActive()) return;
    try {
      window.mambaDesktop?.reportVoiceState?.(true);
    } catch {
      /* non-desktop context */
    }
    setContinuousVoiceActive(true);
    addToast("Voice session started — speak naturally. Click the mic to stop.", "info");
    session.startContinuousSession().catch((err) => {
      setContinuousVoiceActive(false);
      addToast(err?.message || "Voice session failed.", "error");
      try {
        window.mambaDesktop?.reportVoiceState?.(false);
      } catch {
        /* ignore */
      }
    });
  };
  // Keep a stable reference for the mount-once effect below.
  const runContinuousSessionRef = useRef<() => void>(() => {});
  useEffect(() => {
    runContinuousSessionRef.current = runContinuousSession;
  });

  const toggleVoiceSession = () => {
    const session = audioSessionRef.current;
    if (!session) return;
    if (session.isContinuousSessionActive()) {
      session.stopContinuousSession();
      // onVoiceTurnComplete clears continuousVoiceActive + reports to shell.
    } else {
      runContinuousSessionRef.current();
    }
  };

  // Initialize session and voice-turn wiring.
  // Wake-word DETECTION lives in the orb renderer (src/wake/); this window
  // runs the resulting voice session (continuous, Phase 8).
  useEffect(() => {
    const session = new MambaAudioSession({
      onStateChange: (state) => {
        setLiveState(state);
        // A voice session requested before the transport connected starts now.
        if (state === "idle" && pendingTurnRef.current) {
          pendingTurnRef.current = false;
          runContinuousSessionRef.current();
        }
      },
      onTranscription: (role, text) => {
        const newEntry: TranscriptEntry = {
          id: Math.random().toString(36).substring(2, 9),
          timestamp: new Date().toISOString(),
          role,
          content: text,
        };
        setTranscript((prev) => [...prev, newEntry]);
      },
      onProgress: (milestone) => {
        addToast(milestone, "milestone");
      },
      onPermissionRequest: (command, reason) => {
        const req = {
          id: Math.random().toString(36).substring(2, 9),
          command: command || "Execute action",
          requestedBy: "Mamba Core",
          timestamp: new Date(),
          expiresAt: new Date(Date.now() + 60000),
          status: "pending" as const,
        };
        setPendingRequests([req]);
        addToast(`Permission needed: ${reason}`, "reminder");
      },
      onError: (err) => {
        addToast(err, "error");
        setLiveState("error");
      },
      onVoiceTurnComplete: () => {
        // A voice turn (one-turn) or a whole continuous session ended; the
        // mic is released. Tell the shell so the lifecycle timers and the
        // orb wake listener can resume their quiet state.
        setContinuousVoiceActive(false);
        try {
          window.mambaDesktop?.reportVoiceState?.(false);
        } catch {
          /* non-desktop context */
        }
      },
    });

    audioSessionRef.current = session;
    session.connect();

    // Voice-turn requests from the shell (wake-word trigger in the orb
    // renderer) start a continuous Phase 8 voice session. The shell also
    // stashes a pending flag in case this renderer was still loading when
    // the trigger fired.
    const cleanupVoiceTurn = window.mambaDesktop?.onVoiceTurnRequest?.(() => {
      runContinuousSessionRef.current();
    });
    try {
      if (window.mambaDesktop?.consumePendingVoiceTurn?.() === true) {
        pendingTurnRef.current = true;
      }
    } catch {
      /* bridge unavailable */
    }

    return () => {
      cleanupVoiceTurn?.();
      session.disconnect();
    };
  }, []);

  // Handle user approving a permission request
  const handleApprove = (_requestId: string) => {
    audioSessionRef.current?.sendPermissionResponse(true);
    setPendingRequests([]);
    addToast("Action approved", "success");
  };

  // Handle user rejecting a permission request
  const handleReject = (_requestId: string) => {
    audioSessionRef.current?.sendPermissionResponse(false);
    setPendingRequests([]);
    addToast("Action rejected", "info");
  };

  // Handle the "Start Mamba with Windows" toggle.
  const handleAutoStartChange = (enabled: boolean) => {
    let applied = enabled;
    try {
      if (window.mambaDesktop?.setAutoStart) {
        applied = window.mambaDesktop.setAutoStart(enabled) === true;
      }
    } catch {
      applied = enabled; // bridge unavailable: persist the preference only
    }
    setAutoStart(applied);
    setSettings(saveSettings({ autoStart: applied }));
    addToast(
      applied
        ? "Mamba will start with Windows (dormant at login)."
        : "Mamba will no longer start with Windows.",
      "info"
    );
  };

  // Handle the wake-word toggle. The orb renderer hosts the wake listener;
  // notify the shell so it can arm/disarm it without a restart.
  const handleWakeWordChange = (enabled: boolean) => {
    setSettings(saveSettings({ wakeWordEnabled: enabled }));
    try {
      window.mambaDesktop?.notifyWakeSettingChanged?.(enabled);
    } catch {
      /* non-desktop context */
    }
    addToast(
      enabled
        ? `Wake word enabled — say "${settings.wakePhrase || "hey mamba"}" while dormant.`
        : "Wake word disabled.",
      "info"
    );
  };

  // Handle text message submission
  const handleMessageSubmit = (message: string) => {
    if (!message.trim()) return;

    // Add user message to transcript
    const userEntry: TranscriptEntry = {
      id: Math.random().toString(36).substring(2, 9),
      timestamp: new Date().toISOString(),
      role: "user",
      content: message,
    };
    setTranscript((prev) => [...prev, userEntry]);

    // Send through canonical transport
    audioSessionRef.current?.sendText(message);
    setLiveState("thinking");
  };

  // Map LiveState to MambaPresenceState
  const presenceState: MambaPresenceState =
    liveState === "permission"
      ? "permission"
      : liveState === "error"
      ? "error"
      : liveState === "thinking"
      ? "thinking"
      : liveState === "speaking"
      ? "speaking"
      : liveState === "listening"
      ? "listening"
      : "idle";

  // Subtle status caption for the stage (the orb itself stays label-free).
  const statusCopy: Record<string, { text: string; hint: string; pill: string }> = {
    idle: {
      text: "Ready",
      hint: settings.wakeWordEnabled
        ? `Say "${settings.wakePhrase || "hey mamba"}" or click the orb`
        : "Click the orb or type below",
      pill: "",
    },
    listening: { text: "Listening", hint: "Speak your command", pill: "is-listening" },
    thinking: { text: "Thinking", hint: "Planning & reasoning", pill: "is-thinking" },
    speaking: { text: "Speaking", hint: "Responding", pill: "is-speaking" },
    permission: { text: "Approval needed", hint: "Review the request to continue", pill: "is-permission" },
    error: { text: "Something went wrong", hint: "Check the connection and try again", pill: "is-error" },
    connecting: { text: "Connecting", hint: "Starting transport", pill: "" },
    disconnected: { text: "Disconnected", hint: "Restart the backend to reconnect", pill: "is-error" },
  };
  const status = statusCopy[liveState] ?? statusCopy.idle;

  return (
    <div className="mamba-shell">
      {/* Notifications */}
      <ToastContainer toasts={toasts} onDismiss={dismiss} />

      {/* Permission Confirmation Modal */}
      <SudoPopup
        pendingRequests={pendingRequests}
        onApprove={handleApprove}
        onReject={handleReject}
      />

      {/* Settings Panel */}
      <SettingsPanel
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        autoStart={autoStart}
        onAutoStartChange={handleAutoStartChange}
        isDesktop={Boolean(window.mambaDesktop?.isDesktop)}
        wakeWordEnabled={settings.wakeWordEnabled !== false}
        wakePhrase={settings.wakePhrase || "hey mamba"}
        onWakeWordChange={handleWakeWordChange}
      />

      {/* Header */}
      <header className="mamba-header">
        <div className="brand">
          <span className="brand-dot" aria-hidden="true" />
          <span className="brand-name">Mamba</span>
        </div>

        <div className="header-actions">
          <div className="header-status" aria-live="polite">
            <span className={`pill ${status.pill}`}>
              <span className="dot" aria-hidden="true" />
              <span className="pill-label">{status.text}</span>
            </span>
          </div>

          <button
            onClick={() => setIsTranscriptOpen(!isTranscriptOpen)}
            className={`icon-btn${isTranscriptOpen ? " is-active" : ""}`}
            title="Conversation transcript"
            aria-label="Conversation transcript"
            aria-pressed={isTranscriptOpen}
          >
            <MessageSquare />
          </button>

          <button
            onClick={() => setBrowserUrl(browserUrl ? null : "about:blank")}
            className={`icon-btn${browserUrl ? " is-active" : ""}`}
            title="Browser view"
            aria-label="Browser view"
            aria-pressed={!!browserUrl}
          >
            <Globe />
          </button>

          <button
            onClick={toggleVoiceSession}
            className={`icon-btn${continuousVoiceActive ? " is-active is-danger" : ""}`}
            title={continuousVoiceActive ? "Stop voice session" : "Start voice session"}
            aria-label={continuousVoiceActive ? "Stop voice session" : "Start voice session"}
            aria-pressed={continuousVoiceActive}
          >
            {continuousVoiceActive ? <MicOff /> : <Mic />}
          </button>

          <button
            onClick={() => setIsSettingsOpen(!isSettingsOpen)}
            className={`icon-btn${isSettingsOpen ? " is-active" : ""}`}
            title="Settings"
            aria-label="Settings"
            aria-pressed={isSettingsOpen}
          >
            <Settings />
          </button>
        </div>
      </header>

      {/* Center stage — the Orb */}
      <main className="mamba-stage">
        <div
          className="orb-wrap"
          role="button"
          tabIndex={0}
          aria-label="Mamba orb — activate voice turn"
          onClick={() => runVoiceTurnRef.current()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              runVoiceTurnRef.current();
            }
          }}
        >
          <MambaPresence
            state={presenceState}
            variant="orb"
            size={260}
            showLabel={false}
            inputNode={audioSessionRef.current?.inputAnalyser}
            outputNode={audioSessionRef.current?.outputAnalyser}
          />
        </div>
        <div className="status-block" aria-live="polite">
          <div className="status-text">{status.text}</div>
          <div className="status-hint">{status.hint}</div>
        </div>
      </main>

      {/* Bottom composer */}
      <footer className="mamba-footer">
        <Composer
          onMessageSubmit={handleMessageSubmit}
          voiceActive={continuousVoiceActive}
          onToggleVoice={toggleVoiceSession}
          disabled={liveState === "disconnected" || liveState === "connecting"}
        />
        <div className="composer-hint">
          {settings.wakeWordEnabled
            ? `Wake word "${settings.wakePhrase || "hey mamba"}" is on`
            : "Wake word is off"}
          {" · "}Enter to send, Shift+Enter for a new line
        </div>
      </footer>

      {/* Transcript Panel */}
      <TranscriptPanel
        entries={transcript}
        isOpen={isTranscriptOpen}
        onClose={() => setIsTranscriptOpen(false)}
      />

      {/* Browser Viewport Modal */}
      {browserUrl && (
        <BrowserAgent
          url={browserUrl}
          onClose={() => setBrowserUrl(null)}
        />
      )}
    </div>
  );
};

export default MambaApp;

