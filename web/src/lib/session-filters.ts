import type { SessionQueryOptions } from "./api";
import {
  AUTOMATION_SESSION_SOURCES,
  NO_MATCHING_SESSION_SOURCE,
  type SessionFilterCategory,
} from "./session-source";

/** Exact source selections must not admit siblings through the config default. */
export function sessionCategoryQuery(
  category: SessionFilterCategory,
  selectedSources: string[] | null,
): SessionQueryOptions {
  if (selectedSources !== null) {
    if (selectedSources.length === 0) {
      return { source: NO_MATCHING_SESSION_SOURCE, includeSubagents: false };
    }
    return {
      sources: selectedSources,
      includeSubagents: selectedSources.includes("subagent"),
    };
  }
  if (category === "subagents") {
    return { source: "subagent", includeSubagents: true };
  }
  if (category === "automation") {
    return { sources: AUTOMATION_SESSION_SOURCES, includeSubagents: false };
  }
  if (category === "chats") {
    // Including subagent in exclusions preserves the store's show_subagents default.
    return { excludeSources: [...AUTOMATION_SESSION_SOURCES, "subagent"] };
  }
  return {};
}
