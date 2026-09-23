import { describe, expect, it } from "vitest";

import type { SessionMessage } from "./api";
import { isPinnedToBottom, transcriptSignature } from "./session-live-tail";

const message = (over: Partial<SessionMessage> = {}): SessionMessage => ({
  role: "assistant",
  content: "hello",
  timestamp: 100,
  ...over,
});

const toolMessage = (): SessionMessage =>
  message({
    role: "assistant",
    content: null,
    tool_calls: [
      { id: "call-1", function: { name: "bash", arguments: '{"cmd":"ls"}' } },
    ],
  });

describe("transcriptSignature", () => {
  it("is empty for a transcript that has not loaded yet", () => {
    expect(transcriptSignature(null)).toBe("");
  });

  it("matches for equal content behind fresh objects", () => {
    // Every poll deserializes new objects, so equality must be by content.
    const first = [message(), toolMessage()];
    const second = [message(), toolMessage()];
    expect(transcriptSignature(second)).toBe(transcriptSignature(first));
  });

  it("changes when a message is appended", () => {
    expect(transcriptSignature([message(), message({ content: "more" })])).not.toBe(
      transcriptSignature([message()]),
    );
  });

  it("changes when a message body grows without a new row", () => {
    expect(transcriptSignature([message({ content: "hello world" })])).not.toBe(
      transcriptSignature([message({ content: "hello" })]),
    );
  });

  it("changes when tool-call arguments change", () => {
    const edited = toolMessage();
    edited.tool_calls![0].function.arguments = '{"cmd":"pwd"}';
    expect(transcriptSignature([edited])).not.toBe(
      transcriptSignature([toolMessage()]),
    );
  });

  it("changes when a timestamp is restamped", () => {
    expect(transcriptSignature([message({ timestamp: 101 })])).not.toBe(
      transcriptSignature([message({ timestamp: 100 })]),
    );
  });

  it("distinguishes a tool result by its tool_call_id", () => {
    expect(
      transcriptSignature([message({ role: "tool", tool_call_id: "call-2" })]),
    ).not.toBe(
      transcriptSignature([message({ role: "tool", tool_call_id: "call-1" })]),
    );
  });

  // Multimodal rows are persisted as JSON the reader decodes back into parts
  // (`SessionMessagesMixin._encode_content`), so a stored body is not always a
  // string. The poll stamp must survive one — `.charCodeAt` on an array is
  // exactly what used to crash the Sessions page.
  describe("non-string bodies", () => {
    const multimodal = (text: string): SessionMessage =>
      message({
        content: [
          { type: "text", text },
          { type: "image_url", image_url: { url: "data:image/png;base64,AAAA" } },
        ],
      });

    it("stamps a multimodal body without throwing", () => {
      expect(transcriptSignature([multimodal("hi")])).toBeTypeOf("string");
    });

    it("matches for equal multimodal content behind fresh arrays", () => {
      expect(transcriptSignature([multimodal("hi")])).toBe(
        transcriptSignature([multimodal("hi")]),
      );
    });

    it("changes when a multimodal text part grows", () => {
      expect(transcriptSignature([multimodal("hi there")])).not.toBe(
        transcriptSignature([multimodal("hi")]),
      );
    });

    it("changes when a non-text part appears", () => {
      const textOnly = message({ content: [{ type: "text", text: "hi" }] });
      expect(transcriptSignature([multimodal("hi")])).not.toBe(
        transcriptSignature([textOnly]),
      );
    });

    it("stamps an object body", () => {
      expect(
        transcriptSignature([message({ content: { blocks: [{ text: "hi" }] } })]),
      ).toBe(
        transcriptSignature([message({ content: { blocks: [{ text: "hi" }] } })]),
      );
    });

    it("changes when object tool arguments change", () => {
      const call = (cmd: string): SessionMessage =>
        message({
          content: null,
          tool_calls: [
            { id: "call-1", function: { name: "bash", arguments: { cmd } } },
          ],
        });
      expect(transcriptSignature([call("pwd")])).not.toBe(
        transcriptSignature([call("ls")]),
      );
    });
  });
});

describe("isPinnedToBottom", () => {
  it("is true at the exact bottom edge", () => {
    expect(
      isPinnedToBottom({ clientHeight: 600, scrollHeight: 2000, scrollTop: 1400 }),
    ).toBe(true);
  });

  it("tolerates a few pixels of scroll rounding", () => {
    expect(
      isPinnedToBottom({ clientHeight: 600, scrollHeight: 2000, scrollTop: 1395 }),
    ).toBe(true);
  });

  it("is false once the reader has scrolled up past the slack", () => {
    expect(
      isPinnedToBottom({ clientHeight: 600, scrollHeight: 2000, scrollTop: 1000 }),
    ).toBe(false);
  });

  it("is true when the transcript fits without scrolling", () => {
    expect(
      isPinnedToBottom({ clientHeight: 600, scrollHeight: 600, scrollTop: 0 }),
    ).toBe(true);
  });
});
