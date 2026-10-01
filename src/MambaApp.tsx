import React, { useState, useEffect, useRef } from "react";
import { MessageSquare, Globe, Mic, MicOff, Settings, Volume2 } from "lucide-react";

import { MambaAudioSession, LiveState } from "./audio";
import { MambaPresence, MambaPresenceState } from "./MambaPresence";
import { SudoPopup } from "./SudoPopup";
import { SettingsPanel } from "./SettingsPanel";
import { TranscriptPanel } from "./TranscriptPanel";
import { TextChatFallback } from "./TextChatFallback";
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
  const [isTextChatOpen, setIsTextChatOpen] = useState(false);
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

  return (
    <div className="relative w-screen h-screen bg-slate-950 text-white flex flex-col items-center justify-between p-6 select-none overflow-hidden">
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

      {/* Header Bar */}
      <div className="w-full flex items-center justify-between z-20">
        <div className="flex items-center gap-3">
          <div className="w-2.5 h-2.5 rounded-full bg-cyan-400 animate-pulse" />
          <h1 className="text-sm font-bold font-mono tracking-widest text-slate-300">
            MAMBA AI
          </h1>
        </div>

        <div className="flex items-center gap-2">
          {/* Transcript toggle */}
          <button
            onClick={() => setIsTranscriptOpen(!isTranscriptOpen)}
            className={`p-2.5 rounded-xl border transition ${
              isTranscriptOpen
                ? "bg-purple-500/20 border-purple-500/40 text-purple-300"
                : "bg-white/5 border-white/10 text-slate-400 hover:text-white"
            }`}
            title="Toggle Transcript"
          >
            <MessageSquare className="w-4 h-4" />
          </button>

          {/* Browser viewport toggle */}
          <button
            onClick={() => setBrowserUrl(browserUrl ? null : "about:blank")}
            className={`p-2.5 rounded-xl border transition ${
              browserUrl
                ? "bg-cyan-500/20 border-cyan-500/40 text-cyan-300"
                : "bg-white/5 border-white/10 text-slate-400 hover:text-white"
            }`}
            title="Toggle Browser View"
          >
            <Globe className="w-4 h-4" />
          </button>

          {/* Text Chat toggle */}
          <button
            onClick={() => setIsTextChatOpen(!isTextChatOpen)}
            className="p-2.5 rounded-xl bg-white/5 border border-white/10 text-slate-400 hover:text-white transition"
            title="Toggle Text Input"
          >
            <Volume2 className="w-4 h-4" />
          </button>

          {/* Voice session toggle (Phase 8 continuous conversation) */}
          <button
            onClick={toggleVoiceSession}
            className={`p-2.5 rounded-xl border transition ${
              continuousVoiceActive
                ? "bg-red-500/20 border-red-500/40 text-red-300"
                : "bg-white/5 border-white/10 text-slate-400 hover:text-white"
            }`}
            title={continuousVoiceActive ? "Stop voice session" : "Start voice session"}
          >
            {continuousVoiceActive ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
          </button>

          {/* Settings toggle */}
          <button
            onClick={() => setIsSettingsOpen(!isSettingsOpen)}
            className={`p-2.5 rounded-xl border transition ${
              isSettingsOpen
                ? "bg-cyan-500/20 border-cyan-500/40 text-cyan-300"
                : "bg-white/5 border-white/10 text-slate-400 hover:text-white"
            }`}
            title="Settings"
          >
            <Settings className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Center — Mamba Presence State */}
      <div className="flex-1 flex items-center justify-center z-10">
        <MambaPresence
          state={presenceState}
          variant="orb"
          size={260}
          inputNode={audioSessionRef.current?.inputAnalyser}
          outputNode={audioSessionRef.current?.outputAnalyser}
          onClick={() => {
            // Push-to-talk parity with the wake word: one voice turn.
            runVoiceTurnRef.current();
          }}
        />
      </div>

      {/* Bottom Controls / Status */}
      <div className="w-full flex items-center justify-between text-xs font-mono text-slate-500 z-20">
        <div>
          Wake Word:{" "}
          <span className="text-cyan-400">
            {settings.wakeWordEnabled ? `"${settings.wakePhrase}"` : "disabled"}
          </span>
        </div>
        <div>
          Mode: <span className="text-slate-300 capitalize">{liveState}</span>
        </div>
      </div>

      {/* Transcript Panel */}
      <TranscriptPanel
        entries={transcript}
        isOpen={isTranscriptOpen}
        onClose={() => setIsTranscriptOpen(false)}
      />

      {/* Text Chat Fallback input */}
      <TextChatFallback
        isActive={isTextChatOpen}
        onClose={() => setIsTextChatOpen(false)}
        onMessageSubmit={handleMessageSubmit}
        systemStatus={{
          state: liveState === "disconnected" ? "disconnected" : "connected",
          transcriptCount: transcript.length,
          timestamp: new Date().toISOString(),
          status: "operational",
        }}
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

