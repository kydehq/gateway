import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const h = vi.hoisted(() => ({ me: { isAdmin: true } }));
vi.mock("@/hooks/use-me", () => ({ useMe: () => h.me }));
// Stub the data sections — we only test the page shell + read-only gating.
vi.mock("@/components/shared/agent-names-section", () => ({
  AgentNamesSection: ({ readOnly }: { readOnly: boolean }) => (
    <div>agent-names:{String(readOnly)}</div>
  ),
}));
vi.mock("@/components/shared/host-names-section", () => ({
  HostNamesSection: ({ readOnly }: { readOnly: boolean }) => (
    <div>host-names:{String(readOnly)}</div>
  ),
}));

import LabelsPage from "./labels";

function renderPage() {
  return render(
    <MemoryRouter>
      <LabelsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.me = { isAdmin: true };
});

describe("LabelsPage", () => {
  it("renders both label sections editable for an admin", () => {
    renderPage();
    expect(screen.getByText("Agent names")).toBeInTheDocument();
    expect(screen.getByText("agent-names:false")).toBeInTheDocument();
    expect(screen.getByText("host-names:false")).toBeInTheDocument();
    // No read-only badge for admins.
    expect(screen.queryByText(/Read[- ]only/i)).not.toBeInTheDocument();
  });

  it("passes readOnly + shows the badge for a non-admin", () => {
    h.me = { isAdmin: false };
    renderPage();
    expect(screen.getByText("agent-names:true")).toBeInTheDocument();
    expect(screen.getByText("host-names:true")).toBeInTheDocument();
    expect(screen.getByText(/Read[- ]only/i)).toBeInTheDocument();
  });
});
