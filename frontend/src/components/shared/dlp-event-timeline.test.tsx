import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { DlpAlertEvent } from "@/api/types";

const h = vi.hoisted(() => ({
  events: {
    data: undefined as DlpAlertEvent[] | undefined,
    isLoading: false,
    isError: false,
  },
}));
vi.mock("@/api/queries", () => ({
  useDlpAlertEvents: () => h.events,
}));

import { DlpEventTimeline } from "./dlp-event-timeline";

function makeEvent(overrides: Partial<DlpAlertEvent>): DlpAlertEvent {
  return {
    id: 1,
    alert_id: "a1",
    actor_id: null,
    actor_kind: "system",
    event_type: "status_change",
    from_status: null,
    to_status: "open",
    from_assignee: null,
    to_assignee: null,
    disposition: null,
    note: "",
    metadata: {},
    created_at: 1750000000,
    ...overrides,
  };
}

beforeEach(() => {
  h.events = { data: undefined, isLoading: false, isError: false };
});

describe("DlpEventTimeline", () => {
  it("renders nothing without an alert id", () => {
    const { container } = render(<DlpEventTimeline alertId={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a loading note while fetching", () => {
    h.events = { data: undefined, isLoading: true, isError: false };
    render(<DlpEventTimeline alertId="a1" />);
    expect(screen.getByText("Loading events…")).toBeInTheDocument();
  });

  it("shows an error note when the fetch fails", () => {
    h.events = { data: undefined, isLoading: false, isError: true };
    render(<DlpEventTimeline alertId="a1" />);
    expect(screen.getByText("Failed to load events.")).toBeInTheDocument();
  });

  it("shows the empty stub for a fresh alert", () => {
    h.events = { data: [], isLoading: false, isError: false };
    render(<DlpEventTimeline alertId="a1" />);
    expect(screen.getByText("No triage events yet.")).toBeInTheDocument();
  });

  it("renders one row per event with status transition, disposition and note", () => {
    h.events = {
      isLoading: false,
      isError: false,
      data: [
        makeEvent({ id: 1, from_status: null, to_status: "open" }),
        makeEvent({
          id: 2,
          actor_kind: "user",
          from_status: "open",
          to_status: "closed",
          disposition: "false_positive" as DlpAlertEvent["disposition"],
          note: "benign test data",
        }),
      ],
    };
    render(<DlpEventTimeline alertId="a1" />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    // Underscores in dispositions are humanized.
    expect(screen.getByText("· false positive")).toBeInTheDocument();
    expect(screen.getByText("benign test data")).toBeInTheDocument();
    expect(screen.getByText("[user]")).toBeInTheDocument();
    // Epoch-seconds timestamp renders as an ISO-ish string.
    expect(screen.getAllByText(/^2025-06-15 /).length).toBeGreaterThan(0);
  });
});
