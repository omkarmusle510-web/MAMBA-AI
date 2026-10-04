import React, { useEffect, useRef } from "react";
import { OrbRenderer, OrbState } from "./OrbRenderer";
import { motionEnabled } from "../motionPrefs";
import { loadSettings } from "../settingsStore";

export interface OrbViewProps {
  state: OrbState;
  inputNode?: AudioNode | AnalyserNode | null;
  outputNode?: AudioNode | AnalyserNode | null;
  /** Explicit pixel size. Omit to fill the CSS-sized parent (--orb-size). */
  size?: number;
  className?: string;
}

export const OrbView: React.FC<OrbViewProps> = ({
  state = "idle",
  inputNode,
  outputNode,
  size,
  className = "",
}) => {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rendererRef = useRef<OrbRenderer | null>(null);

  // Initialize Three.js renderer
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const renderer = new OrbRenderer(canvas, {
      inputNode,
      outputNode,
      initialState: state,
      animated: motionEnabled(loadSettings().animations),
    });
    rendererRef.current = renderer;

    const handleResize = () => {
      const w = canvas.parentElement?.clientWidth || 220;
      const h = canvas.parentElement?.clientHeight || 220;
      renderer.resize(w, h);
    };

    const resizeObserver = new ResizeObserver(handleResize);
    if (canvas.parentElement) resizeObserver.observe(canvas.parentElement);

    // Capped parallax: the pointer is read against the orb's own box so the
    // field shifts a few pixels instead of tilting.
    const handlePointerMove = (e: PointerEvent) => {
      const box = wrapRef.current?.getBoundingClientRect();
      if (!box || box.width === 0 || box.height === 0) return;
      const x = ((e.clientX - box.left) / box.width) * 2 - 1;
      const y = ((e.clientY - box.top) / box.height) * 2 - 1;
      renderer.setPointer(Math.max(-1, Math.min(1, x)), Math.max(-1, Math.min(1, y)));
    };
    const handlePointerLeave = () => renderer.setPointer(0, 0);

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerleave", handlePointerLeave);

    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerleave", handlePointerLeave);
      resizeObserver.disconnect();
      renderer.dispose();
      rendererRef.current = null;
    };
  }, []);

  // Update state when Mamba Presence changes
  useEffect(() => {
    rendererRef.current?.setState(state);
  }, [state]);

  // Update audio nodes dynamically if provided
  useEffect(() => {
    rendererRef.current?.setInputNode(inputNode || null);
  }, [inputNode]);

  useEffect(() => {
    rendererRef.current?.setOutputNode(outputNode || null);
  }, [outputNode]);

  return (
    <div
      ref={wrapRef}
      className={`orb-view ${className}`}
      style={size ? { width: size, height: size } : undefined}
    >
      <canvas ref={canvasRef} className="orb-canvas" />
    </div>
  );
};

export default OrbView;
