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

/**
 * The clickable body of a session row. In the master-detail layout that click
 * means "show this transcript in the main pane" (it used to expand an
 * accordion), so every navigation gesture in these tests goes through here.
 */
function rowHeaders(): Element[] {
  const headers: Element[] = [];
  for (const btn of document.querySelectorAll('button[aria-label="Delete session"]')) {
    const header = btn.closest("div.cursor-pointer");
    if (header && !headers.includes(header)) headers.push(header);
  }
  return headers;
}

async function selectRow(index = 0) {
  await act(async () => click(rowHeaders()[index] ?? null));
}

/** Report the narrow breakpoint (below lg) so the drawer path is exercised. */
function stubNarrowViewport() {
  vi.stubGlobal("matchMedia", (query: string) => ({
    addEventListener() {},
    matches: query.includes("max-width"),
    media: query,
    removeEventListener() {},
  }));
}

async function renderSessionsPage(
  rows: Record<string, unknown>[],
  { rowActionsVisible = true } = {},
) {
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
  if (rowActionsVisible) {
    await waitFor(() => Boolean(button("Delete session")));
  } else {
    // Below lg the rows live in the drawer, which starts closed.
    await waitFor(() => Boolean(button("Session list")));
  }
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
  // jsdom has no layout engine, so the transcript's "jump to the first search
  // hit" / "open at the newest turn" effects would throw on scrollIntoView.
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("matchMedia", () => ({ addEventListener() {}, matches: false, media: "", removeEventListener() {} }));
  sessionStorage.clear();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

const sessionRow = (
  id: string,
  profile: string,
  overrides: Record<string, unknown> = {},
) => ({
  id,
  profile,
  source: "cli",
  model: null,
  title: id,
  started_at: 1,
  ended_at: null,
  last_active: 1,
  is_active: false,
  message_count: 2,
  tool_call_count: 0,
  input_tokens: 1,
  output_tokens: 1,
  preview: id,
  ...overrides,
});

describe("SessionsPage per-row profile routing (#99387)", () => {
  it("sends every per-row request to the row's owning profile, not the management default", async () => {
    await renderSessionsPage([sessionRow("sid-guanli", "guanli", { title: "Managed" })]);

    // select → transcript read
    await selectRow();
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
        { ...sessionRow("sid-worker", "worker", { title: "Search hit" }), session_id: "sid-worker",
          snippet: "found", role: "user", session_started: 1 },
      ],
    });
    await renderSessionsPage([sessionRow("sid-default", "default", { title: "Listed" })]);

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

// Master-detail: the list picks which session owns the main pane, so selecting
// a row must swap the transcript (and only then read it).
describe("SessionsPage master-detail transcript", () => {
  const firstRow = sessionRow("sid-first", "default", { title: "First" });
  const secondRow = sessionRow("sid-second", "default", { title: "Second" });

  it("opens on the first session and swaps the pane when another row is picked", async () => {
    apiMocks.getSessionMessages.mockImplementation(async (id: string) => ({
      messages: [{ role: "system", content: `body of ${id}`, timestamp: 1 }],
    }));
    await renderSessionsPage([firstRow, secondRow]);

    // No explicit pick yet — the first row stands in, so the pane is never blank.
    await waitFor(() =>
      apiMocks.getSessionMessages.mock.calls.some(([id]) => id === "sid-first"),
    );
    expect(document.body.textContent).toContain("body of sid-first");

    await selectRow(1);
    await waitFor(() =>
      apiMocks.getSessionMessages.mock.calls.some(([id]) => id === "sid-second"),
    );
    await waitFor(() => document.body.textContent?.includes("body of sid-second") === true);
    // …and the previous transcript is gone, not stacked next to it.
    expect(apiMocks.getSessionMessages).toHaveBeenLastCalledWith("sid-second", "default");
  });
});

// Auto-refresh is a monitor: it re-reads the transcript while the agent
// writes it in another process, and MUST NOT write to the session store.
describe("SessionsPage transcript live tail", () => {
  const liveRow = sessionRow("sid-live", "default", {
    title: "Monitored",
    is_active: true,
    message_count: 1,
  });
  const otherRow = sessionRow("sid-other", "default", { title: "Other" });
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
      // The poll follows the pane's own profile, exactly like the initial read.
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

  it("moves the cadence to the newly selected session and stops reading the old one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await renderSessionsPage([liveRow, otherRow]);
      await waitFor(() => apiMocks.getSessionMessages.mock.calls.length >= 1);

      // The page-level switch is remembered across selections: a user watching
      // a run keeps watching when they look at another session.
      await act(async () => click(liveToggle()));
      await act(async () => {
        vi.advanceTimersByTime(3000);
      });
      await waitFor(() =>
        apiMocks.getSessionMessages.mock.calls.filter(([id]) => id === "sid-live").length >= 2,
      );

      await selectRow(1);
      await waitFor(() => Boolean(document.getElementById("sessions-live-tail-sid-other")));
      expect(
        document.getElementById("sessions-live-tail-sid-other")?.getAttribute("aria-checked"),
      ).toBe("true");

      const liveCallsAtSwitch = apiMocks.getSessionMessages.mock.calls.filter(
        ([id]) => id === "sid-live",
      ).length;
      await act(async () => {
        vi.advanceTimersByTime(9000);
      });
      await waitFor(() =>
        apiMocks.getSessionMessages.mock.calls.filter(([id]) => id === "sid-other").length >= 2,
      );
      // The pane is keyed by session id, so the old transcript's timer died
      // with it — no orphaned polls against a session nobody is reading.
      expect(
        apiMocks.getSessionMessages.mock.calls.filter(([id]) => id === "sid-live").length,
      ).toBe(liveCallsAtSwitch);
    } finally {
      vi.useRealTimers();
    }
  });
});

// Tool results (read_file, terminal dumps) are the long rows on a phone, so
// they render as a collapsed summary and mount the full body only on demand.
describe("SessionsPage collapsible tool results", () => {
  const toolRow = sessionRow("sid-tools", "default", {
    title: "Tool heavy",
    message_count: 1,
    tool_call_count: 1,
    preview: "read_file",
  });
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
    await selectRow();
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

    await selectRow();
    await waitFor(() => toolToggle() !== null);

    // The search hit is the row the reader asked for, so it starts open.
    expect(toolToggle()!.getAttribute("aria-expanded")).toBe("true");
  });
});

// A transcript is routinely hundreds of rows of tool output. The pane is
// viewport-locked (header pinned, session rail pinned on desktop), so the only
// way back to either end — or out to another session on a phone — is the
// floating bar in the corner.
describe("SessionsPage floating transcript controls", () => {
  const longRow = sessionRow("sid-long", "default", {
    title: "Long",
    message_count: 2,
  });
  const viewport = () =>
    document.querySelector<HTMLElement>(
      '[data-testid="session-transcript-viewport"]',
    );

  /** jsdom has no layout engine, so a scroll viewport has no extent to jump
   *  within: state the extent the real browser would report. */
  function stubScrollable(el: HTMLElement) {
    Object.defineProperty(el, "scrollHeight", {
      configurable: true,
      value: 4000,
    });
    Object.defineProperty(el, "clientHeight", {
      configurable: true,
      value: 800,
    });
  }

  it("jumps the transcript viewport back to the top and down to the newest turn", async () => {
    apiMocks.getSessionMessages.mockResolvedValue({
      messages: [
        { role: "system", content: "oldest turn", timestamp: 1 },
        { role: "system", content: "newest turn", timestamp: 2 },
      ],
    });
    await renderSessionsPage([longRow]);
    await selectRow();
    await waitFor(() => document.body.textContent?.includes("newest turn") === true);

    const el = viewport();
    if (!el) throw new Error("transcript viewport not rendered");
    stubScrollable(el);
    // A reader parked somewhere in the middle of a long run.
    el.scrollTop = 1200;

    await act(async () => click(button("Back to top")));
    expect(el.scrollTop).toBe(0);

    await act(async () => click(button("Jump to latest")));
    expect(el.scrollTop).toBe(el.scrollHeight);
  });

  it("opens the session-list drawer from the floating bar on a phone", async () => {
    stubNarrowViewport();
    apiMocks.getSessionMessages.mockResolvedValue({
      messages: [{ role: "system", content: "body", timestamp: 1 }],
    });
    await renderSessionsPage([longRow], { rowActionsVisible: false });
    await waitFor(() => viewport() !== null);

    expect(document.getElementById("sessions-list-panel")).toBeNull();
    await act(async () => click(button("Switch session")));
    await waitFor(() => Boolean(document.getElementById("sessions-list-panel")));
    expect(button("Switch session")!.getAttribute("aria-expanded")).toBe("true");
  });
});

// Below lg the list is not a permanent rail: it lives behind a drawer button
// in the transcript header, and picking a session closes it.
describe("SessionsPage mobile session drawer", () => {
  const firstRow = sessionRow("sid-first", "default", { title: "First" });
  const secondRow = sessionRow("sid-second", "default", { title: "Second" });

  it("opens the list from the transcript header, switches session, and closes", async () => {
    stubNarrowViewport();
    apiMocks.getSessionMessages.mockImplementation(async (id: string) => ({
      messages: [{ role: "system", content: `body of ${id}`, timestamp: 1 }],
    }));
    await renderSessionsPage([firstRow, secondRow], { rowActionsVisible: false });

    // The desktop rail is not rendered at all in this mode.
    expect(document.getElementById("sessions-list-panel")).toBeNull();

    await act(async () => click(button("Session list")));
    await waitFor(() => Boolean(document.getElementById("sessions-list-panel")));
    expect(button("Session list")!.getAttribute("aria-expanded")).toBe("true");

    // Picking a row swaps the pane and dismisses the drawer in one gesture.
    await selectRow(1);
    await waitFor(() =>
      apiMocks.getSessionMessages.mock.calls.some(([id]) => id === "sid-second"),
    );
    await waitFor(() => document.getElementById("sessions-list-panel") === null);
    await waitFor(() => document.body.textContent?.includes("body of sid-second") === true);
  });
});

// A phone in History has one job: read the transcript. The store-wide stats
// strip is reference data, so below lg it must not spend a row of the height
// the transcript pane needs — while desktop and the Overview tab keep it.
describe("SessionsPage narrow-screen transcript height", () => {
  const row = sessionRow("sid-height", "default", { title: "Height" });
  const transcriptViewport = () =>
    document.querySelector('[data-testid="session-transcript-viewport"]');

  beforeEach(() => {
    apiMocks.getSessionMessages.mockResolvedValue({
      messages: [{ role: "system", content: "body", timestamp: 1 }],
    });
  });

  it("keeps the store-stats strip on desktop", async () => {
    await renderSessionsPage([row]);
    expect(document.body.textContent).toContain("Active in store");
  });

  it("drops the store-stats strip on a phone so the transcript owns the viewport", async () => {
    stubNarrowViewport();
    await renderSessionsPage([row], { rowActionsVisible: false });
    await waitFor(() => transcriptViewport() !== null);
    expect(document.body.textContent).not.toContain("Active in store");
  });
});
