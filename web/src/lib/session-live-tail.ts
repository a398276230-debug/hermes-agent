import type { SessionMessage } from "./api";

/**
 * Read-only transcript poll cadence for the expanded session row.
 *
 * The Sessions page is a monitor: the conversation itself is owned by the
 * CLI / gateway process, and nothing here may write to the store. Three
 * seconds is fast enough to watch a run progress and slow enough that a
 * 500-message page read stays off the user's radar.
 */
export const SESSION_LIVE_TAIL_INTERVAL_MS = 3000;

/** How close to the bottom edge still counts as "parked at the newest message". */
const BOTTOM_SLACK_PX = 24;

/** 32-bit rolling hash. Only used to answer "did this text change at all". */
function hashText(value: string): number {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) | 0;
  }
  return hash;
}

function messageSignature(message: SessionMessage): string {
  const toolCalls = message.tool_calls ?? [];
  let parts = [
    message.role,
    message.tool_name ?? "",
    message.tool_call_id ?? "",
    message.timestamp ?? 0,
    hashText(message.content ?? ""),
  ].join("\u0001");
  for (const call of toolCalls) {
    parts += `\u0002${call.id}\u0001${call.function.name}\u0001${hashText(
      call.function.arguments,
    )}`;
  }
  return parts;
}

/**
 * Content identity for one transcript page.
 *
 * A live poll almost always returns exactly what the dashboard already
 * shows — the agent appends a row only when it has something to say. Handing
 * React a fresh array on every tick would re-render every Markdown bubble and
 * tool-call block on the page three times a second for no visible change, so
 * the poll compares this stamp first and leaves state (and the user's scroll
 * position) alone when nothing moved.
 *
 * The stamp covers what the expanded view renders: role, tool name/ids,
 * timestamp, and a hash of the message body plus each tool call's arguments,
 * so a growing or rewritten message is still detected even when the row
 * count is unchanged.
 */
export function transcriptSignature(
  messages: SessionMessage[] | null,
): string {
  if (messages === null) return "";
  return messages.map(messageSignature).join("\u0003");
}

/** Scroll metrics of the transcript viewport (a DOM element fits this shape). */
export interface TranscriptScrollMetrics {
  clientHeight: number;
  scrollHeight: number;
  scrollTop: number;
}

/**
 * Whether the transcript viewport is parked at (or within a few pixels of)
 * its bottom edge.
 *
 * Live tail follows new messages to the bottom only while the reader is
 * already there: someone who scrolled up to read history should not be
 * yanked back down every time the agent appends a row.
 */
export function isPinnedToBottom(
  { clientHeight, scrollHeight, scrollTop }: TranscriptScrollMetrics,
  slackPx: number = BOTTOM_SLACK_PX,
): boolean {
  return scrollHeight - (scrollTop + clientHeight) <= slackPx;
}
