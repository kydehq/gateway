import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const h = vi.hoisted(() => ({
  config: { data: undefined as unknown, isLoading: true },
  features: { signingEnabled: true },
}));
vi.mock("@/api/queries", () => ({ useConfiguration: () => h.config }));
vi.mock("@/hooks/use-features", () => ({ useFeatures: () => h.features }));

import SettingsSigningPage from "./signing";

beforeEach(() => {
  h.config = { data: undefined, isLoading: true };
  h.features = { signingEnabled: true };
});

describe("SettingsSigningPage", () => {
  it("shows the upgrade notice in the sandbox edition", () => {
    h.config = { data: {}, isLoading: false };
    h.features = { signingEnabled: false };
    render(<SettingsSigningPage />);
    expect(screen.getByText(/Independent audit signing/)).toBeInTheDocument();
  });

  it("renders signing details when enabled", () => {
    h.config = {
      data: {
        signing_mode: "software",
        algorithm: "Ed25519",
        tpm_available: false,
        public_key_fingerprint: "deadbeef",
        key_paths: {
          private_key: { path: "/k/priv", exists: true },
          public_key: { path: "/k/pub", exists: true },
          tpm_key: { path: "", exists: false },
        },
      },
      isLoading: false,
    };
    h.features = { signingEnabled: true };
    render(<SettingsSigningPage />);
    expect(screen.getByText("software")).toBeInTheDocument();
    expect(screen.getByText("Ed25519")).toBeInTheDocument();
    expect(screen.getByText("deadbeef")).toBeInTheDocument();
  });
});
