import React, { useState, useRef, useEffect } from "react";
import {
  MessageSquare,
  Search,
  Trash2,
  X,
  Minimize2,
  Maximize2,
  Clock,
  Copy,
  Filter,
  Palette,
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";

interface TranscriptEntry {
  id: string;
  timestamp: string;
  role: "user" | "model";
  content: string;
  isError?: boolean;
  emotion?: string;
  isSelected?: boolean;
  /** Provisional streaming text — rendered as plain text, no highlighting. */
  streaming?: boolean;
}

// Distance from the bottom within which the panel keeps following the tail.
const TAIL_FOLLOW_PX = 48;

interface TranscriptPanelProps {
  entries: TranscriptEntry[];
  isOpen: boolean;
  onClose: () => void;
  onEntriesChange?: (entries: TranscriptEntry[]) => void;
  onSelectionChange?: (selectedIds: Set<string>) => void;
  selectedIds?: Set<string>;
}

export const TranscriptPanel: React.FC<TranscriptPanelProps> = ({
  entries: initialEntries,
  isOpen,
  onClose,
  onEntriesChange,
  onSelectionChange,
  selectedIds = new Set(),
}) => {
  const [entries, setEntries] = useState<TranscriptEntry[]>(initialEntries);
  const [searchTerm, setSearchTerm] = useState("");
  const [activeFilter, setActiveFilter] = useState<"all" | "user" | "model">("all");
  const [isMinimized, setIsMinimized] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const [panelWidth, setPanelWidth] = useState(520);
  const [panelHeight, setPanelHeight] = useState(600);

  const containerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const resizeRef = useRef<HTMLDivElement>(null);
  const lastScrollTop = useRef(0);
  const stickToTailRef = useRef(true);

  // Sync local entries with parent
  useEffect(() => setEntries(initialEntries), [initialEntries]);
  useEffect(() => {
    if (onEntriesChange) onEntriesChange(entries);
  }, [entries]);
  useEffect(() => {
    if (onSelectionChange) onSelectionChange(selectedIds);
  }, [selectedIds]);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!isOpen) return;
      if (e.key === "Escape") onClose();
      if (e.key === "F4") onClose();
      if (e.ctrlKey && e.key === "k") {
        e.preventDefault();
        (document.querySelector("[data-transcript-search] input") as HTMLInputElement)?.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  // Smooth scroll to new entry. Streaming updates grow an entry's text rather
  // than its count, so follow the tail from the rendered content and let the
  // user opt out by scrolling up.
  useEffect(() => {
    const list = listRef.current;
    if (list && stickToTailRef.current) list.scrollTop = list.scrollHeight;
  }, [entries]);

  const handleListScroll = () => {
    const list = listRef.current;
    if (!list) return;
    stickToTailRef.current =
      list.scrollHeight - list.scrollTop - list.clientHeight < TAIL_FOLLOW_PX;
  };

  // Resize handlers
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing) return;
      if (e.clientX <= 200) return; // Minimum width constraint
      const newWidth = Math.max(380, Math.min(800, e.clientX));
      setPanelWidth(newWidth);
    };
    const handleMouseUp = () => setIsResizing(false);
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isResizing]);

  // Filter entries based on search and filter
  const filteredEntries = entries.filter((entry) => {
    const matchesSearch =
      entry.content.toLowerCase().includes(searchTerm.toLowerCase()) ||
      entry.role.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesFilter = activeFilter === "all" || entry.role === activeFilter;
    return matchesSearch && matchesFilter;
  });

  // Format timestamp
  const formatTimestamp = (entry: TranscriptEntry) => {
    const date = new Date(entry.timestamp);
    return date.toLocaleTimeString("en-US", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  };

  // Highlight matching text
  const highlightMatch = (text: string, term: string) => {
    if (!term) return text;
    const regex = new RegExp(`(${term})`, "gi");
    return text.replace(regex, "<mark class=\"bg-yellow-500/30 text-yellow-200\">$1</mark>");
  };

  // Copy entry to clipboard
  const copyEntry = async (entry: TranscriptEntry) => {
    const text = `[${formatTimestamp(entry)}] ${entry.role.toUpperCase()}: ${entry.content}`;
    await navigator.clipboard.writeText(text);
  };

  // Clear all entries with confirmation
  const clearAll = () => {
    if (entries.length === 0) return;
    const confirmed = window.confirm(
      "Permanently clear full conversation history? This cannot be undone."
    );
    if (confirmed) setEntries([]);
  };

  // Toggle entry selection
  const toggleEntrySelection = (entryId: string) => {
    const newSet = new Set(selectedIds);
    if (newSet.has(entryId)) newSet.delete(entryId);
    else newSet.add(entryId);
    // For now, parent component manages this; we can expose a callback if needed.
  };

  // Sync entries
  if (!isOpen) return null;

  return (
    <AnimatePresence>
      <motion.div
        ref={containerRef}
        initial={{ opacity: 0, x: 32 }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0, x: 32 }}
        transition={{ type: "spring", stiffness: 300, damping: 30 }}
        className={`transcript-panel${isMinimized ? " is-minimized" : ""}`}
        style={
          isMinimized
            ? undefined
            : { width: `${panelWidth}px`, height: `${panelHeight}px` }
        }
      >
        {/* Resizer handle */}
        {!isMinimized && (
          <div
            ref={resizeRef}
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
            Conversation Transcript
            <span className="count">({entries.length} entries)</span>
            {activeFilter !== "all" && (
              <span className="t-role model">{activeFilter}</span>
            )}
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
                placeholder="Search transcript..."
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

        {/* Content area */}
        {!isMinimized && (
          <div className="transcript-list" ref={listRef} onScroll={handleListScroll}>
            {filteredEntries.length === 0 ? (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="transcript-empty"
              >
                <MessageSquare />
                <div>No conversation history available</div>
                <div>Start a new session to begin recording dialogue</div>
              </motion.div>
            ) : (
              filteredEntries.map((entry) => (
                <motion.div
                  key={entry.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, x: -32 }}
                  transition={{ duration: 0.2 }}
                  className={`t-entry${entry.role === "user" ? " is-user" : " is-model"}${entry.isError ? " is-error" : ""}`}
                  onClick={() => toggleEntrySelection(entry.id)}
                >
                  {/* Entry header */}
                  <div className="t-entry-head">
                    <div className="t-entry-meta">
                      <span className={`t-role ${entry.role}`}>
                        {entry.role}
                      </span>
                      <span className="t-time">
                        <Clock /> {formatTimestamp(entry)}
                      </span>
                      {entry.emotion && entry.emotion !== "idle" && (
                        <span className="t-role model">
                          <Palette style={{ width: 11, height: 11 }} /> {entry.emotion}
                        </span>
                      )}
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        copyEntry(entry);
                      }}
                      className="t-copy"
                      title="Copy to clipboard"
                      aria-label="Copy entry to clipboard"
                    >
                      <Copy />
                    </button>
                  </div>

                  {/* Entry content — provisional stream text is a plain text
                      node (no per-token regex/innerHTML churn, no highlight
                      of text that is not yet the final answer). */}
                  {entry.streaming ? (
                    <div className="t-body is-streaming">{entry.content}</div>
                  ) : (
                    <div
                      className="t-body"
                      dangerouslySetInnerHTML={{ __html: highlightMatch(entry.content, searchTerm) }}
                    />
                  )}
                </motion.div>
              ))
            )}
          </div>
        )}

        {/* Minimized view */}
        {isMinimized && (
          <div className="transcript-empty" style={{ padding: "14px 20px" }}>
            <div className="panel-title" style={{ justifyContent: "center" }}>Transcript</div>
            <div style={{ color: "var(--ink)", fontWeight: 700, marginTop: 6 }}>
              {entries.length} entries
            </div>
            <div style={{ fontSize: 10, marginTop: 4 }}>Press F4 to expand</div>
          </div>
        )}
      </motion.div>
    </AnimatePresence>
  );
};

export default TranscriptPanel;