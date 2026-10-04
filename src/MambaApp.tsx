import React, { useState, useEffect, useMemo, useRef } from "react";
import { MessageSquare, Globe, Mic, MicOff, Settings } from "lucide-react";

import { MambaAudioSession, LiveState } from "./audio";
import { MambaPresence, MambaPresenceState } from "./MambaPresence";
import { SudoPopup } from "./SudoPopup";
import { SettingsPanel } from "./SettingsPanel";
import { TranscriptPanel } from "./TranscriptPanel";
import { ThreadEntry, type TranscriptEntry } from "./ThreadEntry";
import { Composer } from "./Composer";
import { ToastContainer, useToast } from "./Toast";
import { BrowserAgent } from "./BrowserAgent";
import { motionEnabled } from "./motionPrefs";
import { loadSettings, saveSettings, MambaSettings } from "./settingsStore";

const newEntryId = () => Math.random().toString(36).substring(2, 9);

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
  const [permissionReason, setPermissionReason] = useState<string>("");

  // Toast notifications hook
  const { toasts, addToast, dismiss, pause, resume } = useToast();

  // Audio session ref (wake detection lives in the orb renderer — see src/wake/)
  const audioSessionRef = useRef<MambaAudioSession | null>(null);
  // Set when the shell requested a voice turn before the transport connected.
  const pendingTurnRef = useRef<boolean>(false);
  // True while a continuous (Phase 8) voice session holds the mic open.
  const [continuousVoiceActive, setContinuousVoiceActive] = useState(false);

  // --- Phase 4 provisional streaming (presentation only) ---
  // A turn owns at most one open streaming bubble. Delta frames patch it;
  // ONLY the authoritative model transcription can finalize it. Anything
  // else (turn end without a final text, cancellation, error) discards it,
  // so streamed text can never be mistaken for a completed answer.
  const streamEntryIdRef = useRef<string | null>(null);
  const deltaBufferRef = useRef("");
  const deltaFrameRef = useRef<number | null>(null);
  // Composer adds the user message optimistically; the transport echoes it
  // back as a user transcription, which must not appear twice.
  const userEchoRef = useRef<string | null>(null);

  const flushDeltaFrame = () => {
    deltaFrameRef.current = null;
    const chunk = deltaBufferRef.current;
    deltaBufferRef.current = "";
    const entryId = streamEntryIdRef.current;
    if (!chunk || !entryId) return;
    setTranscript((prev) =>
      prev.map((entry) =>
        entry.id === entryId ? { ...entry, content: entry.content + chunk } : entry
      )
    );
  };

  const appendDelta = (text: string) => {
    if (!streamEntryIdRef.current) {
      const id = newEntryId();
      streamEntryIdRef.current = id;
      setTranscript((prev) => [
        ...prev,
        { id, timestamp: new Date().toISOString(), role: "model", content: "", streaming: true },
      ]);
    }
    deltaBufferRef.current += text;
    if (deltaFrameRef.current === null) {
      deltaFrameRef.current = requestAnimationFrame(flushDeltaFrame);
    }
  };

  /** Close the open bubble: pending deltas are dropped, then `action` runs. */
  const resolveStream = (action: (entryId: string) => void): void => {
    const entryId = streamEntryIdRef.current;
    streamEntryIdRef.current = null;
    deltaBufferRef.current = "";
    if (deltaFrameRef.current !== null) {
      cancelAnimationFrame(deltaFrameRef.current);
      deltaFrameRef.current = null;
    }
    if (entryId) action(entryId);
  };

  /** Authoritative text: replace the provisional content, keep it readable. */
  const finalizeStreamWith = (text: string) => {
    resolveStream((entryId) =>
      setTranscript((prev) =>
        prev.map((entry) =>
          entry.id === entryId ? { ...entry, content: text, streaming: false } : entry
        )
      )
    );
  };

  /** No authoritative text backs the deltas — they were never an answer. */
  const discardStream = () => {
    resolveStream((entryId) =>
      setTranscript((prev) => prev.filter((entry) => entry.id !== entryId))
    );
  };

  const cancelTurn = () => {
    audioSessionRef.current?.sendCancel();
    addToast("Cancelling current turn…", "info");
  };

  // Derived Orb phase: which part of the turn Mamba is in. Presentation only —
  // it reads milestones the transport already transmits and never changes
  // LiveState, the transport, or the shell's busy detection.
  const [orbPhase, setOrbPhase] = useState<"executing" | "verifying" | null>(null);

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

  // Window modes: a resized window is the same product with less, not a second
  // design (Spec §19). One state object, one resize listener, two CSS modifiers.
  const [winMode, setWinMode] = useState(() => ({
    compact: window.innerWidth <= 720,
    short: window.innerHeight <= 620,
  }));

  useEffect(() => {
    const onResize = () =>
      setWinMode({
        compact: window.innerWidth <= 720,
        short: window.innerHeight <= 620,
      });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
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
        // A turn phase only lives inside "thinking" — anything else ends it.
        if (state !== "thinking") setOrbPhase(null);
        // A voice session requested before the transport connected starts now.
        if (state === "idle" && pendingTurnRef.current) {
          pendingTurnRef.current = false;
          runContinuousSessionRef.current();
        }
      },
      onTranscription: (role, text) => {
        if (role === "user") {
          // The composer already showed the typed message.
          if (userEchoRef.current === text) {
            userEchoRef.current = null;
            return;
          }
          setTranscript((prev) => [
            ...prev,
            { id: newEntryId(), timestamp: new Date().toISOString(), role, content: text },
          ]);
          return;
        }
        // The model transcription is the authoritative turn result.
        setOrbPhase(null);
        if (streamEntryIdRef.current) {
          finalizeStreamWith(text);
        } else {
          setTranscript((prev) => [
            ...prev,
            { id: newEntryId(), timestamp: new Date().toISOString(), role, content: text },
          ]);
        }
      },
      onDelta: appendDelta,
      onCancelled: () => {
        discardStream();
        setOrbPhase(null);
        addToast("Turn cancelled.", "info");
      },
      onTurnComplete: () => {
        userEchoRef.current = null;
        setOrbPhase(null);
        // Reached without an authoritative model text (e.g. the turn stopped
        // at an approval prompt): provisional deltas are not an answer.
        discardStream();
      },
      onProgress: (milestone) => {
        addToast(milestone, "milestone");
        const m = milestone.toLowerCase();
        if (m.startsWith("executing")) setOrbPhase("executing");
        else if (m.startsWith("verifying")) setOrbPhase("verifying");
        else if (m.startsWith("planning") || m.startsWith("understanding")) setOrbPhase(null);
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
        setPermissionReason(reason || "");
        setPendingRequests([req]);
        addToast(
          reason ? `Permission needed: ${reason}` : "Permission needed",
          "reminder"
        );
      },
      onError: (err) => {
        // A failed turn is a failure, not partially streamed success.
        discardStream();
        setOrbPhase(null);
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
      if (deltaFrameRef.current !== null) cancelAnimationFrame(deltaFrameRef.current);
      deltaFrameRef.current = null;
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

  // Wake-word phrase and sensitivity live in the orb renderer's controller, so
  // a change is persisted locally and forwarded over the bridge; the controller
  // rearms with the new options.
  const handleWakePhraseChange = (phrase: string) => {
    setSettings(saveSettings({ wakePhrase: phrase }));
    try {
      window.mambaDesktop?.notifyWakeOptions?.({ phrase });
    } catch {
      /* non-desktop context */
    }
    addToast(`Wake phrase set to "${phrase}".`, "info");
  };

  const handleSensitivityChange = (sensitivity: number) => {
    setSettings(saveSettings({ sensitivity }));
    try {
      window.mambaDesktop?.notifyWakeOptions?.({ sensitivity });
    } catch {
      /* non-desktop context */
    }
  };

  // Motion is read from the store by each surface as it renders, so the change
  // is broadcast for the next render rather than threaded through new state.
  const handleAnimationsChange = (enabled: boolean) => {
    setSettings(saveSettings({ animations: enabled }));
    window.dispatchEvent(new Event("mamba:settings-changed"));
    addToast(
      enabled
        ? "Motion enabled."
        : "Motion reduced — takes effect on the next interaction.",
      "info"
    );
  };

  // Handle text message submission
  const handleMessageSubmit = (message: string) => {
    if (!message.trim()) return;

    // A new turn never inherits an unresolved stream slot.
    discardStream();

    // Add user message to transcript
    const userEntry: TranscriptEntry = {
      id: newEntryId(),
      timestamp: new Date().toISOString(),
      role: "user",
      content: message,
    };
    userEchoRef.current = message.trim();
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

  // The Orb shows the derived turn phase while Mamba is thinking. The
  // transport's own state always wins otherwise, so `permission` cannot be
  // overwritten by a stale phase.
  const displayState = orbPhase && liveState === "thinking" ? orbPhase : liveState;
  const orbState: MambaPresenceState =
    orbPhase && liveState === "thinking" ? orbPhase : presenceState;

  // Sync the Orb state to the desktop shell (Floating Orb synchronisation).
  // Transport-only states pass through unmapped: the orb ignores them today and
  // calling "connecting" idle would misreport startup as quiet.
  useEffect(() => {
    if (!window.mambaDesktop?.reportState) return;
    window.mambaDesktop.reportState(
      liveState === "connecting" || liveState === "disconnected" ? liveState : orbState
    );
  }, [orbState, liveState]);

  // Quiet status caption for the stage (the orb itself stays label-free).
  // Copy is outcome-first: never a model, provider, agent or tool name.
  const statusCopy: Record<string, { text: string; hint: string; pill: string }> = {
    idle: {
      text: "Ready",
      hint: settings.wakeWordEnabled
        ? `Say "${settings.wakePhrase || "hey mamba"}" or just start typing`
        : "Ask Mamba anything below",
      pill: "",
    },
    listening: { text: "Listening…", hint: "Go ahead", pill: "is-listening" },
    thinking: { text: "Thinking…", hint: "Working through it", pill: "is-thinking" },
    executing: { text: "Working…", hint: "Getting it done", pill: "is-executing" },
    verifying: { text: "Checking…", hint: "Making sure it worked", pill: "is-verifying" },
    speaking: { text: "Speaking…", hint: "Answering out loud", pill: "is-speaking" },
    permission: { text: "Needs your approval", hint: "Review the request", pill: "is-permission" },
    error: { text: "Something went wrong", hint: "Try again in a moment", pill: "is-error" },
    connecting: { text: "Starting up…", hint: "", pill: "" },
    disconnected: { text: "Not connected", hint: "Restart Mamba to reconnect", pill: "is-error" },
  };
  const status = statusCopy[displayState] ?? statusCopy.idle;

  // The stage carries at most the last authoritative answer — never provisional
  // deltas, which are the transcript rail's job while they are still arriving.
  const latestAnswer = useMemo(() => {
    for (let i = transcript.length - 1; i >= 0; i--) {
      const e = transcript[i];
      if (e.role === "model" && !e.streaming) return e;
    }
    return null;
  }, [transcript]);

  const modeClass = `${winMode.compact ? " is-compact" : ""}${
    winMode.short ? " is-short" : ""
  }`;

  return (
    <div
      className={`mamba-shell${motionEnabled(settings.animations) ? "" : " no-motion"}${modeClass}`}
    >
      {/* Notifications */}
      <ToastContainer
        toasts={toasts}
        onDismiss={dismiss}
        onPause={pause}
        onResume={resume}
      />

      {/* Permission Confirmation Modal */}
      <SudoPopup
        pendingRequests={pendingRequests}
        reason={permissionReason}
        onApprove={handleApprove}
        onReject={handleReject}
      />

      {/* Settings Panel */}
      <SettingsPanel
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        isDesktop={Boolean(window.mambaDesktop?.isDesktop)}
        autoStart={autoStart}
        onAutoStartChange={handleAutoStartChange}
        animations={settings.animations !== false}
        onAnimationsChange={handleAnimationsChange}
        wakeWordEnabled={settings.wakeWordEnabled !== false}
        onWakeWordChange={handleWakeWordChange}
        wakePhrase={settings.wakePhrase || "hey mamba"}
        onWakePhraseChange={handleWakePhraseChange}
        sensitivity={settings.sensitivity}
        onSensitivityChange={handleSensitivityChange}
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
            state={orbState}
            inputNode={audioSessionRef.current?.inputAnalyser}
            outputNode={audioSessionRef.current?.outputAnalyser}
          />
        </div>
        <div className="status-block" aria-live="polite">
          <div className="status-text">{status.text}</div>
          <div className="status-hint">{status.hint}</div>
        </div>
        {latestAnswer && (
          <div className="stage-answer">
            <ThreadEntry entry={latestAnswer} variant="stage" defaultExpanded={false} />
            <button
              type="button"
              className="thread-more btn btn-ghost"
              onClick={() => setIsTranscriptOpen(true)}
            >
              View details
            </button>
          </div>
        )}
      </main>

      {/* Bottom composer */}
      <footer className="mamba-footer">
        <Composer
          onMessageSubmit={handleMessageSubmit}
          voiceActive={continuousVoiceActive}
          onToggleVoice={toggleVoiceSession}
          busy={liveState === "thinking"}
          onCancelTurn={cancelTurn}
          disabled={liveState === "disconnected" || liveState === "connecting"}
        />
        <div className="composer-hint">Enter to send · Shift+Enter for a new line</div>
      </footer>

      {/* Transcript Panel */}
      <TranscriptPanel
        entries={transcript}
        isOpen={isTranscriptOpen}
        onClose={() => setIsTranscriptOpen(false)}
        onClear={() => setTranscript([])}
        animations={settings.animations !== false}
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

