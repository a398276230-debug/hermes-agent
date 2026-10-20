import { describe, expect, it } from "vitest";
import { sessionCategoryQuery } from "./session-filters";
import {
  AUTOMATION_SESSION_SOURCES,
  NO_MATCHING_SESSION_SOURCE,
  sourceBelongsToCategory,
  sourceLabel,
  sourceVisual,
} from "./session-source";

describe("subagent session classification", () => {
  it("keeps delegates independently selectable, outside automation and chat sources", () => {
    expect(sourceBelongsToCategory("subagent", "subagents")).toBe(true);
    expect(sourceBelongsToCategory("subagent", "all")).toBe(true);
    expect(sourceBelongsToCategory("subagent", "automation")).toBe(false);
    expect(sourceBelongsToCategory("subagent", "chats")).toBe(false);
    for (const source of AUTOMATION_SESSION_SOURCES) {
      expect(sourceBelongsToCategory(source, "subagents")).toBe(false);
    }
    expect(sourceLabel("subagent", "子代理")).toBe("子代理");
    expect(sourceVisual("subagent")).not.toEqual(sourceVisual("unknown"));
  });

  it("overrides visibility only for explicit choices, leaving default lists config-aware", () => {
    expect(sessionCategoryQuery("subagents", null)).toEqual({
      source: "subagent", includeSubagents: true,
    });
    const chats = sessionCategoryQuery("chats", null);
    expect(chats.includeSubagents).toBeUndefined();
    expect(chats.excludeSources).toContain("subagent");
    expect(sessionCategoryQuery("all", null).includeSubagents).toBeUndefined();
    expect(sessionCategoryQuery("automation", null)).toEqual({
      sources: AUTOMATION_SESSION_SOURCES, includeSubagents: false,
    });
    expect(sessionCategoryQuery("all", ["cli", "subagent"])).toEqual({
      sources: ["cli", "subagent"], includeSubagents: true,
    });
    expect(sessionCategoryQuery("all", ["cli"]).includeSubagents).toBe(false);
    expect(sessionCategoryQuery("subagents", [])).toEqual({
      source: NO_MATCHING_SESSION_SOURCE, includeSubagents: false,
    });
  });
});
