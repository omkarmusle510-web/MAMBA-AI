import React, { useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Bell, X } from "lucide-react";

export interface ToastItem {
  id: string;
  text: string;
  type: "reminder" | "info" | "success" | "milestone" | "error";
}

interface ToastContainerProps {
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
}

export const ToastContainer: React.FC<ToastContainerProps> = ({ toasts, onDismiss }) => {
  return (
    <div className="toast-stack" aria-live="polite">
      <AnimatePresence>
        {toasts.map((t) => {
          const tone =
            t.type === "error"
              ? "t-error"
              : t.type === "success"
              ? "t-success"
              : t.type === "milestone" || t.type === "info"
              ? "t-info"
              : "t-reminder";

          const label =
            t.type === "error"
              ? "Notice"
              : t.type === "success"
              ? "Success"
              : t.type === "milestone" || t.type === "info"
              ? "Status"
              : "Reminder";

          return (
            <motion.div
              key={t.id}
              initial={{ opacity: 0, y: -30, scale: 0.9 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -20, scale: 0.9 }}
              transition={{ type: "spring", damping: 20, stiffness: 300 }}
              className={`toast ${tone}`}
            >
              <div className="toast-icon">
                <Bell />
              </div>
              <div className="toast-body">
                <div className="toast-label">
                  {label}
                </div>
                <p className="toast-text">{t.text}</p>
              </div>
              <button
                onClick={() => onDismiss(t.id)}
                className="toast-dismiss"
                aria-label="Dismiss notification"
              >
                <X />
              </button>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
};

// Auto-dismiss hook
export function useToast(durationMs = 8000) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const addToast = (text: string, type: ToastItem["type"] = "reminder") => {
    const id = Math.random().toString(36).substring(2, 9);
    setToasts((prev) => [...prev, { id, text, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, durationMs);
  };

  const dismiss = (id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  return { toasts, addToast, dismiss };
}