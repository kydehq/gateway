import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDebounced } from "./use-debounced";

describe("useDebounced", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns the initial value immediately", () => {
    const { result } = renderHook(() => useDebounced("a", 500));
    expect(result.current).toBe("a");
  });

  it("defers updates until the delay elapses", () => {
    const { result, rerender } = renderHook(
      ({ v, d }) => useDebounced(v, d),
      { initialProps: { v: "a", d: 500 } },
    );

    rerender({ v: "b", d: 500 });
    expect(result.current).toBe("a"); // not yet

    act(() => {
      vi.advanceTimersByTime(499);
    });
    expect(result.current).toBe("a"); // one tick short

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toBe("b"); // delay reached
  });

  it("resets the timer on rapid successive changes (only the last value lands)", () => {
    const { result, rerender } = renderHook(
      ({ v }) => useDebounced(v, 300),
      { initialProps: { v: "a" } },
    );

    rerender({ v: "b" });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    rerender({ v: "c" }); // resets the pending timer
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(result.current).toBe("a"); // 200ms < 300ms since last change

    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe("c"); // never flashes "b"
  });
});
