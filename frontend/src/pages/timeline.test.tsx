import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import type { EntriesPage, EntryFacets, EntryRow } from "@/api/types";

const h = vi.hoisted(() => ({
  entries: {
    data: undefined as { pages: EntriesPage[] } | undefined,
    isLoading: false,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  },
  entriesParams: undefined as Record<string, unknown> | undefined,
  facets: { data: undefined as EntryFacets | undefined },
}));

vi.mock("@/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/queries")>()),
  useEntriesInfinite: (params: Record<string, unknown>) => {
    h.entriesParams = params;
    return h.entries;
  },
  useEntryFacets: () => h.facets,
}));

import TimelinePage from "./timeline";

function entry(overrides: Partial<EntryRow>): EntryRow {
  return {
    seq: 1,
    dt: "2026-07-01T12:00:00Z",
    agent_id: "agent:a",
    action_type: "chat",
    model: "gpt-x",
    upstream: "api.openai.com",
    prompt_tokens: 100,
    completion_tokens: 20,
    session_id: "session-abcdef-123456",
    tool_count: 0,
    ...overrides,
  };
}

function page(items: EntryRow[]): { pages: EntriesPage[] } {
  return { pages: [{ items, next_cursor: null, has_more: false }] };
}

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{`${loc.pathname}${loc.search}`}</div>;
}

function renderPage(initialEntry = "/timeline") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route
          path="/timeline"
          element={
            <>
              <TimelinePage />
              <LocationProbe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

// Both the mobile card list and the desktop table render in jsdom (the
// sm: breakpoint is CSS-only), so table-specific assertions scope to the
// table role.
function table() {
  return within(screen.getByRole("table"));
}

beforeEach(() => {
  h.entries = {
    data: page([]),
    isLoading: false,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  };
  h.entriesParams = undefined;
  h.facets = {
    data: { action_types: ["chat", "tool"], upstreams: ["api.openai.com"] },
  };
});

describe("TimelinePage — states", () => {
  it("shows skeleton rows while loading", () => {
    h.entries.data = undefined;
    h.entries.isLoading = true;
    const { container } = renderPage();
    expect(screen.getByText("Entry Timeline")).toBeInTheDocument();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("shows the empty state", () => {
    renderPage();
    expect(
      screen.getAllByText("No entries match the current filters.").length,
    ).toBeGreaterThan(0);
  });

  it("counts loaded rows and flags more pages with a plus", () => {
    h.entries.data = page([entry({ seq: 1 }), entry({ seq: 2 })]);
    h.entries.hasNextPage = true;
    renderPage();
    expect(screen.getByText("2 loaded+")).toBeInTheDocument();
  });

  it("shows the loading-more indicator while fetching", () => {
    h.entries.data = page([entry({})]);
    h.entries.isFetchingNextPage = true;
    renderPage();
    expect(screen.getByText("Loading more…")).toBeInTheDocument();
  });
});

describe("TimelinePage — rows", () => {
  it("renders entry fields with a truncated session link and tool summary", () => {
    h.entries.data = page([
      entry({ seq: 42, tool_count: 2, first_tool: "bash-execute-tool" }),
    ]);
    renderPage();
    const t = table();
    expect(t.getByText("42")).toBeInTheDocument();
    expect(t.getByText("agent:a")).toBeInTheDocument();
    expect(t.getByText("gpt-x")).toBeInTheDocument();
    expect(t.getByText("api.openai.com")).toBeInTheDocument();
    expect(t.getByText("100")).toBeInTheDocument();
    expect(t.getByText("20")).toBeInTheDocument();
    expect(
      t.getByRole("link", { name: "session-abcdef-1..." }),
    ).toHaveAttribute("href", "/sessions/session-abcdef-123456");
    expect(t.getByText("2 · bash-execute-too...")).toBeInTheDocument();
  });

  it("opens the entry dialog via ?entry= on row click", async () => {
    h.entries.data = page([entry({ seq: 42 })]);
    renderPage();
    await userEvent.click(table().getByText("42"));
    expect(screen.getByTestId("loc")).toHaveTextContent("/timeline?entry=42");
  });

  it("sorts loaded rows client-side when a header is toggled", async () => {
    h.entries.data = page([
      entry({ seq: 1, agent_id: "agent:z" }),
      entry({ seq: 2, agent_id: "agent:b" }),
    ]);
    renderPage();
    const firstSeq = () =>
      table().getAllByRole("row")[1].querySelector("td")?.textContent;
    // Default sort is seq desc.
    expect(firstSeq()).toBe("2");
    await userEvent.click(table().getByText("Seq"));
    expect(firstSeq()).toBe("1");
    // Sorting by agent asc puts agent:b (seq 2) first.
    await userEvent.click(table().getByText("Agent"));
    expect(firstSeq()).toBe("2");
  });
});

describe("TimelinePage — filters", () => {
  it("reads filters from the URL and shows removable chips", async () => {
    renderPage("/timeline?action=chat&upstream=api.openai.com&q=leak");
    expect(h.entriesParams).toMatchObject({
      action: "chat",
      upstream: "api.openai.com",
      q: "leak",
    });
    expect(screen.getByText("action:")).toBeInTheDocument();
    expect(screen.getByText("upstream:")).toBeInTheDocument();
    expect(screen.getByText("search:")).toBeInTheDocument();
    // Removing one chip only strips that param.
    await userEvent.click(screen.getByText("action:").closest("button")!);
    await waitFor(() =>
      expect(screen.getByTestId("loc")).toHaveTextContent(
        "/timeline?upstream=api.openai.com&q=leak",
      ),
    );
  });

  it("clears every filter at once", async () => {
    renderPage("/timeline?action=chat&q=leak");
    await userEvent.click(screen.getByText("Clear all"));
    await waitFor(() =>
      expect(screen.getByTestId("loc")).toHaveTextContent(/^\/timeline$/),
    );
    expect(screen.getByPlaceholderText("Search… ( / )")).toHaveValue("");
  });

  it("pushes the debounced search into the URL", async () => {
    renderPage();
    await userEvent.type(screen.getByPlaceholderText("Search… ( / )"), "leak");
    await waitFor(() =>
      expect(screen.getByTestId("loc")).toHaveTextContent("/timeline?q=leak"),
    );
  });

  it("offers a clear-filters shortcut in the filtered empty state", async () => {
    renderPage("/timeline?q=nomatch");
    await userEvent.click(table().getByRole("button", { name: "Clear filters" }));
    await waitFor(() =>
      expect(screen.getByTestId("loc")).toHaveTextContent(/^\/timeline$/),
    );
  });

  it("focuses search on '/'", async () => {
    renderPage();
    await userEvent.keyboard("/");
    expect(screen.getByPlaceholderText("Search… ( / )")).toHaveFocus();
  });
});

describe("TimelinePage — CSV export", () => {
  it("builds a CSV blob from the loaded rows and downloads it", async () => {
    h.entries.data = page([entry({ seq: 7, model: 'model-with-"quote' })]);
    let captured: Blob | undefined;
    const createObjectURL = vi.fn((b: Blob) => {
      captured = b;
      return "blob:mock";
    });
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      value: createObjectURL,
      configurable: true,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      value: revokeObjectURL,
      configurable: true,
    });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});

    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /Export CSV/ }));

    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock");
    // jsdom's Blob has no .text(); go through FileReader instead.
    const text = await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText(captured!);
    });
    expect(text.split("\n")[0]).toBe(
      "seq,dt,agent_id,action_type,model,upstream,prompt_tokens,completion_tokens,session_id,tool_count",
    );
    // Values are quoted and embedded quotes doubled.
    expect(text).toContain('"7"');
    expect(text).toContain('"model-with-""quote"');
  });
});
