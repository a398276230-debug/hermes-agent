/**
 * Transcript rendering for one session: the scrollable message list, the
 * per-role bubbles, and the collapsed tool-result / tool-call cards.
 *
 * Pure presentation — every component here takes data + callbacks and knows
 * nothing about how the transcript was fetched. `SessionTranscriptPane` owns
 * the read (initial GET plus the optional live-tail poll); the Sessions page
 * owns which session is selected. Splitting it this way keeps the master-detail
 * refactor from re-growing the page file, and lets the pane render a transcript
 * for a session row without dragging the whole list along.
 */
import { useEffect, useId, useRef, useState, type RefObject } from "react";

import { Badge } from "@nous-research/ui/ui/components/badge";
import { ListItem } from "@nous-research/ui/ui/components/list-item";
import { ChevronDown, ChevronRight } from "lucide-react";

import { Markdown } from "@/components/Markdown";
import { toolResultPreview } from "@/lib/tool-result-preview";
import { contentToText, toolArgumentsToText } from "@/lib/message-content";
import { isPinnedToBottom } from "@/lib/session-live-tail";
import { timeAgo } from "@/lib/utils";
import type { SessionMessage } from "@/lib/api";
import { useI18n } from "@/i18n";
import { cn } from "@/lib/utils";

// Context-compaction handoff blocks are persisted as ``role="user"`` or
// ``role="assistant"`` with content starting with one of these prefixes —
// they're metadata inserted by ``agent/context_compressor.py``, NOT real
// turns the user typed or the model replied with. Rendering them with
// the same styling as regular messages confuses operators scrolling the
// session timeline (#29824 — "WebUI can show context compaction block
// instead of latest assistant response after compression"), so we
// detect them here and downgrade them to a muted, clearly-labelled
// "Context handoff" row.
//
// Keep these prefixes (and the END marker below) in sync with
// ``SUMMARY_PREFIX`` / ``LEGACY_SUMMARY_PREFIX`` and the
// merge-into-tail marker in ``agent/context_compressor.py``.
const COMPACTION_PREFIXES = [
  "[CONTEXT COMPACTION — REFERENCE ONLY]",
  "[CONTEXT COMPACTION - REFERENCE ONLY]",
  "[CONTEXT SUMMARY]:",
] as const;

// Marker the compressor inserts between a merged summary and the
// original tail message content. When the summary role would collide
// with both head and tail roles (e.g. head ends with ``user`` and tail
// starts with ``assistant``), the compressor merges the summary as a
// prefix on the first tail message instead of inserting a standalone
// row. We split on this marker so the WebUI still shows the original
// assistant reply as its own readable bubble — otherwise the merged
// row reads as a single opaque "Context compaction" block and the
// user can't see the reply (#29824).
const COMPACTION_END_MARKER =
  "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---";

interface CompactionSplit {
  /** Summary text (header + body, without the end marker). */
  summary: string;
  /** Original message content that came after the end marker. */
  remainder: string;
}

function splitCompactionContent(content: string): CompactionSplit | null {
  const head = content.trimStart();
  if (!COMPACTION_PREFIXES.some((p) => head.startsWith(p))) return null;
  const markerIdx = content.indexOf(COMPACTION_END_MARKER);
  if (markerIdx < 0) {
    return { summary: content, remainder: "" };
  }
  return {
    summary: content.slice(0, markerIdx),
    remainder: content
      .slice(markerIdx + COMPACTION_END_MARKER.length)
      .replace(/^\s+/, ""),
  };
}

function ToolCallBlock({
  toolCall,
}: {
  toolCall: { id: string; function: { name: string; arguments: unknown } };
}) {
  const [open, setOpen] = useState(false);
  const { t } = useI18n();

  // Imported transcripts can carry the already-parsed argument object; the
  // pretty-print round trip below only applies to the JSON string Hermes
  // itself persists.
  let args = toolArgumentsToText(toolCall.function.arguments);
  try {
    args = JSON.stringify(JSON.parse(args), null, 2);
  } catch {
    // keep as-is
  }

  return (
    <div className="mt-2 border border-warning/20 bg-warning/5">
      <ListItem
        onClick={() => setOpen(!open)}
        aria-label={`${open ? t.common.collapse : t.common.expand} tool call ${toolCall.function.name}`}
        aria-expanded={open}
        className="px-3 py-2 text-xs text-warning hover:bg-warning/10 hover:text-warning"
      >
        {open ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        <span className="font-mono-ui font-medium">
          {toolCall.function.name}
        </span>
        <span className="text-warning/50 ml-auto">{toolCall.id}</span>
      </ListItem>
      {open && (
        <pre className="border-t border-warning/20 px-3 py-2 text-xs text-warning/80 overflow-x-auto whitespace-pre-wrap font-mono">
          {args}
        </pre>
      )}
    </div>
  );
}

/**
 * Collapsible tool-result bubble.
 *
 * Tool output dwarfs everything around it, which makes scroll-back on a phone
 * miserable. The header stays visible — tool name, time, line count and a
 * one-line preview — and the full markdown body mounts only when expanded. A
 * bubble matching the active search starts expanded, since that is exactly
 * the content the reader asked to see.
 */
function ToolResultBubble({
  msg,
  content,
  label,
  style,
  isHit,
  highlightTerms,
}: {
  msg: SessionMessage;
  /** Already coerced by the caller (`msg.content` may be a multimodal array). */
  content: string;
  label: string;
  style: { bg: string; text: string };
  isHit: boolean;
  highlightTerms?: string[];
}) {
  const { t } = useI18n();
  const bodyId = useId();
  // `null` follows the default (expanded while it is a search hit); an
  // explicit toggle pins the reader's choice for the life of the bubble.
  const [override, setOverride] = useState<boolean | null>(null);
  const expanded = override ?? isHit;
  const { lines, preview } = toolResultPreview(content);

  return (
    <div
      className={`${style.bg} p-3 ${isHit ? "ring-1 ring-warning/40" : ""}`}
      data-search-hit={isHit || undefined}
    >
      {/* The whole header is the toggle: on touch there is no hover state to
          reveal a small chevron, so the target spans the full row (min-h-11
          keeps it at the 44px comfortable-tap minimum). */}
      <button
        type="button"
        onClick={() => setOverride(!expanded)}
        aria-expanded={expanded}
        aria-controls={bodyId}
        className="flex w-full min-h-11 items-start gap-2 text-left"
      >
        <span className={`mt-0.5 shrink-0 ${style.text}`} aria-hidden="true">
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </span>
        <span className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className={`text-xs font-semibold ${style.text}`}>
              {label}
            </span>
            {isHit && (
              <Badge tone="warning" className="text-xs py-0 px-1.5">
                {t.common.match}
              </Badge>
            )}
            {msg.timestamp && (
              <span className="text-xs text-text-tertiary">
                {timeAgo(msg.timestamp)}
              </span>
            )}
            {lines > 1 && (
              <span className="text-xs text-text-tertiary">
                {t.sessions.toolResultLines.replace("{count}", String(lines))}
              </span>
            )}
          </span>
          {!expanded && preview && (
            <span className="block truncate font-mono text-xs text-foreground/70">
              {preview}
            </span>
          )}
          <span className="sr-only">
            {expanded ? t.common.collapse : t.common.expand}
          </span>
        </span>
      </button>
      {expanded && content && (
        <div id={bodyId} className="mt-2">
          <Markdown content={content} highlightTerms={highlightTerms} />
        </div>
      )}
      {msg.tool_calls && msg.tool_calls.length > 0 && (
        <div className="mt-1">
          {msg.tool_calls.map((tc) => (
            <ToolCallBlock key={tc.id} toolCall={tc} />
          ))}
        </div>
      )}
    </div>
  );
}

export function MessageBubble({
  msg,
  highlight,
}: {
  msg: SessionMessage;
  highlight?: string;
}) {
  const { t } = useI18n();

  const ROLE_STYLES: Record<
    string,
    { bg: string; text: string; label: string }
  > = {
    user: {
      bg: "bg-primary/10",
      text: "text-primary",
      label: t.sessions.roles.user,
    },
    assistant: {
      bg: "bg-success/10",
      text: "text-success",
      label: t.sessions.roles.assistant,
    },
    system: {
      bg: "bg-muted",
      text: "text-muted-foreground",
      label: t.sessions.roles.system,
    },
    tool: {
      bg: "bg-warning/10",
      text: "text-warning",
      label: t.sessions.roles.tool,
    },
    // Compaction handoffs render as faded system-style metadata with a
    // distinctive label so they can't be mistaken for real assistant
    // replies during a scroll-back review (#29824).
    compaction: {
      bg: "bg-muted/50",
      text: "text-muted-foreground italic",
      label: "Context handoff",
    },
  };

  // A stored body is not always a string (see `contentToText`): multimodal
  // rows arrive as an array of parts, an object for foreign transcripts.
  // Coerce once here and every branch below — compaction split, search hit,
  // markdown body — sees text.
  const content = contentToText(msg.content);

  // When a compaction handoff is merged into the front of the first
  // tail message (the compressor's double-collision path —
  // ``_merge_summary_into_tail`` in ``agent/context_compressor.py``),
  // the message we received is ``[CONTEXT COMPACTION ...] + END_MARKER
  // + <original assistant reply>``. We split it back into two visual
  // rows here so the operator's actual answer survives as a readable
  // bubble next to the (clearly-labelled) handoff metadata (#29824).
  const compactionSplit = splitCompactionContent(content);

  if (compactionSplit && compactionSplit.remainder) {
    return (
      <>
        <MessageBubble
          msg={{ ...msg, content: compactionSplit.summary }}
          highlight={highlight}
        />
        <MessageBubble
          msg={{
            ...msg,
            content: compactionSplit.remainder,
            // The remainder is the original assistant reply that the
            // compressor pre-pended the summary to — render with the
            // normal assistant styling, NOT the muted handoff style.
            // ``isCompactionMessage`` returns false on this stripped
            // content because it no longer starts with the prefix.
          }}
          highlight={highlight}
        />
      </>
    );
  }

  const isCompaction = compactionSplit !== null;
  const style = isCompaction
    ? ROLE_STYLES.compaction
    : ROLE_STYLES[msg.role] ?? ROLE_STYLES.system;
  const label = isCompaction
    ? ROLE_STYLES.compaction.label
    : msg.tool_name
      ? `${t.sessions.roles.tool}: ${msg.tool_name}`
      : style.label;

  // Check if any search term appears as a prefix of any word in content
  const isHit = (() => {
    if (!highlight || !content) return false;
    const haystack = content.toLowerCase();
    const terms = highlight.toLowerCase().split(/\s+/).filter(Boolean);
    return terms.some((term) => haystack.includes(term));
  })();

  // Split search query into terms for inline highlighting
  const highlightTerms =
    isHit && highlight ? highlight.split(/\s+/).filter(Boolean) : undefined;

  // Tool results are the long tail of a transcript — one read_file or
  // terminal dump can be hundreds of lines. They render as a collapsed
  // summary row so a phone reader can scan past them; a search hit expands
  // automatically because that is the row the reader came for. A row with no
  // body has nothing to collapse and keeps the plain layout.
  if (msg.role === "tool" && content) {
    return (
      <ToolResultBubble
        msg={msg}
        content={content}
        label={label}
        style={style}
        isHit={isHit}
        highlightTerms={highlightTerms}
      />
    );
  }

  return (
    <div
      className={`${style.bg} p-3 ${isHit ? "ring-1 ring-warning/40" : ""}`}
      data-search-hit={isHit || undefined}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className={`text-xs font-semibold ${style.text}`}>{label}</span>
        {isHit && (
          <Badge tone="warning" className="text-xs py-0 px-1.5">
            {t.common.match}
          </Badge>
        )}
        {msg.timestamp && (
          <span className="text-xs text-text-tertiary">
            {timeAgo(msg.timestamp)}
          </span>
        )}
      </div>
      {content &&
        (msg.role === "system" ? (
          <div className="text-sm text-foreground whitespace-pre-wrap leading-relaxed">
            {content}
          </div>
        ) : (
          <Markdown content={content} highlightTerms={highlightTerms} />
        ))}
      {msg.tool_calls && msg.tool_calls.length > 0 && (
        <div className="mt-1">
          {msg.tool_calls.map((tc) => (
            <ToolCallBlock key={tc.id} toolCall={tc} />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Message list with auto-scroll to first search hit, and optional
 * follow-the-bottom behaviour for the live tail.
 *
 * The scroll container fills whatever space the pane gives it (`min-h-0
 * flex-1`), which is what makes the master-detail layout immersive: the pane
 * chrome stays put and only the transcript scrolls.
 */
export function MessageList({
  messages,
  highlight,
  follow = false,
  className,
  containerRef,
}: {
  messages: SessionMessage[];
  highlight?: string;
  follow?: boolean;
  className?: string;
  /**
   * The scrolling viewport element. The pane passes its own ref so the
   * floating quick-jump buttons drive the very element these effects follow;
   * a local ref stands in when nobody needs to reach in from outside.
   */
  containerRef?: RefObject<HTMLDivElement | null>;
}) {
  const ownRef = useRef<HTMLDivElement>(null);
  const viewportRef = containerRef ?? ownRef;
  // Tracks whether the viewport is at the bottom BEFORE new rows render, so
  // appending messages only scrolls when the reader was already following
  // along (and never yanks someone reading history back down).
  const pinnedRef = useRef(true);

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const onScroll = () => {
      pinnedRef.current = isPinnedToBottom(el);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      el.removeEventListener("scroll", onScroll);
    };
  }, [viewportRef]);

  // A search highlight owns the scroll position while it is active, so the
  // live tail stands down rather than fighting the "jump to first hit".
  const followBottom = follow && !highlight;
  const followingRef = useRef(false);

  // The newest turn is what a reader came for, so a transcript that was just
  // loaded opens at the bottom — same as a terminal or a chat. Runs once per
  // mounted transcript (the pane is keyed by session id); later arrivals are
  // governed by `followBottom` so history readers are never yanked down.
  const initialScrollRef = useRef(false);
  useEffect(() => {
    const el = viewportRef.current;
    if (!el || initialScrollRef.current || highlight) return;
    initialScrollRef.current = true;
    el.scrollTop = el.scrollHeight;
    pinnedRef.current = true;
  }, [highlight, messages, viewportRef]);

  useEffect(() => {
    const el = viewportRef.current;
    const justEnabled = followBottom && !followingRef.current;
    followingRef.current = followBottom;
    if (!el || !followBottom) return;
    if (justEnabled || pinnedRef.current) {
      el.scrollTop = el.scrollHeight;
      pinnedRef.current = true;
    }
  }, [followBottom, messages, viewportRef]);

  useEffect(() => {
    if (!highlight || !viewportRef.current) return;
    // Scroll to first hit after render
    const timer = setTimeout(() => {
      const hit = viewportRef.current?.querySelector("[data-search-hit]");
      if (hit) {
        hit.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }, 50);
    return () => clearTimeout(timer);
  }, [messages, highlight, viewportRef]);

  return (
    <div
      ref={viewportRef}
      data-testid="session-transcript-viewport"
      className={cn(
        "flex min-h-0 flex-col gap-3 overflow-y-auto pr-2",
        className,
      )}
    >
      {messages.map((msg, i) => (
        <MessageBubble key={i} msg={msg} highlight={highlight} />
      ))}
    </div>
  );
}
