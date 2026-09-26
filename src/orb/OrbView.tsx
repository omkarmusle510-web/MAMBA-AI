import React, { useEffect, useRef } from "react";
import { OrbRenderer, OrbState } from "./OrbRenderer";

export interface OrbViewProps {
  state: OrbState;
  inputNode?: AudioNode | AnalyserNode | null;
  outputNode?: AudioNode | AnalyserNode | null;
  size?: number;
  className?: string;
}

export const OrbView: React.FC<OrbViewProps> = ({
  state = "idle",
  inputNode,
  outputNode,
  size = 220,
  className = "",
}) => {
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
    });
    rendererRef.current = renderer;

    const handleResize = () => {
      if (rendererRef.current && canvas) {
        const w = canvas.parentElement?.clientWidth || size;
        const h = canvas.parentElement?.clientHeight || size;
        rendererRef.current.resize(w, h);
      }
    };

    const resizeObserver = new ResizeObserver(() => handleResize());
    if (canvas.parentElement) {
      resizeObserver.observe(canvas.parentElement);
    }

    return () => {
      resizeObserver.disconnect();
      renderer.dispose();
      rendererRef.current = null;
    };
  }, []);

  // Update state when Mamba Presence changes
  useEffect(() => {
    if (rendererRef.current) {
      rendererRef.current.setState(state);
    }
  }, [state]);

  // Update audio nodes dynamically if provided
  useEffect(() => {
    if (rendererRef.current) {
      rendererRef.current.setInputNode(inputNode || null);
    }
  }, [inputNode]);

  useEffect(() => {
    if (rendererRef.current) {
      rendererRef.current.setOutputNode(outputNode || null);
    }
  }, [outputNode]);

  return (
    <div
      style={{ width: size, height: size }}
      className={`relative overflow-hidden flex items-center justify-center rounded-full pointer-events-none ${className}`}
    >
      <canvas
        ref={canvasRef}
        style={{ width: "100%", height: "100%", display: "block" }}
      />
    </div>
  );
};

export default OrbView;

