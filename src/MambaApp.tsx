import React, { useState, useEffect, useRef } from "react";
import { MessageSquare, Globe, Mic, MicOff, Settings, Volume2 } from "lucide-react";

import { MambaAudioSession, LiveState } from "./audio";
import { MambaWakeWordDetector } from "./wakeWord";
import { MambaPresence, MambaPresenceState } from "./MambaPresence";
import { SudoPopup } from "./SudoPopup";
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

  // Sudo / Permission Requests from Mamba
  const [pendingRequests, setPendingRequests] = useState<any[]>([]);

  // Toast notifications hook
  const { toasts, addToast, dismiss } = useToast(6000);

  // Audio session & wake word refs
  const audioSessionRef = useRef<MambaAudioSession | null>(null);
  const wakeWordRef = useRef<MambaWakeWordDetector | null>(null);

  // Sync liveState to desktop shell (for Floating Orb synchronization)
  useEffect(() => {
    if (window.mambaDesktop?.reportState) {
      window.mambaDesktop.reportState(liveState);
    }
  }, [liveState]);

  // Initialize session and wake word detector
  useEffect(() => {
    const session = new MambaAudioSession({
      onStateChange: (state) => {
        setLiveState(state);
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
    });

    audioSessionRef.current = session;
    session.connect();

    // Wake word detector
    const wakeDetector = new MambaWakeWordDetector();
    wakeWordRef.current = wakeDetector;

    if (settings.wakeWordEnabled) {
      wakeDetector.start({
        phrase: settings.wakePhrase || "hey mamba",
        sensitivity: settings.sensitivity,
        onTriggered: () => {
          addToast("Wake word detected: Listening...", "info");
          setLiveState("listening");
        },
      });
    }

    return () => {
      session.disconnect();
      wakeDetector.stop();
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
            if (liveState === "listening") {
              setLiveState("idle");
            } else {
              setLiveState("listening");
            }
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

