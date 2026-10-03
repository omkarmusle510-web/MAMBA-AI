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
      ringColor: "rgba(99, 102, 241, 0.3)",
      ringShadow: "none",
      text: "Ready",
      subtext: "Click the orb or type below",
    },
    listening: {
      outer: "rgba(6, 182, 212, 0.4)",
      inner: "rgba(34, 211, 238, 0.7)",
      glow: "rgba(6, 182, 212, 0.3)",
      ringColor: "rgba(34, 211, 238, 0.6)",
      ringShadow: "0 0 50px rgba(6,182,212,0.4)",
      text: "Listening",
      subtext: "Say your command",
    },
    thinking: {
      outer: "rgba(168, 85, 247, 0.4)",
      inner: "rgba(192, 132, 252, 0.7)",
      glow: "rgba(168, 85, 247, 0.35)",
      ringColor: "rgba(192, 132, 252, 0.6)",
      ringShadow: "0 0 60px rgba(168,85,247,0.4)",
      text: "Thinking",
      subtext: "Planning & reasoning",
    },
    speaking: {
      outer: "rgba(20, 184, 166, 0.4)",
      inner: "rgba(45, 212, 191, 0.7)",
      glow: "rgba(20, 184, 166, 0.35)",
      ringColor: "rgba(45, 212, 191, 0.6)",
      ringShadow: "0 0 60px rgba(20,184,166,0.4)",
      text: "Speaking",
      subtext: "Responding",
    },
    permission: {
      outer: "rgba(245, 158, 11, 0.4)",
      inner: "rgba(251, 191, 36, 0.8)",
      glow: "rgba(245, 158, 11, 0.35)",
      ringColor: "rgba(251, 191, 36, 0.8)",
      ringShadow: "0 0 60px rgba(245,158,11,0.5)",
      text: "Approval needed",
      subtext: "Confirm the elevated action",
    },
    error: {
      outer: "rgba(239, 68, 68, 0.4)",
      inner: "rgba(248, 113, 113, 0.7)",
      glow: "rgba(239, 68, 68, 0.35)",
      ringColor: "rgba(244, 63, 94, 0.6)",
      ringShadow: "0 0 50px rgba(239,68,68,0.4)",
      text: "Error",
      subtext: "Execution interrupted",
    },
  }[state];

  return (
    <div className={`presence ${className}`}>
      <div
        style={{ width: size, height: size, position: "relative", display: "flex", alignItems: "center", justifyContent: "center" }}
      >
        {/* Outermost ambient glow - only for 2D pulse/circle variants */}
        {variant !== "orb" && (
          <motion.div
            className="presence-glow"
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
        )}

        {variant === "orb" ? (
          <div className="presence-orb-slot">
            <OrbView
              state={state}
              inputNode={inputNode}
              outputNode={outputNode}
              size={size}
            />
          </div>
        ) : (
          /* Pulse / Ring Visual Representation */
          <motion.div
            className="presence-ring"
            style={{
              width: size * 0.88,
              height: size * 0.88,
              background: `radial-gradient(circle, ${config.inner} 0%, ${config.outer} 70%, transparent 100%)`,
              border: `1px solid ${config.ringColor}`,
              boxShadow: config.ringShadow,
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
            <div className="presence-core">
              {state === "permission" ? (
                <ShieldAlert style={{ width: 40, height: 40, color: "#fcd34d" }} />
              ) : state === "error" ? (
                <AlertTriangle style={{ width: 40, height: 40, color: "#fda4af" }} />
              ) : (
                <motion.div
                  className="presence-core-dot"
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
        <div className="presence-label">
          <div className="t">{label || config.text}</div>
          <div className="s">{config.subtext}</div>
        </div>
      )}
    </div>
  );
};

export default MambaPresence;

