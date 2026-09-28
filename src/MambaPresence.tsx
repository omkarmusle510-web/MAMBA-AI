import React from "react";
import { motion } from "motion/react";
import { ShieldAlert, AlertTriangle } from "lucide-react";
import { OrbView } from "./orb/OrbView";

export type MambaPresenceState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "permission"
  | "error";

export interface MambaPresenceProps {
  state: MambaPresenceState;
  variant?: "orb" | "pulse" | "circle";
  onClick?: () => void;
  size?: number;
  label?: string;
  showLabel?: boolean;
  className?: string;
  inputNode?: AudioNode | AnalyserNode | null;
  outputNode?: AudioNode | AnalyserNode | null;
}

export const MambaPresence: React.FC<MambaPresenceProps> = ({
  state = "idle",
  variant = "orb",
  onClick,
  size = 220,
  label,
  showLabel = true,
  className = "",
  inputNode,
  outputNode,
}) => {
  // Color palettes per state
  const config = {
    idle: {
      outer: "rgba(99, 102, 241, 0.2)",
      inner: "rgba(129, 140, 248, 0.4)",
      glow: "rgba(99, 102, 241, 0.15)",
      ring: "border-indigo-500/30",
      text: "MAMBA",
      subtext: "Ready",
    },
    listening: {
      outer: "rgba(6, 182, 212, 0.4)",
      inner: "rgba(34, 211, 238, 0.7)",
      glow: "rgba(6, 182, 212, 0.3)",
      ring: "border-cyan-400/60 shadow-[0_0_50px_rgba(6,182,212,0.4)]",
      text: "LISTENING",
      subtext: "Say your command or 'Hey Mamba'",
    },
    thinking: {
      outer: "rgba(168, 85, 247, 0.4)",
      inner: "rgba(192, 132, 252, 0.7)",
      glow: "rgba(168, 85, 247, 0.35)",
      ring: "border-purple-400/60 shadow-[0_0_60px_rgba(168,85,247,0.4)]",
      text: "THINKING",
      subtext: "Planning & reasoning...",
    },
    speaking: {
      outer: "rgba(20, 184, 166, 0.4)",
      inner: "rgba(45, 212, 191, 0.7)",
      glow: "rgba(20, 184, 166, 0.35)",
      ring: "border-teal-400/60 shadow-[0_0_60px_rgba(20,184,166,0.4)]",
      text: "SPEAKING",
      subtext: "Responding...",
    },
    permission: {
      outer: "rgba(245, 158, 11, 0.4)",
      inner: "rgba(251, 191, 36, 0.8)",
      glow: "rgba(245, 158, 11, 0.35)",
      ring: "border-amber-400/80 shadow-[0_0_60px_rgba(245,158,11,0.5)]",
      text: "APPROVAL REQUIRED",
      subtext: "Confirm elevated action in Sudo popup",
    },
    error: {
      outer: "rgba(239, 68, 68, 0.4)",
      inner: "rgba(248, 113, 113, 0.7)",
      glow: "rgba(239, 68, 68, 0.35)",
      ring: "border-rose-500/60 shadow-[0_0_50px_rgba(239,68,68,0.4)]",
      text: "ERROR",
      subtext: "Execution interrupted",
    },
  }[state];

  return (
    <div
      className={`flex flex-col items-center justify-center select-none cursor-pointer transition-all ${className}`}
      onClick={onClick}
    >
      <div
        style={{ width: size, height: size }}
        className="relative flex items-center justify-center"
      >
        {/* Outermost ambient glow */}
        <motion.div
          className="absolute inset-0 rounded-full blur-3xl pointer-events-none"
          style={{ background: config.glow }}
          animate={{
            scale: state === "listening" ? [1, 1.3, 1] : state === "thinking" ? [1, 1.2, 1] : [1, 1.08, 1],
            opacity: state === "listening" ? [0.6, 0.9, 0.6] : [0.3, 0.6, 0.3],
          }}
          transition={{
            duration: state === "listening" ? 1.8 : state === "thinking" ? 1.2 : 4,
            repeat: Infinity,
            ease: "easeInOut",
          }}
        />

        {variant === "orb" ? (
          <div className="relative z-10 flex items-center justify-center">
            <OrbView
              state={state}
              inputNode={inputNode}
              outputNode={outputNode}
              size={size}
            />
            {state === "permission" && (
              <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                <ShieldAlert className="w-12 h-12 text-amber-400 drop-shadow-[0_0_15px_rgba(245,158,11,0.8)] animate-pulse" />
              </div>
            )}
            {state === "error" && (
              <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                <AlertTriangle className="w-12 h-12 text-rose-400 drop-shadow-[0_0_15px_rgba(244,63,94,0.8)] animate-bounce" />
              </div>
            )}
          </div>
        ) : (
          /* Pulse / Ring Visual Representation */
          <motion.div
            className={`absolute rounded-full border ${config.ring}`}
            style={{
              width: size * 0.88,
              height: size * 0.88,
              background: `radial-gradient(circle, ${config.inner} 0%, ${config.outer} 70%, transparent 100%)`,
            }}
            animate={{
              scale: state === "speaking" ? [0.95, 1.05, 0.95] : state === "thinking" ? [0.98, 1.02, 0.98] : 1,
              rotate: state === "thinking" ? 360 : 0,
            }}
            transition={{
              duration: state === "thinking" ? 3 : 1.5,
              repeat: Infinity,
              ease: state === "thinking" ? "linear" : "easeInOut",
            }}
          >
            {/* Inner core node */}
            <div className="w-full h-full rounded-full flex items-center justify-center backdrop-blur-sm">
              {state === "permission" ? (
                <ShieldAlert className="w-10 h-10 text-amber-300 animate-pulse" />
              ) : state === "error" ? (
                <AlertTriangle className="w-10 h-10 text-rose-300" />
              ) : (
                <motion.div
                  className="w-12 h-12 rounded-full bg-white/20 border border-white/40 shadow-inner"
                  animate={{
                    scale: state === "listening" ? [1, 1.3, 1] : [1, 1.1, 1],
                    opacity: state === "idle" ? [0.5, 0.8, 0.5] : [0.8, 1, 0.8],
                  }}
                  transition={{
                    duration: state === "listening" ? 1.4 : 3,
                    repeat: Infinity,
                    ease: "easeInOut",
                  }}
                />
              )}
            </div>
          </motion.div>
        )}
      </div>

      {/* Label & Status */}
      {showLabel && (
        <div className="mt-4 text-center">
          <h2 className="text-sm font-bold font-mono tracking-widest text-slate-200">
            {label || config.text}
          </h2>
          <p className="text-xs font-mono text-slate-400 mt-0.5">
            {config.subtext}
          </p>
        </div>
      )}
    </div>
  );
};

export default MambaPresence;

