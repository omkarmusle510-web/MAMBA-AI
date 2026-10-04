import React, { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { X } from "lucide-react";
import { motionEnabled } from "./motionPrefs";
import { loadSettings } from "./settingsStore";

export interface ToastItem {
  id: string;
  text: string;
  type: "reminder" | "info" | "success" | "milestone" | "error";
}

type ToastKind = ToastItem["type"];

const DURATIONS: Record<ToastKind, number> = {
  milestone: 2400,
  info: 3600,
  success: 2600,
  reminder: 5200,
  error: 6000,
};

const TONE: Record<ToastKind, string> = {
  milestone: "t-milestone",
  info: "t-info",
  success: "t-success",
  reminder: "t-reminder",
  error: "t-error",
};

const LABEL: Record<ToastKind, string> = {
  milestone: "Milestone",
  info: "Status",
  success: "Done",
  reminder: "Reminder",
  error: "Problem",
};

const MAX_STACK = 3;

interface ToastContainerProps {
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
  onPause?: (id: string) => void;
  onResume?: (id: string) => void;
}

export const ToastContainer: React.FC<ToastContainerProps> = ({
  toasts,
  onDismiss,
  onPause,
  onResume,
}) => {
  const animate = motionEnabled(loadSettings().animations);

  return (
    <div className="toast-stack" aria-live="polite">
      <AnimatePresence>
        {toasts.map((t) => {
          const isMilestone = t.type === "milestone";

          return (
            // Exiting toasts drop pointer events: a fade-out caught while the
            // window is hidden must not leave an invisible chip eating clicks.
            <motion.div
              key={t.id}
              initial={animate ? { opacity: 0, y: -8 } : false}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4, pointerEvents: "none" }}
              transition={{ duration: animate ? 0.28 : 0, ease: [0.16, 1, 0.3, 1] }}
              className={`toast ${TONE[t.type]}${isMilestone ? " is-milestone" : ""}`}
              onMouseEnter={() => onPause?.(t.id)}
              onMouseLeave={() => onResume?.(t.id)}
            >
              <div className="toast-body">
                {!isMilestone && <div className="toast-label">{LABEL[t.type]}</div>}
                <p className="toast-text">{t.text}</p>
              </div>
              {!isMilestone && (
                <button
                  type="button"
                  onClick={() => onDismiss(t.id)}
                  className="toast-dismiss"
                  aria-label="Dismiss notification"
                >
                  <X />
                </button>
              )}
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
};

interface ToastTimer {
  timer: number;
  dueAt: number;
  ms: number;
}

// Auto-dismiss hook. Timers are owned here so hovering a toast can hold it,
// and so nothing fires into a component that has already gone away.
export function useToast() {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const timers = useRef<Map<string, ToastTimer>>(new Map());

  const dismiss = useCallback((id: string) => {
    const entry = timers.current.get(id);
    if (entry) {
      window.clearTimeout(entry.timer);
      timers.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const start = useCallback((id: string, ms: number) => {
    const existing = timers.current.get(id);
    if (existing) window.clearTimeout(existing.timer);
    const timer = window.setTimeout(() => dismiss(id), ms);
    timers.current.set(id, { timer, dueAt: Date.now() + ms, ms });
  }, [dismiss]);

  const addToast = useCallback(
    (text: string, type: ToastKind = "reminder") => {
      const id = Math.random().toString(36).substring(2, 9);
      const ms = DURATIONS[type];
      setToasts((prev) => [...prev, { id, text, type }].slice(-MAX_STACK));
      start(id, ms);
    },
    [start]
  );

  const pause = useCallback((id: string) => {
    const entry = timers.current.get(id);
    if (!entry) return;
    window.clearTimeout(entry.timer);
    timers.current.set(id, {
      timer: 0,
      dueAt: entry.dueAt,
      ms: Math.max(500, entry.dueAt - Date.now()),
    });
  }, []);

  const resume = useCallback(
    (id: string) => {
      const entry = timers.current.get(id);
      if (!entry) return;
      start(id, entry.ms);
    },
    [start]
  );

  // A toast dropped by the stack cap must not keep a timer alive.
  useEffect(() => {
    const live = new Set(toasts.map((t) => t.id));
    timers.current.forEach((entry, id) => {
      if (live.has(id)) return;
      if (entry.timer) window.clearTimeout(entry.timer);
      timers.current.delete(id);
    });
  }, [toasts]);

  useEffect(
    () => () => {
      timers.current.forEach((entry) => window.clearTimeout(entry.timer));
      timers.current.clear();
    },
    []
  );

  return { toasts, addToast, dismiss, pause, resume };
}
