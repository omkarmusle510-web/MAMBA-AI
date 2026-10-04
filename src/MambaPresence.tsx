import React from "react";
import { OrbView } from "./orb/OrbView";
import type { OrbState } from "./orb/OrbRenderer";

/**
 * Mamba's visual presence: the Orb, plus an optional caption slot.
 *
 * State is carried as a data attribute; every per-state value lives in CSS
 * custom properties so the Orb and its aura change together. Status wording is
 * the shell's job (MambaApp.statusCopy) — this component never labels itself.
 */
export type MambaPresenceState = OrbState;

export interface MambaPresenceProps {
  state: MambaPresenceState;
  /** Explicit pixel size; omit to fill the CSS-sized parent (--orb-size). */
  size?: number;
  inputNode?: AudioNode | AnalyserNode | null;
  outputNode?: AudioNode | AnalyserNode | null;
  className?: string;
  /** Dev-preview caption only — the shell renders its own status block. */
  caption?: string;
}

export const MambaPresence: React.FC<MambaPresenceProps> = ({
  state = "idle",
  size,
  inputNode,
  outputNode,
  className = "",
  caption,
}) => (
  <div
    className={`presence ${className}`}
    data-state={state}
    style={size ? { width: size, height: size } : undefined}
  >
    <div className="presence-aura" aria-hidden="true" />
    <div className="presence-orb-slot">
      <OrbView state={state} inputNode={inputNode} outputNode={outputNode} size={size} />
    </div>
    {caption && <div className="presence-caption">{caption}</div>}
  </div>
);

export default MambaPresence;
