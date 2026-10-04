import React, { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { motionEnabled } from "./motionPrefs";
import { loadSettings } from "./settingsStore";

interface SudoRequest {
  id: string;
  command: string;
  package?: string;
  requestedBy: string;
  timestamp: Date;
  expiresAt: Date;
  status: "pending" | "approved" | "rejected" | "expired";
}

interface SudoPopupProps {
  onApprove?: (token: string) => void;
  onReject?: (token: string) => void;
  pendingRequests?: SudoRequest[];
  /** Why Mamba wants this — shown instead of a generic warning. */
  reason?: string;
}

const FALLBACK_EXPLANATION = "Mamba wants to do this before it can continue.";
const RISK_LINE = "This runs on your machine, outside Mamba. Allow it only if you were expecting it.";

export const SudoPopup: React.FC<SudoPopupProps> = ({
  onApprove,
  onReject,
  pendingRequests = [],
  reason,
}) => {
  const [selectedRequest, setSelectedRequest] = useState<SudoRequest | null>(null);
  const [timeLeft, setTimeLeft] = useState<number>(0);
  const [expired, setExpired] = useState<boolean>(false);
  const denyRef = useRef<HTMLButtonElement>(null);
  const approveRef = useRef<HTMLButtonElement>(null);

  const showDialog = selectedRequest !== null;
  const animate = motionEnabled(loadSettings().animations);

  const handleApprove = () => {
    if (!selectedRequest || expired) return;
    onApprove?.(selectedRequest.id);
    setSelectedRequest(null);
  };

  const handleReject = () => {
    if (!selectedRequest || expired) return;
    onReject?.(selectedRequest.id);
    setSelectedRequest(null);
  };

  // Compared by object identity, not by "nothing is selected": an expired
  // request stays on screen, and a new one must still replace it.
  useEffect(() => {
    const next = pendingRequests[0] ?? null;
    setSelectedRequest((current) => (current === next ? current : next));
  }, [pendingRequests]);

  // The countdown ends when the request does: the timer stops rather than
  // parking at `0s`, and nothing is sent on the user's behalf either way.
  useEffect(() => {
    const request = selectedRequest;
    if (!request) return;

    const remaining = () =>
      Math.max(0, Math.ceil((request.expiresAt.getTime() - Date.now()) / 1000));

    const first = remaining();
    setTimeLeft(first);
    setExpired(first <= 0);
    if (first <= 0) return;

    const timer = window.setInterval(() => {
      const left = remaining();
      setTimeLeft(left);
      if (left <= 0) {
        setExpired(true);
        window.clearInterval(timer);
      }
    }, 1000);

    return () => window.clearInterval(timer);
  }, [selectedRequest]);

  useEffect(() => {
    if (!selectedRequest) return;
    denyRef.current?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        handleReject();
        return;
      }
      if (event.key !== "Tab") return;

      const targets = [denyRef.current, approveRef.current].filter(
        (el): el is HTMLButtonElement => el !== null && !el.disabled
      );
      event.preventDefault();
      if (targets.length === 0) return;

      const index = targets.indexOf(document.activeElement as HTMLButtonElement);
      const step = event.shiftKey ? -1 : 1;
      const next = index === -1 ? targets[0] : targets[(index + step + targets.length) % targets.length];
      next?.focus();
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedRequest, expired]);

  const formatTimeLeft = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
  };

  const explanation = reason && reason.trim() ? reason.trim() : FALLBACK_EXPLANATION;

  return (
    <AnimatePresence>
      {showDialog && selectedRequest && (
      <motion.div
        key={selectedRequest.id}
        initial={animate ? { opacity: 0 } : false}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: animate ? 0.18 : 0 }}
        className="modal-backdrop"
      >
        <motion.div
          initial={animate ? { opacity: 0, y: 10 } : false}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 4, pointerEvents: "none" }}
          transition={{ duration: animate ? 0.22 : 0, ease: [0.16, 1, 0.3, 1] }}
          className="dialog"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="sudo-title"
          aria-describedby="sudo-explain"
        >
          <h3 className="dialog-title" id="sudo-title">
            Mamba needs your permission
          </h3>
          <p className="dialog-explain" id="sudo-explain">
            {explanation}
          </p>

          <div className="dialog-action">
            <div className="dialog-action-label">What it will do</div>
            <div className="dialog-action-text">{selectedRequest.command}</div>
            {selectedRequest.package && (
              <div className="dialog-action-meta">Package {selectedRequest.package}</div>
            )}
          </div>

          <p className="dialog-risk">{RISK_LINE}</p>

          <div className="dialog-foot">
            <span className={`dialog-timer${expired ? " is-expired" : ""}`}>
              {expired
                ? "This request has expired. Answer from the composer to continue."
                : `Expires in ${formatTimeLeft(timeLeft)}`}
            </span>
            <div className="dialog-actions">
              <button
                ref={denyRef}
                type="button"
                onClick={handleReject}
                disabled={expired}
                className="btn btn-ghost"
              >
                Deny
              </button>
              <button
                ref={approveRef}
                type="button"
                onClick={handleApprove}
                disabled={expired}
                className="btn btn-primary"
              >
                Allow
              </button>
            </div>
          </div>
        </motion.div>
      </motion.div>
      )}
    </AnimatePresence>
  );
};
