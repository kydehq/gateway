import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { useVerify } from "@/api/queries";
import { useFeatures } from "@/hooks/use-features";

// Quiet infrastructure-health indicator. Sits next to the edition chip in
// the sidebar. Click → /integrity for the detail view. The full-width
// banner in the shell only fires on failure (see IntegrityBanner), so
// this chip is the healthy-state signal.
//
// Integrity verification is an Enterprise feature, so this chip is hidden in
// the sandbox edition — the EditionChip next to it carries the edition state.
export function ChainStatusChip() {
  const { signingEnabled } = useFeatures();
  const { data: v, isLoading } = useVerify();

  if (!signingEnabled) return null;

  if (isLoading || !v) {
    return <Badge variant="tag-muted">Data Integrity</Badge>;
  }

  return (
    <Link
      to="/integrity"
      aria-label={v.valid ? "Data integrity verified" : "Data integrity failure — view details"}
    >
      {/* Verified integrity is a settled-good state → green (ok), not blue. */}
      <Badge variant={v.valid ? "tag-success" : "tag-destructive"}>
        {!v.valid ? (
          <span className="h-1.5 w-1.5 rounded-full bg-destructive-foreground animate-pulse-dot" />
        ) : null}
        Data Integrity
      </Badge>
    </Link>
  );
}
