import React, { useState, useRef, useEffect } from "react";
import {
  MessageSquare,
  Search,
  Trash2,
  X,
  Minimize2,
  Maximize2,
  Filter,
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";

import { ThreadEntry, type TranscriptEntry } from "./ThreadEntry";
import { motionEnabled } from "./motionPrefs";

// Distance from the bottom within which the rail keeps following the tail.
const TAIL_FOLLOW_PX = 48;

const MIN_WIDTH = 360;
const MAX_WIDTH = 800;

interface TranscriptPanelProps {
  entries: TranscriptEntry[];
  isOpen: boolean;
  onClose: () => void;
  onClear?: () => void;
  /** In-app motion master toggle (settings.animations). */
  animations?: boolean;
}

export const TranscriptPanel: React.FC<TranscriptPanelProps> = ({
  entries,
  isOpen,
  onClose,
  onClear,
  animations = true,
}) => {
  const [searchTerm, setSearchTerm] = useState("");
  const [activeFilter, setActiveFilter] = useState<"all" | "user" | "model">("all");
  const [isMinimized, setIsMinimized] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const [panelWidth, setPanelWidth] = useState(480);

  const listRef = useRef<HTMLDivElement>(null);
  const stickToTailRef = useRef(true);
  const motionOn = motionEnabled(animations);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!isOpen) return;
      if (e.key === "Escape" || e.key === "F4") onClose();
      if (e.ctrlKey && e.key === "k") {
        e.preventDefault();
        (document.querySelector("input[data-transcript-search]") as HTMLInputElement)?.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  // Follow the tail. Streaming grows an entry's text rather than its count, so
  // this keys off the rendered list and lets the user opt out by scrolling up.
  useEffect(() => {
    const list = listRef.current;
    if (list && stickToTailRef.current) list.scrollTop = list.scrollHeight;
  }, [entries, activeFilter, searchTerm]);

  const handleListScroll = () => {
    const list = listRef.current;
    if (!list) return;
    stickToTailRef.current =
      list.scrollHeight - list.scrollTop - list.clientHeight < TAIL_FOLLOW_PX;
  };

  // Left-edge drag: width only. The rail's height belongs to the shell.
  useEffect(() => {
    if (!isResizing) return;
    const handleMouseMove = (e: MouseEvent) => {
      if (e.clientX <= 200) return;
      setPanelWidth(Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, e.clientX)));
    };
    const handleMouseUp = () => setIsResizing(false);
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isResizing]);

  const filteredEntries = entries.filter((entry) => {
    const matchesSearch =
      !searchTerm ||
      entry.content.toLowerCase().includes(searchTerm.toLowerCase()) ||
      entry.role.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesFilter = activeFilter === "all" || entry.role === activeFilter;
    return matchesSearch && matchesFilter;
  });

  const clearAll = () => {
    if (entries.length === 0) return;
    if (window.confirm("Permanently clear full conversation history? This cannot be undone."))
      onClear?.();
  };

  const entrance = motionOn
    ? { initial: { opacity: 0, x: 24 }, animate: { opacity: 1, x: 0 }, exit: { opacity: 0, x: 24 } }
    : { initial: false as const, animate: { opacity: 1, x: 0 } };

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={entrance.initial}
          animate={entrance.animate}
          exit={entrance.exit}
          transition={motionOn ? { duration: 0.22, ease: [0.22, 0.61, 0.36, 1] } : { duration: 0 }}
          className={`transcript-panel${isMinimized ? " is-minimized" : ""}`}
          style={isMinimized ? undefined : { width: `${panelWidth}px` }}
        >
          {/* Resizer handle */}
          {!isMinimized && (
            <div
              className="transcript-resizer"
              onMouseDown={(e) => {
                e.preventDefault();
                setIsResizing(true);
              }}
            />
          )}

          {/* Header bar */}
          <div className="panel-header">
            <h3 className="panel-title">
              <MessageSquare />
              Conversation
              <span className="count">{entries.length}</span>
              {activeFilter !== "all" && <span className="count">{activeFilter}</span>}
            </h3>

            <div className="panel-actions">
              <button
                onClick={() =>
                  setActiveFilter(
                    activeFilter === "all" ? "user" : activeFilter === "user" ? "model" : "all"
                  )
                }
                className="icon-btn"
                title="Toggle filter"
                aria-label="Toggle filter"
              >
                <Filter />
              </button>
              <button
                onClick={clearAll}
                className="icon-btn"
                title="Clear transcript"
                aria-label="Clear transcript"
              >
                <Trash2 />
              </button>
              <button
                onClick={() => setIsMinimized(!isMinimized)}
                className="icon-btn"
                title="Minimize"
                aria-label="Minimize"
              >
                {isMinimized ? <Maximize2 /> : <Minimize2 />}
              </button>
              <button
                onClick={onClose}
                className="icon-btn"
                title="Close (Esc)"
                aria-label="Close transcript"
              >
                <X />
              </button>
            </div>
          </div>

          {/* Search bar */}
          {!isMinimized && (
            <div className="transcript-search">
              <div className="search-field">
                <Search />
                <input
                  data-transcript-search
                  type="text"
                  placeholder="Search conversation…"
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  aria-label="Search transcript"
                />
                {searchTerm && (
                  <button
                    onClick={() => setSearchTerm("")}
                    className="search-clear"
                    aria-label="Clear search"
                  >
                    <X />
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Thread */}
          {!isMinimized && (
            <div
              className="transcript-list"
              ref={listRef}
              onScroll={handleListScroll}
              role="log"
              aria-live="polite"
            >
              {filteredEntries.length === 0 ? (
                <div className="transcript-empty">
                  <MessageSquare />
                  <div>{entries.length === 0 ? "Nothing said yet" : "No matching messages"}</div>
                </div>
              ) : (
                filteredEntries.map((entry) => (
                  <ThreadEntry key={entry.id} entry={entry} query={searchTerm} />
                ))
              )}
            </div>
          )}

          {/* Minimized view */}
          {isMinimized && (
            <div className="transcript-empty" style={{ padding: "var(--s-4)" }}>
              <div style={{ color: "var(--ink)", fontWeight: 500 }}>{entries.length} messages</div>
              <div style={{ fontSize: "var(--text-xs)", marginTop: "var(--s-1)" }}>
                Press F4 to expand
              </div>
            </div>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default TranscriptPanel;
