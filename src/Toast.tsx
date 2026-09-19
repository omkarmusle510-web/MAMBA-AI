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
    <div className="fixed top-4 left-1/2 -translate-x-1/2 z-[100] flex flex-col items-center gap-2 pointer-events-none">
      <AnimatePresence>
        {toasts.map((t) => {
          const isMilestone = t.type === "milestone" || t.type === "info";
          const isError = t.type === "error";
          const isSuccess = t.type === "success";

          const borderColor = isError
            ? "border-rose-500/30"
            : isSuccess
            ? "border-emerald-500/30"
            : isMilestone
            ? "border-cyan-500/30"
            : "border-amber-500/30";

          const labelColor = isError
            ? "text-rose-400"
            : isSuccess
            ? "text-emerald-400"
            : isMilestone
            ? "text-cyan-400"
            : "text-amber-400";

          const label = isError
            ? "Notice"
            : isSuccess
            ? "Success"
            : isMilestone
            ? "Status"
            : "Reminder";

          return (
            <motion.div
              key={t.id}
              initial={{ opacity: 0, y: -30, scale: 0.9 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -20, scale: 0.9 }}
              transition={{ type: "spring", damping: 20, stiffness: 300 }}
              className={`pointer-events-auto flex items-center gap-3 px-4 py-3 rounded-2xl border ${borderColor} bg-[#0a0a14]/90 backdrop-blur-xl shadow-[0_0_30px_rgba(0,0,0,0.5)] max-w-sm`}
            >
              <div className={`p-1.5 rounded-lg shrink-0 ${isMilestone ? "bg-cyan-500/15" : isError ? "bg-rose-500/15" : "bg-amber-500/15"}`}>
                <Bell size={14} className={labelColor} />
              </div>
              <div className="flex-1 min-w-0">
                <div className={`text-[10px] font-mono uppercase tracking-widest ${labelColor} mb-0.5`}>
                  {label}
                </div>
                <p className="text-xs text-white/90 leading-relaxed">{t.text}</p>
              </div>
              <button
                onClick={() => onDismiss(t.id)}
                className="shrink-0 p-1 rounded-lg hover:bg-white/10 text-slate-500 hover:text-white transition cursor-pointer"
              >
                <X size={12} />
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