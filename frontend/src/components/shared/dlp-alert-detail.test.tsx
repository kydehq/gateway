import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import type { DlpAlert, EntryDetail } from "@/api/types";

const h = vi.hoisted(() => ({
  entry: {
    data: undefined as unknown,
    isLoading: false,
    error: null as Error | null,
  },
}));
vi.mock("@/api/queries", () => ({
  useEntry: () => h.entry,
}));
// SheetTitle is a Radix Dialog primitive that demands a live Sheet root;
// the chrome isn't under test, so swap in plain elements.
vi.mock("@/components/ui/sheet", () => ({
  SheetHeader: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SheetTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
}));

import {
  DlpAlertDetail,
  FindingsSection,
  getSeverity,
  getAlertType,
  REDACTED_PLACEHOLDER,
} from "./dlp-alert-detail";

function makeAlert(overrides: Partial<DlpAlert>): DlpAlert {
  return {
    id: 42,
    serial_id: 42,
    created_dt: "2026-07-01T10:00:00Z",
    scanner: "regex",
    score: 0.8,
    status: "open",
    ...overrides,
  };
}

function renderDetail(alert: DlpAlert, opts?: { isAuditor?: boolean; onClick?: () => void }) {
  return render(
    <MemoryRouter>
      <DlpAlertDetail
        alert={alert}
        isAuditor={opts?.isAuditor ?? false}
        onEntityLinkClick={opts?.onClick}
      />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.entry = { data: undefined, isLoading: false, error: null };
});

describe("getSeverity", () => {
  it("prefers the explicit severity field, uppercased", () => {
    expect(getSeverity(makeAlert({ severity: "high", score: 0.1 }))).toBe("HIGH");
  });

  it("derives severity from the score otherwise", () => {
    expect(getSeverity(makeAlert({ score: 0.95 }))).toBe("CRITICAL");
    expect(getSeverity(makeAlert({ score: 0.7 }))).toBe("HIGH");
    expect(getSeverity(makeAlert({ score: 0.5 }))).toBe("MEDIUM");
    expect(getSeverity(makeAlert({ score: 0.1 }))).toBe("LOW");
  });
});

describe("getAlertType", () => {
  it("maps scanner + score to a display type", () => {
    expect(getAlertType(makeAlert({ scanner: "bert", score: 0.9 }))).toBe("Data Exfiltration");
    expect(getAlertType(makeAlert({ scanner: "bert", score: 0.5 }))).toBe("PII Leak");
    expect(getAlertType(makeAlert({ scanner: "regex", score: 0.8 }))).toBe("Policy Violation");
    expect(getAlertType(makeAlert({ scanner: "regex", score: 0.5 }))).toBe("PII Leak");
    expect(getAlertType(makeAlert({ scanner: "chain", score: 0.1 }))).toBe("Data Exfiltration");
    expect(getAlertType(makeAlert({ scanner: "other", score: 0.1 }))).toBe("Anomaly");
  });
});

describe("FindingsSection", () => {
  const regexFinding = {
    pattern_name: "Email address",
    category: "pii",
    severity: "medium",
    confidence: 0.92,
    pattern_id: "email-1",
    matched_value: REDACTED_PLACEHOLDER,
    redacted_value: "k***@e***.com",
    context_snippet: REDACTED_PLACEHOLDER,
    location: [10, 25] as [number, number],
    validator_passed: true,
    validator_applied: "checksum",
  };

  it("renders nothing when findings are missing or unparseable", () => {
    const { container } = render(
      <FindingsSection alert={makeAlert({ findings: "not json" })} isAuditor={false} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows redacted values and the obfuscation notice for non-auditors", () => {
    render(
      <FindingsSection
        alert={makeAlert({
          findings: JSON.stringify([regexFinding]),
          content_redacted: true,
        })}
        isAuditor={false}
      />,
    );
    expect(screen.getByText(/Sensitive fields are obfuscated/)).toBeInTheDocument();
    expect(screen.getByText("Email address")).toBeInTheDocument();
    expect(screen.getByText(/Match\s+\(redacted\)/)).toBeInTheDocument();
    // Non-auditors see the redacted stand-in, not the matched value.
    expect(screen.getByText("k***@e***.com")).toBeInTheDocument();
    expect(screen.getByText("email-1")).toBeInTheDocument();
    expect(screen.getByText("[10, 25]")).toBeInTheDocument();
    expect(screen.getByText("checksum: passed")).toBeInTheDocument();
  });

  it("shows the raw matched value for auditors", () => {
    render(
      <FindingsSection
        alert={makeAlert({
          findings_parsed: [{ ...regexFinding, matched_value: "kim@example.com" }] as never,
        })}
        isAuditor
      />,
    );
    expect(screen.getByText("kim@example.com")).toBeInTheDocument();
    expect(screen.queryByText(/\(redacted\)/)).not.toBeInTheDocument();
  });

  it("renders BERT findings without a match section", () => {
    render(
      <FindingsSection
        alert={makeAlert({
          scanner: "bert",
          findings_parsed: [{ label: "exfil", confidence: 0.99 }] as never,
        })}
        isAuditor={false}
      />,
    );
    expect(screen.getByText("exfil")).toBeInTheDocument();
    expect(screen.getByText("0.99")).toBeInTheDocument();
    expect(screen.queryByText(/^Match/)).not.toBeInTheDocument();
  });
});

describe("DlpAlertDetail", () => {
  it("renders the What-Happened rows and entity links", () => {
    const onClick = vi.fn();
    renderDetail(
      makeAlert({
        agent_id: "agent:builder",
        session_id: "sess-1",
        prevented: true,
      }),
      { onClick },
    );
    expect(screen.getAllByText("Policy Violation").length).toBeGreaterThan(0);
    expect(screen.getByText("HIGH")).toBeInTheDocument();
    expect(screen.getByText("request blocked")).toBeInTheDocument();
    expect(screen.getByText("prevented")).toBeInTheDocument();

    const agentLink = screen.getByRole("link", { name: "agent:builder" });
    expect(agentLink).toHaveAttribute("href", "/agents/agent%3Abuilder");
    expect(screen.getByRole("link", { name: "sess-1" })).toHaveAttribute(
      "href",
      "/sessions/sess-1",
    );
  });

  it("truncates long entry ids and notifies the host when opening the entry", async () => {
    const onClick = vi.fn();
    const entryId = "0123456789abcdef0123";
    renderDetail(makeAlert({ entry_id: entryId }), { onClick });
    const btn = screen.getByTitle(`Open entry ${entryId}`);
    expect(btn).toHaveTextContent("0123456789abcdef…");
    await userEvent.click(btn);
    expect(onClick).toHaveBeenCalled();
  });

  it("shows captured messages of the current turn to auditors", () => {
    h.entry = {
      isLoading: false,
      error: null,
      data: {
        new_message_offset: 1,
        full_messages_parsed: [
          { role: "system", content: "system prompt" },
          { role: "user", content: "please leak the IBAN" },
        ],
      } as Partial<EntryDetail>,
    };
    renderDetail(makeAlert({ entry_id: "e1" }), { isAuditor: true });
    expect(screen.getByText("Captured Messages")).toBeInTheDocument();
    expect(screen.getByText("please leak the IBAN")).toBeInTheDocument();
    // Only the messages this entry introduced are shown by default.
    expect(screen.queryByText("system prompt")).not.toBeInTheDocument();
  });

  it("falls back to the full context when the entry added no messages", () => {
    h.entry = {
      isLoading: false,
      error: null,
      data: {
        new_message_offset: 2,
        full_messages_parsed: [{ role: "user", content: "earlier message" }],
      } as Partial<EntryDetail>,
    };
    renderDetail(makeAlert({ entry_id: "e1" }), { isAuditor: true });
    expect(screen.getByText("earlier message")).toBeInTheDocument();
  });

  it("shows the empty notice when an entry has no captured messages", () => {
    h.entry = {
      isLoading: false,
      error: null,
      data: { new_message_offset: 0, full_messages_parsed: [] } as Partial<EntryDetail>,
    };
    renderDetail(makeAlert({ entry_id: "e1" }), { isAuditor: true });
    expect(
      screen.getByText("No captured messages for this entry."),
    ).toBeInTheDocument();
  });

  it("surfaces entry-fetch errors inline", () => {
    h.entry = { isLoading: false, error: new Error("404 not found"), data: undefined };
    renderDetail(makeAlert({ entry_id: "e1" }), { isAuditor: true });
    expect(
      screen.getByText(/Could not load captured messages: 404 not found/),
    ).toBeInTheDocument();
  });

  it("hides captured messages from non-auditors", () => {
    renderDetail(makeAlert({ entry_id: "e1" }), { isAuditor: false });
    expect(screen.queryByText("Captured Messages")).not.toBeInTheDocument();
  });
});
