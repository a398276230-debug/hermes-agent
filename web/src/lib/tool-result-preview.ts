/**
 * Collapsed-summary helpers for tool-result bubbles on the Sessions page.
 *
 * Tool results (read_file, terminal output, JSON blobs) routinely run to
 * hundreds of lines and dwarf every other row on a phone. The bubble renders
 * a one-line preview while collapsed, so this module owns the "what is worth
 * showing" decision: plain text in, numbers and a string out, no DOM.
 */

/** Longest preview we keep before clamping, in characters. */
export const TOOL_RESULT_PREVIEW_MAX = 160;

export interface ToolResultPreview {
  /** Line count of the full result — `0` for empty content. */
  lines: number;
  /** First non-blank line, whitespace-collapsed and clamped to `maxLength`. */
  preview: string;
  /** Whether `preview` was cut short of the first line's full text. */
  truncated: boolean;
}

export function toolResultPreview(
  content: string | null | undefined,
  maxLength: number = TOOL_RESULT_PREVIEW_MAX,
): ToolResultPreview {
  const text = content ?? "";
  const lines = text === "" ? 0 : text.split("\n").length;
  // The first blank line is usually formatting noise (leading newline, code
  // fence); skipping to the first real line is what makes the preview useful.
  const firstLine = text.split("\n").find((line) => line.trim() !== "") ?? "";
  const collapsed = firstLine.replace(/\s+/g, " ").trim();
  const limit = Math.max(1, maxLength);
  const truncated = collapsed.length > limit;
  return {
    lines,
    // Reserve one character for the ellipsis so the preview never exceeds
    // `limit` — the collapsed row is a single truncated line by design.
    preview: truncated ? `${collapsed.slice(0, limit - 1)}…` : collapsed,
    truncated,
  };
}
