import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

// The entry-detail dialog is route-transparent: any page can open it by
// appending `?entry={ref}` to the URL. This hook hides the URL plumbing.
export function useEntryRef() {
  const [params, setParams] = useSearchParams();
  const ref = params.get("entry");

  const open = useCallback(
    (nextRef: string) => {
      const p = new URLSearchParams(params);
      p.set("entry", nextRef);
      setParams(p, { replace: false });
    },
    [params, setParams],
  );

  const close = useCallback(() => {
    const p = new URLSearchParams(params);
    p.delete("entry");
    setParams(p, { replace: false });
  }, [params, setParams]);

  return { ref, open, close };
}
