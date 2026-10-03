import React, { useState, useEffect } from "react";
import { Shield, Check, X, Terminal, Clock, AlertCircle } from "lucide-react";
import { motion, AnimatePresence } from "motion/react";

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
}

export const SudoPopup: React.FC<SudoPopupProps> = ({
  onApprove,
  onReject,
  pendingRequests = [],
}) => {
  const [selectedRequest, setSelectedRequest] = useState<SudoRequest | null>(null);
  const [timeLeft, setTimeLeft] = useState<number>(0);
  const [dots, setDots] = useState<string>(".");

  const hasPending = pendingRequests.length > 0;
  const showDialog = selectedRequest !== null;

  useEffect(() => {
    if (!selectedRequest) return;
    const updateTimer = () => {
      const now = new Date();
      const diff = Math.max(0, selectedRequest.expiresAt.getTime() - now.getTime());
      setTimeLeft(Math.floor(diff / 1000));
    };
    updateTimer();
    const interval = setInterval(updateTimer, 1000);
    return () => clearInterval(interval);
  }, [selectedRequest]);

  useEffect(() => {
    if (hasPending && !selectedRequest) {
      setSelectedRequest(pendingRequests[0]);
    } else if (!hasPending && selectedRequest) {
      setSelectedRequest(null);
    }
  }, [hasPending, pendingRequests, selectedRequest]);

  useEffect(() => {
    if (!showDialog) return;
    const interval = setInterval(() => {
      setDots((prev) => (prev.length >= 3 ? "." : prev + "."));
    }, 500);
    return () => {
      clearInterval(interval);
      setDots(".");
    };
  }, [showDialog]);

  const formatTimeLeft = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
  };

  const handleApprove = () => {
    if (selectedRequest && onApprove) {
      onApprove(selectedRequest.id);
    }
    setSelectedRequest(null);
  };

  const handleReject = () => {
    if (selectedRequest && onReject) {
      onReject(selectedRequest.id);
    }
    setSelectedRequest(null);
  };

  return (
    <>
      {/* Sudo Status Dot Indicator */}
      <motion.div
        initial={{ opacity: 0, scale: 0.8 }}
        animate={{ opacity: 1, scale: 1 }}
        className={`sudo-dot${hasPending ? " is-pending" : " is-ok"}`}
      >
        <span className="orb" aria-hidden="true" />
        <span>
          Sudo {hasPending ? "pending" : "secure"}
        </span>
        {hasPending && <span className="pending-dots">{dots}</span>}
      </motion.div>

      {/* Sudo Confirmation Dialog */}
      <AnimatePresence>
        {showDialog && selectedRequest && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="modal-backdrop"
          >
            <motion.div
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.9, opacity: 0 }}
              transition={{ type: "spring", stiffness: 300, damping: 30 }}
              className="dialog"
              role="alertdialog"
              aria-label="Sudo confirmation required"
            >
              <div className="dialog-head">
                <div className="dialog-icon">
                  <Shield />
                </div>
                <div>
                  <h3 className="dialog-title">Confirmation required</h3>
                  <p className="dialog-sub">
                    Command execution needs your approval
                  </p>
                </div>
              </div>

              <div className="dialog-code">
                <div className="k">Command</div>
                <div className="v">
                  {selectedRequest.command}
                </div>
                {selectedRequest.package && (
                  <>
                    <div className="k" style={{ marginTop: 10 }}>Package</div>
                    <div className="v" style={{ color: "var(--cyan)" }}>
                      {selectedRequest.package}
                    </div>
                  </>
                )}
              </div>

              <div className="notice amber">
                <Clock />
                <span>
                  Expires in: <strong>{formatTimeLeft(timeLeft)}</strong>
                </span>
              </div>

              <div className="notice rose">
                <AlertCircle />
                <div>
                  <div className="strong">Security warning</div>
                  <div>
                    This command will execute with elevated privileges. Only approve if you trust
                    the source.
                  </div>
                </div>
              </div>

              <div className="dialog-actions">
                <button
                  onClick={handleReject}
                  className="btn btn-reject"
                >
                  <X />
                  Reject
                </button>
                <button
                  onClick={handleApprove}
                  className="btn btn-approve"
                >
                  <Check />
                  Approve
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
};