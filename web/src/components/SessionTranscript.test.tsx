// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { MessageBubble, MessageList } from "./SessionTranscript";
import type { SessionMessage } from "@/lib/api";

let container: HTMLDivElement;
let root: Root;

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
});

/**
 * A stored body is not always a string — multimodal rows come back from the
 * store as an array of parts (`SessionMessagesMixin._encode_content`). Before
 * these guards the transcript crashed the whole Sessions page on such a row,
 * so each case here is a regression test for that page-blanking failure.
 */
describe("MessageBubble with non-string content", () => {
  it("renders the text parts of a multimodal body", async () => {
    const msg: SessionMessage = {
      role: "user",
      content: [
        { type: "text", text: "look at this chart" },
        { type: "image_url", image_url: { url: "data:image/png;base64,AAAA" } },
      ],
    };

    await render(<MessageBubble msg={msg} />);

    const text = container.textContent ?? "";
    expect(text).toContain("look at this chart");
    expect(text).toContain("[image]");
    expect(text).not.toContain("base64");
  });

  it("still rings a search hit found in a multimodal body", async () => {
    const msg: SessionMessage = {
      role: "assistant",
      content: [{ type: "text", text: "the needle is here" }],
    };

    await render(<MessageBubble msg={msg} highlight="needle" />);

    expect(container.querySelector("[data-search-hit]")).not.toBeNull();
  });

  it("renders an object body as JSON", async () => {
    await render(
      <MessageBubble msg={{ role: "assistant", content: { answer: 42 } }} />,
    );

    expect(container.textContent).toContain("answer");
  });

  it("renders the system variant with a non-string body", async () => {
    await render(
      <MessageBubble
        msg={{ role: "system", content: [{ type: "text", text: "be brief" }] }}
      />,
    );

    expect(container.textContent).toContain("be brief");
  });
});

describe("MessageList with non-string content", () => {
  it("renders a tool row whose body is parts and whose arguments are an object", async () => {
    await render(
      <MessageList
        messages={[
          {
            role: "tool",
            tool_name: "read_file",
            content: [{ type: "text", text: "file contents here" }],
            tool_calls: [
              { id: "call-1", function: { name: "bash", arguments: { cmd: "ls" } } },
            ],
          },
        ]}
      />,
    );

    // The collapsed tool bubble previews the first line of the coerced body.
    expect(container.textContent).toContain("file contents here");
  });
});
