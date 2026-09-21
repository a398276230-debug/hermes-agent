import { describe, expect, it } from "vitest";

import {
  TOOL_RESULT_PREVIEW_MAX,
  toolResultPreview,
} from "./tool-result-preview";

describe("toolResultPreview", () => {
  it("reports nothing for an empty result", () => {
    expect(toolResultPreview("")).toEqual({ lines: 0, preview: "", truncated: false });
    expect(toolResultPreview(null)).toEqual({ lines: 0, preview: "", truncated: false });
  });

  it("counts every line of a multi-line result", () => {
    expect(toolResultPreview("a\nb\nc").lines).toBe(3);
    // A trailing newline is a real (empty) line in the result, so it counts.
    expect(toolResultPreview("a\nb\n").lines).toBe(3);
  });

  it("previews the first non-blank line, not a leading empty one", () => {
    expect(toolResultPreview("\n\n  {\"ok\": true}\nsecond").preview).toBe('{"ok": true}');
  });

  it("collapses whitespace so a preview stays one line", () => {
    expect(toolResultPreview("a\tb   c\nd").preview).toBe("a b c");
  });

  it("clamps a long line and says so", () => {
    const long = "x".repeat(TOOL_RESULT_PREVIEW_MAX + 50);
    const { preview, truncated } = toolResultPreview(long);
    expect(truncated).toBe(true);
    expect(preview).toHaveLength(TOOL_RESULT_PREVIEW_MAX);
    expect(preview.endsWith("…")).toBe(true);
  });

  it("keeps a line that is exactly the limit unclamped", () => {
    const exact = "y".repeat(TOOL_RESULT_PREVIEW_MAX);
    expect(toolResultPreview(exact)).toEqual({
      lines: 1,
      preview: exact,
      truncated: false,
    });
  });

  it("honours a custom limit", () => {
    const { preview, truncated } = toolResultPreview("abcdefgh", 4);
    expect(preview).toHaveLength(4);
    expect(truncated).toBe(true);
  });
});
