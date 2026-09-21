// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getSessions: vi.fn(),
  getSessionMessages: vi.fn(),
  getEmptySessionsCount: vi.fn(),
  getStatus: vi.fn(),
  searchSessions: vi.fn(),
  importSessions: vi.fn(),
  exportSessionUrl: vi.fn(),
  renameSession: vi.fn(),
  pruneSessions: vi.fn(),
  deleteSession: vi.fn(),
  deleteEmptySessions: vi.fn(),
  bulkDeleteSessions: vi.fn(),
  getProfiles: vi.fn(),
  getActiveProfile: vi.fn(),
  getSessionStats: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: apiMocks,
  // ProfileProvider mirrors its selection into the api module.
  setManagementProfile: vi.fn(),
  getManagementProfile: vi.fn(() => ""),
}));
vi.mock("@/components/PlatformsCard", () => ({ PlatformsCard: () => null }));
vi.mock("@/components/Markdown", () => ({ Markdown: () => null }));

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function waitFor(cond: () => boolean, timeoutMs = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) throw new Error("waitFor: condition never became true");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
}

function click(el: Element | null) {
  if (!el) throw new Error("element not rendered");
  el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
}

const button = (label: string) => document.querySelector(`button[aria-label="${label}"]`);

async function renderSessionsPage(rows: Record<string, unknown>[]) {
  // Page list uses limit 20; the overview tab's recent-cards fetch uses 50 —
  // keep the overview empty so the list view (with row actions) renders.
  apiMocks.getSessions.mockImplementation(async (limit: number) => ({
    sessions: limit >= 50 ? [] : rows,
    total: limit >= 50 ? 0 : rows.length,
    limit,
    offset: 0,
  }));
  const [{ default: SessionsPage }, { I18nProvider }, { SystemActionsProvider }, { ProfileProvider }, { PageHeaderProvider }] =
    await Promise.all([
      import("./SessionsPage"),
      import("@/i18n"),
      import("@/contexts/SystemActions"),
      import("@/contexts/ProfileProvider"),
      import("@/contexts/PageHeaderProvider"),
    ]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root.render(
      <I18nProvider>
        <MemoryRouter>
          <SystemActionsProvider>
            <ProfileProvider>
              <PageHeaderProvider pluginTabs={[]}>
                <SessionsPage />
              </PageHeaderProvider>
            </ProfileProvider>
          </SystemActionsProvider>
        </MemoryRouter>
      </I18nProvider>,
    ),
  );
  await waitFor(() => Boolean(button("Delete session")));
}

beforeEach(() => {
  for (const fn of Object.values(apiMocks)) fn.mockReset();
  apiMocks.getStatus.mockResolvedValue({});
  apiMocks.getEmptySessionsCount.mockResolvedValue({ count: 0 });
  apiMocks.getProfiles.mockResolvedValue({ profiles: [] });
  // active === current keeps the management profile "" — the precondition
  // under which an unstamped request hits the process's own store.
  apiMocks.getActiveProfile.mockResolvedValue({ current: "default", active: "default" });
  apiMocks.getSessionStats.mockResolvedValue({ by_source: {} });
  apiMocks.getSessionMessages.mockResolvedValue({ messages: [] });
  apiMocks.deleteSession.mockResolvedValue({ ok: true });
  apiMocks.renameSession.mockResolvedValue({ ok: true, title: "Renamed" });
  apiMocks.exportSessionUrl.mockReturnValue("/api/sessions/x/export");
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 500 })));
  vi.stubGlobal("ResizeObserver", class { disconnect() {} observe() {} unobserve() {} });
  // gsap ticks through rAF; a synchronous callback recurses to death.
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0) as unknown as number);
  vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
  vi.stubGlobal("matchMedia", () => ({ addEventListener() {}, matches: false, media: "", removeEventListener() {} }));
  sessionStorage.clear();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("SessionsPage per-row profile routing (#99387)", () => {
  it("sends every per-row request to the row's owning profile, not the management default", async () => {
    await renderSessionsPage([
      { id: "sid-guanli", profile: "guanli", source: "cli", model: null, title: "Managed", started_at: 1, ended_at: null,
        last_active: 1, is_active: false, message_count: 2, tool_call_count: 0, input_tokens: 1, output_tokens: 1, preview: "hi" },
    ]);

    // expand → transcript read
    await act(async () => click(button("Delete session")!.closest("div.cursor-pointer")));
    await waitFor(() => apiMocks.getSessionMessages.mock.calls.length > 0);
    expect(apiMocks.getSessionMessages).toHaveBeenCalledWith("sid-guanli", "guanli");

    await act(async () => click(button("Export session")));
    expect(apiMocks.exportSessionUrl).toHaveBeenCalledWith("sid-guanli", "guanli");

    await act(async () => click(button("Rename session")));
    const input = document.querySelector<HTMLInputElement>('input[placeholder="Session title"]');
    if (!input) throw new Error("rename input not rendered");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "Renamed");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => click(button("Save title")));
    expect(apiMocks.renameSession).toHaveBeenCalledWith("sid-guanli", "Renamed", "guanli");

    await act(async () => click(button("Delete session")));
    await waitFor(() => Boolean(document.querySelector('[role="alertdialog"]')));
    const confirm = Array.from(document.querySelectorAll('[role="alertdialog"] button')).find(
      (b) => b.textContent?.trim() === "Delete",
    );
    await act(async () => click(confirm ?? null));
    expect(apiMocks.deleteSession).toHaveBeenCalledWith("sid-guanli", "guanli");
  });

  it("routes a search result through the profile stamped on that result", async () => {
    apiMocks.searchSessions.mockResolvedValue({
      results: [
        { id: "sid-worker", session_id: "sid-worker", profile: "worker", source: "cli", model: null,
          title: "Search hit", started_at: 1, ended_at: null, last_active: 1, is_active: false,
          message_count: 2, tool_call_count: 0, input_tokens: 1, output_tokens: 1, preview: "found",
          snippet: "found", role: "user", session_started: 1 },
      ],
    });
    await renderSessionsPage([
      { id: "sid-default", profile: "default", source: "cli", model: null, title: "Listed", started_at: 1,
        ended_at: null, last_active: 1, is_active: false, message_count: 2, tool_call_count: 0,
        input_tokens: 1, output_tokens: 1, preview: "listed" },
    ]);

    const search = document.querySelector<HTMLInputElement>('input[placeholder]');
    if (!search) throw new Error("search input not rendered");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(search, "found");
      search.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await waitFor(() => document.body.textContent?.includes("Search hit") === true);

    await act(async () => click(button("Delete session")));
    await waitFor(() => Boolean(document.querySelector('[role="alertdialog"]')));
    const confirm = Array.from(document.querySelectorAll('[role="alertdialog"] button')).find(
      (b) => b.textContent?.trim() === "Delete",
    );
    await act(async () => click(confirm ?? null));

    expect(apiMocks.deleteSession).toHaveBeenCalledWith("sid-worker", "worker");
  });
});

// Auto-refresh is a monitor: it re-reads the transcript while the agent
// writes it in another process, and MUST NOT write to the session store.
describe("SessionsPage expanded-row live tail", () => {
  const liveRow = {
    id: "sid-live",
    profile: "default",
    source: "cli",
    model: null,
    title: "Monitored",
    started_at: 1,
    ended_at: null,
    last_active: 1,
    is_active: true,
    message_count: 1,
    tool_call_count: 0,
    input_tokens: 1,
    output_tokens: 1,
    preview: "hi",
  };
  const firstMessage = { role: "system", content: "first line", timestamp: 1 };
  const secondMessage = { role: "system", content: "second line", timestamp: 2 };
  const liveToggle = () => document.getElementById("sessions-live-tail-sid-live");

  it("re-reads the transcript on a timer, shows new messages, and writes nothing", async () => {
    // Fake-but-auto-advancing timers keep waitFor's real-time polling alive
    // while advanceTimersByTime drives the 3s cadence deterministically.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      apiMocks.getSessionMessages.mockResolvedValueOnce({
        messages: [firstMessage],
      });
      apiMocks.getSessionMessages.mockResolvedValue({
        messages: [firstMessage, secondMessage],
      });
      await renderSessionsPage([liveRow]);

      await act(async () => click(button("Delete session")!.closest("div.cursor-pointer")));
      await waitFor(() => apiMocks.getSessionMessages.mock.calls.length >= 1);

      // Off until asked for.
      expect(liveToggle()?.getAttribute("aria-checked")).toBe("false");
      const beforeToggle = apiMocks.getSessionMessages.mock.calls.length;

      await act(async () => click(liveToggle()));
      expect(liveToggle()?.getAttribute("aria-checked")).toBe("true");

      await act(async () => {
        vi.advanceTimersByTime(3000);
      });
      await waitFor(() => apiMocks.getSessionMessages.mock.calls.length > beforeToggle);
      // The poll follows the row's own profile, exactly like the expand read.
      expect(apiMocks.getSessionMessages).toHaveBeenLastCalledWith("sid-live", "default");
      await waitFor(() => document.body.textContent?.includes("second line") === true);

      // Read-only: polling must not reach for any mutating endpoint.
      expect(apiMocks.renameSession).not.toHaveBeenCalled();
      expect(apiMocks.deleteSession).not.toHaveBeenCalled();
      expect(apiMocks.bulkDeleteSessions).not.toHaveBeenCalled();
      expect(apiMocks.deleteEmptySessions).not.toHaveBeenCalled();
      expect(apiMocks.pruneSessions).not.toHaveBeenCalled();
      expect(apiMocks.importSessions).not.toHaveBeenCalled();

      // Switching off stops the cadence.
      await act(async () => click(liveToggle()));
      expect(liveToggle()?.getAttribute("aria-checked")).toBe("false");
      const afterDisable = apiMocks.getSessionMessages.mock.calls.length;
      await act(async () => {
        vi.advanceTimersByTime(15000);
      });
      expect(apiMocks.getSessionMessages.mock.calls.length).toBe(afterDisable);
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops the cadence when the row is collapsed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await renderSessionsPage([liveRow]);
      const header = () => button("Delete session")!.closest("div.cursor-pointer");
      await act(async () => click(header()));
      await waitFor(() => apiMocks.getSessionMessages.mock.calls.length >= 1);

      await act(async () => click(liveToggle()));
      await act(async () => {
        vi.advanceTimersByTime(3000);
      });
      await waitFor(() => apiMocks.getSessionMessages.mock.calls.length >= 2);

      await act(async () => click(header()));
      await waitFor(() => liveToggle() === null);
      const afterCollapse = apiMocks.getSessionMessages.mock.calls.length;
      await act(async () => {
        vi.advanceTimersByTime(15000);
      });
      expect(apiMocks.getSessionMessages.mock.calls.length).toBe(afterCollapse);
    } finally {
      vi.useRealTimers();
    }
  });
});

// Tool results (read_file, terminal dumps) are the long rows on a phone, so
// they render as a collapsed summary and mount the full body only on demand.
describe("SessionsPage collapsible tool results", () => {
  const toolRow = {
    id: "sid-tools",
    profile: "default",
    source: "cli",
    model: null,
    title: "Tool heavy",
    started_at: 1,
    ended_at: null,
    last_active: 1,
    is_active: false,
    message_count: 1,
    tool_call_count: 1,
    input_tokens: 1,
    output_tokens: 1,
    preview: "read_file",
  };
  const toolMessage = {
    role: "tool",
    tool_name: "read_file",
    content: "first line of the file\nsecond line\nthird line",
    timestamp: 5,
  };

  // The whole bubble header is the toggle, so find it through the visible
  // tool label rather than a positional selector.
  const toolToggle = () => {
    const label = Array.from(document.querySelectorAll("button span")).find(
      (el) => el.textContent === "Tool: read_file",
    );
    return (label?.closest("button") ?? null) as HTMLButtonElement | null;
  };

  async function expandRow() {
    await renderSessionsPage([toolRow]);
    await expandAgain();
  }

  // When FTS is active the list is fed by search results, so the row is
  // re-rendered with the search term as its highlight — the header click is
  // the same gesture either way.
  async function expandAgain() {
    await act(async () =>
      click(button("Delete session")!.closest("div.cursor-pointer")),
    );
  }

  it("collapses by default and mounts the full body only once expanded", async () => {
    apiMocks.getSessionMessages.mockResolvedValue({ messages: [toolMessage] });
    await expandRow();
    await waitFor(() => toolToggle() !== null);

    const collapsed = toolToggle()!;
    expect(collapsed.getAttribute("aria-expanded")).toBe("false");
    // The summary shows the tool name, a one-line preview and the line count…
    expect(collapsed.textContent).toContain("Tool: read_file");
    expect(collapsed.textContent).toContain("first line of the file");
    expect(collapsed.textContent).toContain("3 lines");
    // …and the body is not in the DOM at all while collapsed.
    const bodyId = collapsed.getAttribute("aria-controls")!;
    expect(document.getElementById(bodyId)).toBeNull();

    await act(async () => click(collapsed));
    const expanded = toolToggle()!;
    expect(expanded.getAttribute("aria-expanded")).toBe("true");
    expect(document.getElementById(expanded.getAttribute("aria-controls")!)).not.toBeNull();
    // The preview is replaced by the body, not shown alongside it.
    expect(expanded.textContent).not.toContain("first line of the file");

    await act(async () => click(toolToggle()));
    expect(toolToggle()!.getAttribute("aria-expanded")).toBe("false");
    expect(document.getElementById(bodyId)).toBeNull();
  });

  it("auto-expands a tool result that matches the active search", async () => {
    apiMocks.getSessionMessages.mockResolvedValue({
      messages: [
        { ...toolMessage, content: "needle in the haystack\nmore output" },
      ],
    });
    apiMocks.searchSessions.mockResolvedValue({ results: [toolRow] });
    await renderSessionsPage([toolRow]);

    const search = document.querySelector<HTMLInputElement>("input[placeholder]");
    if (!search) throw new Error("search input not rendered");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(
        search,
        "needle",
      );
      search.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await waitFor(() => apiMocks.searchSessions.mock.calls.length > 0);

    await expandAgain();
    await waitFor(() => toolToggle() !== null);

    // The search hit is the row the reader asked for, so it starts open.
    expect(toolToggle()!.getAttribute("aria-expanded")).toBe("true");
  });
});
