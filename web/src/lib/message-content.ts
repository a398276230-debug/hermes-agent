/**
 * Safe text extraction for stored message bodies.
 *
 * `/api/sessions/{id}/messages` returns the rows verbatim, and the store keeps
 * multimodal content as a JSON-encoded string (`SessionMessagesMixin.
 * _encode_content`) that the reader decodes back to a list/dict. So `content`
 * is a plain string for ordinary text turns but an array of parts for anything
 * carrying an image — and `tool_calls[].function.arguments` is a string for
 * Hermes' own calls but an object for imported/foreign transcripts.
 *
 * The transcript used to assume `string` everywhere (`charCodeAt`, `split`,
 * `toLowerCase`, `JSON.parse`), so one such row crashed the whole Sessions
 * page. Everything that reads a body goes through here instead; the text
 * extraction mirrors `agent/message_content.py::flatten_message_text` so the
 * dashboard and the agent agree on what a message "says".
 */

/** Part types that carry no text — and whose payload is far too big to render. */
const MEDIA_PART_LABELS: Record<string, string> = {
  image: "[image]",
  image_url: "[image]",
  input_image: "[image]",
  audio: "[audio]",
  input_audio: "[audio]",
  video: "[video]",
  video_url: "[video]",
  input_video: "[video]",
};

/** Keys a text-bearing part may store its text under, in priority order. */
const TEXT_KEYS = [
  "text",
  "content",
  "input_text",
  "output_text",
  "summary_text",
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** JSON for anything else, with a total fallback: this must never throw. */
function jsonText(value: unknown): string {
  try {
    const json = JSON.stringify(value);
    if (typeof json === "string") return json;
  } catch {
    // Circular or unserializable (function/symbol) — fall through.
  }
  try {
    return String(value);
  } catch {
    return "";
  }
}

function joinParts(parts: unknown[]): string {
  return parts
    .map((part) => partToText(part))
    .filter((text) => text !== "")
    .join("\n");
}

function partToText(part: unknown): string {
  if (part === null || part === undefined) return "";
  if (typeof part === "string") return part;
  if (
    typeof part === "number" ||
    typeof part === "boolean" ||
    typeof part === "bigint"
  ) {
    return String(part);
  }
  if (Array.isArray(part)) return joinParts(part);

  const type =
    isRecord(part) && typeof part.type === "string"
      ? part.type.trim().toLowerCase()
      : "";
  const mediaLabel = MEDIA_PART_LABELS[type];
  if (mediaLabel) return mediaLabel;

  if (isRecord(part)) {
    for (const key of TEXT_KEYS) {
      const text = part[key];
      if (typeof text === "string") return text;
    }
    // Anthropic-style wrappers (`tool_result`, `input_audio`) nest their parts
    // under `content`; recurse so a nested text block still renders.
    const nested = part.content;
    if (typeof nested === "string") return nested;
    if (Array.isArray(nested)) return joinParts(nested);
    if (type) return `[${type}]`;
  }
  return jsonText(part);
}

/**
 * Text to hash/render for one message body.
 *
 * `null`/`undefined` → `""`; a string passes through untouched (the signature
 * must be byte-stable for ordinary text); arrays join their text parts with
 * newlines; anything else is JSON. Never throws.
 */
export function contentToText(content: unknown): string {
  if (typeof content === "string") return content;
  if (content === null || content === undefined) return "";
  if (Array.isArray(content)) return joinParts(content);
  return partToText(content);
}

/**
 * `tool_calls[].function.arguments` as a displayable string.
 *
 * Hermes persists the JSON string the model produced; some import paths store
 * the already-parsed object. A string is returned as-is so the caller's
 * pretty-print round trip still works; an object becomes compact JSON.
 */
export function toolArgumentsToText(args: unknown): string {
  if (typeof args === "string") return args;
  if (args === null || args === undefined) return "";
  return jsonText(args);
}
