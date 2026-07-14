import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSort } from "./sortable-th";

describe("useSort", () => {
  it("starts from the provided initial state", () => {
    const { result } = renderHook(() => useSort({ key: "name", dir: "asc" }));
    expect(result.current.sort).toEqual({ key: "name", dir: "asc" });
  });

  it("toggling the active column flips the direction", () => {
    const { result } = renderHook(() => useSort({ key: "name", dir: "asc" }));

    act(() => result.current.toggle("name"));
    expect(result.current.sort).toEqual({ key: "name", dir: "desc" });

    act(() => result.current.toggle("name"));
    expect(result.current.sort).toEqual({ key: "name", dir: "asc" });
  });

  it("switching to a new column resets direction to asc", () => {
    // Widen the key type so the column can change; otherwise K is inferred as
    // the "name" literal and toggle("created") would not type-check.
    const { result } = renderHook(() =>
      useSort<string>({ key: "name", dir: "desc" }),
    );

    act(() => result.current.toggle("created"));
    expect(result.current.sort).toEqual({ key: "created", dir: "asc" });
  });
});
