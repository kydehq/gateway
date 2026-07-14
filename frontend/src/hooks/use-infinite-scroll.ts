import { useEffect, useRef } from "react";

interface Args {
  onLoadMore: () => void;
  enabled: boolean;
  rootMargin?: string;
  /** Scrollable ancestor; defaults to viewport. */
  rootRef?: React.RefObject<HTMLElement | null>;
}

export function useInfiniteScroll<T extends HTMLElement>({
  onLoadMore,
  enabled,
  rootMargin = "300px",
  rootRef,
}: Args) {
  const sentinelRef = useRef<T | null>(null);
  const cbRef = useRef(onLoadMore);
  cbRef.current = onLoadMore;

  useEffect(() => {
    if (!enabled) return;
    const el = sentinelRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const e of entries) if (e.isIntersecting) cbRef.current();
      },
      { root: rootRef?.current ?? null, rootMargin },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [enabled, rootMargin, rootRef]);

  return sentinelRef;
}
