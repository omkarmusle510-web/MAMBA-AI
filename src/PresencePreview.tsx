import React, { useState } from "react";

import { MambaPresence, type MambaPresenceState } from "./MambaPresence";
import { SudoPopup } from "./SudoPopup";
import { ToastContainer, useToast } from "./Toast";

const TOAST_KINDS = ["milestone", "info", "success", "reminder", "error"] as const;

type PermissionRequests = NonNullable<React.ComponentProps<typeof SudoPopup>["pendingRequests"]>;

const LONG_COMMAND =
  "C:\\Users\\Omkar\\AppData\\Local\\Programs\\Mamba\\resources\\app.asar\\backend\\python.exe -m skills.desktop --action close_window --title \"Untitled - Notepad\"";

const requestWith = (
  expiresInMs: number,
  command: string,
  pkg?: string
): PermissionRequests => [
  {
    id: "preview-request",
    command,
    package: pkg,
    requestedBy: "Mamba Core",
    timestamp: new Date(),
    expiresAt: new Date(Date.now() + expiresInMs),
    status: "pending",
  },
];

const PERMISSION_CASES: Array<{
  label: string;
  reason: string;
  requests: () => PermissionRequests;
}> = [
  {
    label: "Pending",
    reason: "You asked me to close the Notepad window I opened.",
    requests: () => requestWith(45000, "close_window: Untitled - Notepad"),
  },
  {
    label: "With package",
    reason: "",
    requests: () => requestWith(45000, "pip install pynput", "pynput"),
  },
  {
    label: "Long command, no reason",
    reason: "",
    requests: () => requestWith(45000, LONG_COMMAND),
  },
  {
    label: "Expired",
    reason: "You asked me to close the Notepad window I opened.",
    requests: () => requestWith(-5000, "close_window: Untitled - Notepad"),
  },
];

/**
 * Visual-QA surface for the Orb (`?mode=preview`). Never mounted by the shell:
 * no transport, no audio nodes, no backend. Click a cell to inspect it large.
 */
const STATES: Array<{ state: MambaPresenceState; caption: string }> = [
  { state: "idle", caption: "Ready" },
  { state: "listening", caption: "Listening" },
  { state: "thinking", caption: "Thinking" },
  { state: "executing", caption: "Working" },
  { state: "verifying", caption: "Checking" },
  { state: "speaking", caption: "Speaking" },
  { state: "permission", caption: "Needs approval" },
  { state: "error", caption: "Went wrong" },
];

export const PresencePreview: React.FC = () => {
  const [focused, setFocused] = useState<MambaPresenceState>("idle");
  const [requests, setRequests] = useState<PermissionRequests>([]);
  const [reason, setReason] = useState("");
  const [lastAnswer, setLastAnswer] = useState("no answer yet");
  const { toasts, addToast, dismiss, pause, resume } = useToast();
  const focus = STATES.find((s) => s.state === focused) ?? STATES[0];

  const answer = (verdict: string) => {
    setLastAnswer(verdict);
    setRequests([]);
  };

  return (
    <div className="presence-preview">
      <h1>Orb states — visual check</h1>

      <div className="presence-preview-stage">
        <MambaPresence state={focused} caption={focus.caption} />
      </div>

      <div className="presence-preview-grid">
        {STATES.map(({ state, caption }) => (
          <button
            key={state}
            type="button"
            className={`presence-preview-cell${state === focused ? " is-active" : ""}`}
            onClick={() => setFocused(state)}
            aria-label={`Preview ${caption}`}
          >
            <MambaPresence state={state} size={168} />
            <span className="presence-preview-caption">{caption}</span>
          </button>
        ))}
      </div>

      <h2>Permission dialog</h2>
      <div className="presence-preview-cases">
        {PERMISSION_CASES.map((testCase) => (
          <button
            key={testCase.label}
            type="button"
            className="btn"
            onClick={() => {
              setReason(testCase.reason);
              setRequests(testCase.requests());
            }}
          >
            {testCase.label}
          </button>
        ))}
        <button type="button" className="btn btn-ghost" onClick={() => setRequests([])}>
          Clear
        </button>
      </div>
      <p className="presence-preview-note">Dialog answered with: {lastAnswer}</p>

      <SudoPopup
        pendingRequests={requests}
        reason={reason}
        onApprove={() => answer("Allow")}
        onReject={() => answer("Deny")}
      />

      <h2>Toasts</h2>
      <div className="presence-preview-cases">
        {TOAST_KINDS.map((kind) => (
          <button
            key={kind}
            type="button"
            className="btn"
            onClick={() => addToast(`${kind}: Notion page updated`, kind)}
          >
            {kind}
          </button>
        ))}
        <button
          type="button"
          className="btn"
          onClick={() => {
            for (let i = 0; i < 6; i += 1) addToast(`burst ${i + 1}`, "reminder");
          }}
        >
          6 at once
        </button>
      </div>
      <p className="presence-preview-note">Toasts on screen: {toasts.length}</p>

      <ToastContainer
        toasts={toasts}
        onDismiss={dismiss}
        onPause={pause}
        onResume={resume}
      />
    </div>
  );
};

export default PresencePreview;
