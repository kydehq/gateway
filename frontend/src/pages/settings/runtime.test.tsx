import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const h = vi.hoisted(() => ({
  settings: { data: undefined as unknown, isLoading: true },
}));
vi.mock("@/api/queries", () => ({ useSettings: () => h.settings }));
// Stub the per-row field so we only test the page's filter/branch logic.
vi.mock("@/components/shared/setting-field", () => ({
  SettingField: ({ entry }: { entry: { key: string } }) => <div>field:{entry.key}</div>,
}));

import SettingsRuntimePage from "./runtime";

beforeEach(() => {
  h.settings = { data: undefined, isLoading: true };
});

describe("SettingsRuntimePage", () => {
  it("renders non-SMTP settings and filters out SMTP_* keys", () => {
    h.settings = {
      data: [
        { key: "DLP_BERT_THRESHOLD" },
        { key: "SMTP_HOST" },
        { key: "SMTP_PORT" },
        { key: "HOST_DNS_TIMEOUT_SECONDS" },
      ],
      isLoading: false,
    };
    render(<SettingsRuntimePage />);
    expect(screen.getByText("field:DLP_BERT_THRESHOLD")).toBeInTheDocument();
    expect(screen.getByText("field:HOST_DNS_TIMEOUT_SECONDS")).toBeInTheDocument();
    // SMTP_* live on their own page → filtered out here.
    expect(screen.queryByText("field:SMTP_HOST")).not.toBeInTheDocument();
    expect(screen.queryByText("field:SMTP_PORT")).not.toBeInTheDocument();
  });

  it("shows an empty message when there are no settings", () => {
    h.settings = { data: [], isLoading: false };
    render(<SettingsRuntimePage />);
    expect(screen.getByText("No runtime-tunable settings.")).toBeInTheDocument();
  });
});
