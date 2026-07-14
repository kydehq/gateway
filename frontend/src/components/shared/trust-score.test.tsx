import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { TrustDimensions } from "@/api/types";
import { tierMeta, TrustBadge, DimensionBars } from "./trust-score";

describe("tierMeta", () => {
  it("maps each tier to its label and on-palette severity classes", () => {
    expect(tierMeta("autonomous")).toMatchObject({
      label: "Autonomous",
      text: "text-sev-low",
      dot: "bg-sev-low",
      border: "border-sev-low",
    });
    expect(tierMeta("monitored").text).toBe("text-sev-medium");
    expect(tierMeta("human_approval").label).toBe("Human Approval");
    expect(tierMeta("isolated").text).toBe("text-sev-critical");
  });
});

describe("TrustBadge", () => {
  it("shows the raw score and the tier label as a tooltip title", () => {
    const { container } = render(<TrustBadge score={87} tierKey="monitored" />);
    expect(screen.getByText("87")).toBeInTheDocument();
    expect(screen.getByTitle("Monitored")).toBeInTheDocument();
    expect(container.querySelector(".bg-sev-medium")).not.toBeNull();
  });
});

describe("DimensionBars", () => {
  it("clamps each dimension fill to 0–100 and rounds the displayed value", () => {
    const dims: TrustDimensions = {
      security: 90,
      compliance: 0,
      integrity: 150, // over the top → clamps to 100
      reliability: 50,
      economics: -10, // below zero → clamps to 0
    };
    const { container } = render(<DimensionBars dimensions={dims} />);

    // Fill bars are the inner divs carrying an inline width; the order matches
    // TRUST_DIMENSIONS (security, compliance, integrity, reliability, economics).
    const fills = Array.from(
      container.querySelectorAll<HTMLElement>('div[style*="width"]'),
    );
    expect(fills.map((f) => f.style.width)).toEqual([
      "90%",
      "0%",
      "100%",
      "50%",
      "0%",
    ]);

    // Clamped values that are unique in the set render as visible numbers.
    expect(screen.getByText("90")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument(); // integrity, clamped
    expect(screen.getByText("50")).toBeInTheDocument();
  });

  it("renders all five dimension labels", () => {
    const dims: TrustDimensions = {
      security: 10,
      compliance: 20,
      integrity: 30,
      reliability: 40,
      economics: 60,
    };
    render(<DimensionBars dimensions={dims} />);
    for (const label of [
      "Security",
      "Compliance",
      "Integrity",
      "Reliability",
      "Economics",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });
});
