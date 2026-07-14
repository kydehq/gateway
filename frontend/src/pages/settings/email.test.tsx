import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// SmtpSettings has its own hooks/tests; stub it to a marker.
vi.mock("@/components/shared/smtp-settings", () => ({
  SmtpSettings: () => <div data-testid="smtp-settings" />,
}));

import SettingsEmailPage from "./email";

describe("SettingsEmailPage", () => {
  it("renders the email-notifications section and the SMTP settings block", () => {
    render(<SettingsEmailPage />);
    expect(screen.getByText("Email notifications")).toBeInTheDocument();
    expect(screen.getByTestId("smtp-settings")).toBeInTheDocument();
  });
});
