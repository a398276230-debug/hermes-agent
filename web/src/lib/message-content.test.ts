import { describe, expect, it } from "vitest";

import { contentToText, toolArgumentsToText } from "./message-content";

describe("contentToText", () => {
  it("passes ordinary text through byte-for-byte", () => {
    expect(contentToText("hello\nworld")).toBe("hello\nworld");
    // The live-tail stamp hashes this output, so "" and null must not drift.
    expect(contentToText("")).toBe("");
  });

  it("maps empty bodies to an empty string", () => {
    expect(contentToText(null)).toBe("");
    expect(contentToText(undefined)).toBe("");
  });

  it("joins the text parts of a multimodal body", () => {
    expect(
      contentToText([
        { type: "text", text: "first" },
        { type: "text", text: "second" },
      ]),
    ).toBe("first\nsecond");
  });

  it("labels media parts instead of rendering their payload", () => {
    const text = contentToText([
      { type: "text", text: "see this" },
      { type: "image_url", image_url: { url: "data:image/png;base64,AAAA" } },
      { type: "input_audio", input_audio: { data: "BBBB" } },
    ]);
    expect(text).toBe("see this\n[image]\n[audio]");
    expect(text).not.toContain("base64");
    expect(text).not.toContain("AAAA");
  });

  it("reads the common aliases a text part may use", () => {
    expect(contentToText([{ type: "output_text", text: "answer" }])).toBe(
      "answer",
    );
    expect(contentToText([{ type: "text", content: "nested" }])).toBe("nested");
  });

  it("recurses into Anthropic-style nested content blocks", () => {
    expect(
      contentToText([
        {
          type: "tool_result",
          content: [{ type: "text", text: "file contents" }],
        },
      ]),
    ).toBe("file contents");
    expect(
      contentToText([{ type: "tool_result", content: "plain result" }]),
    ).toBe("plain result");
  });

  it("names an unknown part type rather than dropping it", () => {
    expect(contentToText([{ type: "tool_use", name: "read_file" }])).toBe(
      "[tool_use]",
    );
  });

  it("renders a nested array part", () => {
    expect(contentToText([["a", "b"], "c"])).toBe("a\nb\nc");
  });

  it("serializes an object body as JSON", () => {
    expect(contentToText({ answer: 42 })).toBe('{"answer":42}');
  });

  it("never throws on values that cannot be serialized", () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(() => contentToText(circular)).not.toThrow();
    expect(() => contentToText([circular])).not.toThrow();
    expect(() => contentToText(Symbol("nope"))).not.toThrow();
  });
});

describe("toolArgumentsToText", () => {
  it("returns the persisted JSON string untouched", () => {
    expect(toolArgumentsToText('{"cmd":"ls"}')).toBe('{"cmd":"ls"}');
  });

  it("serializes an already-parsed argument object", () => {
    expect(toolArgumentsToText({ cmd: "ls" })).toBe('{"cmd":"ls"}');
  });

  it("maps missing arguments to an empty string", () => {
    expect(toolArgumentsToText(null)).toBe("");
    expect(toolArgumentsToText(undefined)).toBe("");
  });
});
