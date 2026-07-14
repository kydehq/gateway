import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { statusKind, StatusBadge } from "./status-badge";

describe("statusKind", () => {
  it("classifies each tone bucket", () => {
    expect(statusKind("open")).toBe("neutral");
    expect(statusKind("monitoring")).toBe("active");
    expect(statusKind("verified")).toBe("ok");
    expect(statusKind("blocked")).toBe("bad");
  });

  it("is case-insensitive and trims surrounding whitespace", () => {
    expect(statusKind("  Resolved  ")).toBe("ok");
    expect(statusKind("BLOCKED")).toBe("bad");
  });

  it("treats space- and underscore-separated variants alike", () => {
    expect(statusKind("in review")).toBe("active");
    expect(statusKind("in_review")).toBe("active");
  });

  it("defaults unknown statuses to neutral", () => {
    expect(statusKind("frobnicate")).toBe("neutral");
  });
});

describe("StatusBadge", () => {
  it("renders the label with underscores turned into spaces", () => {
    render(<StatusBadge status="in_review" />);
    const el = screen.getByText("in review");
    expect(el).toBeInTheDocument();
    expect(el).toHaveClass("badge-status", "badge-status-active");
  });

  it("honors an explicit kind over the classifier", () => {
    render(<StatusBadge status="open" kind="bad" />);
    expect(screen.getByText("open")).toHaveClass("badge-status-bad");
  });

  it("renders a leading dot only when dot is set", () => {
    const withDot = render(<StatusBadge status="connected" dot />);
    expect(withDot.container.querySelector(".bg-current")).not.toBeNull();

    const noDot = render(<StatusBadge status="connected" />);
    expect(noDot.container.querySelector(".bg-current")).toBeNull();
  });
});
