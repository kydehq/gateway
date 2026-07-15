import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ActionBadge } from "./action-badge";

describe("ActionBadge", () => {
  it("styles known action types", () => {
    render(<ActionBadge type="policy_block" />);
    expect(screen.getByText("policy_block")).toHaveClass("text-destructive");
  });

  it("renders chat with the primary tint", () => {
    render(<ActionBadge type="chat" />);
    expect(screen.getByText("chat")).toHaveClass("text-primary");
  });

  it("falls back to the neutral style for unknown types", () => {
    render(<ActionBadge type="mystery_kind" />);
    expect(screen.getByText("mystery_kind")).toHaveClass("text-muted-foreground");
  });
});
