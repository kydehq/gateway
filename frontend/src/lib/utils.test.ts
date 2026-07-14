import { describe, it, expect } from "vitest";
import { cn } from "./utils";

describe("cn", () => {
  it("joins truthy class names", () => {
    expect(cn("a", "b", "c")).toBe("a b c");
  });

  it("drops falsy / conditional values", () => {
    expect(cn("a", false, null, undefined, "", "b")).toBe("a b");
    expect(cn("base", { active: true, hidden: false })).toBe("base active");
  });

  it("merges conflicting tailwind utilities, last one winning", () => {
    // tailwind-merge: later padding overrides the earlier one.
    expect(cn("p-2", "p-4")).toBe("p-4");
    expect(cn("text-sm text-muted-foreground", "text-foreground")).toBe(
      "text-sm text-foreground",
    );
  });

  it("flattens array inputs", () => {
    expect(cn(["a", "b"], "c")).toBe("a b c");
  });
});
