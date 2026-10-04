import React, { useMemo, useState } from "react";

/**
 * One conversation entry, rendered at two densities.
 *
 * `rail`  — inside the docked transcript panel: role + time, clamped long
 *           answers with a "View details" affordance.
 * `stage` — the single latest answer under the orb: no role chrome, clamped
 *           to two lines. The stage never shows provisional text; the
 *           authoritative model frame is the only source of truth.
 */

export interface TranscriptEntry {
  id: string;
  timestamp: string;
  role: "user" | "model";
  content: string;
  isError?: boolean;
  /** Provisional streaming text — not yet confirmed by the model frame. */
  streaming?: boolean;
}

export type ThreadVariant = "rail" | "stage";

const CLAMP_CHARS = 420;

export interface ThreadEntryProps {
  entry: TranscriptEntry;
  variant?: ThreadVariant;
  /** Search highlight, rail only. */
  query?: string;
  defaultExpanded?: boolean;
}

/** Safe highlight: returns text segments flagged as matches (no HTML injection). */
export function highlightParts(text: string, query: string): Array<{ text: string; hit: boolean }> {
  const q = query.trim().toLowerCase();
  if (!q) return [{ text, hit: false }];
  const parts: Array<{ text: string; hit: boolean }> = [];
  const hay = text.toLowerCase();
  let from = 0;
  for (let at = hay.indexOf(q); at !== -1; at = hay.indexOf(q, from)) {
    if (at > from) parts.push({ text: text.slice(from, at), hit: false });
    parts.push({ text: text.slice(at, at + q.length), hit: true });
    from = at + q.length;
  }
  if (from < text.length) parts.push({ text: text.slice(from), hit: false });
  return parts;
}

/** HH:MM in 24h; empty for an unparseable stamp — time must not compete with text. */
function formatTime(timestamp: string): string {
  if (!timestamp) return "";
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit" });
}

export const ThreadEntry: React.FC<ThreadEntryProps> = ({
  entry,
  variant = "rail",
  query = "",
  defaultExpanded = false,
}) => {
  const [open, setOpen] = useState(defaultExpanded);
  const long =
    entry.content.length > CLAMP_CHARS || entry.content.split("\n\n").length > 2;
  const clamped = variant === "rail" && long && !open && !entry.streaming;
  const time = useMemo(() => formatTime(entry.timestamp), [entry.timestamp]);
  const verified = !entry.streaming && entry.role === "model" && variant === "rail";
  const settled = entry.streaming ? " is-streaming" : verified ? " is-verified" : "";

  return (
    <article
      className={`thread-entry is-${entry.role}${entry.isError ? " is-error" : ""}${settled}${
        variant === "stage" ? " is-stage" : ""
      }`}
    >
      <header className="thread-head">
        <span className="thread-role">{entry.role === "user" ? "You" : "Mamba"}</span>
        {time && (
          <time className="thread-time" dateTime={entry.timestamp}>
            {time}
          </time>
        )}
      </header>
      <div className={`thread-body${clamped ? " is-clamped" : ""}`}>
        {highlightParts(entry.content, query).map((part, i) =>
          part.hit ? (
            <mark className="thread-hit" key={i}>
              {part.text}
            </mark>
          ) : (
            <React.Fragment key={i}>{part.text}</React.Fragment>
          )
        )}
      </div>
      {long && variant === "rail" && (
        <button
          type="button"
          className="thread-more btn btn-ghost"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          {open ? "Less" : "View details"}
        </button>
      )}
    </article>
  );
};

export default ThreadEntry;
