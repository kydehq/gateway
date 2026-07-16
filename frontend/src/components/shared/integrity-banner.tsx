import { useLocation } from "react-router-dom";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useVerify } from "@/api/queries";
import { useFeatures } from "@/hooks/use-features";
import { ShieldAlert } from "lucide-react";

// Quiet by default: only renders when the chain is broken, and never on
// /integrity itself (that page already surfaces the same information).
// The green "intact" signal lives in the sidebar as a small chip.
//
// Integrity verification is Enterprise-only, so the banner never fires in the
// starter edition.
export function IntegrityBanner() {
  const { signingEnabled } = useFeatures();
  const { data: v, isLoading } = useVerify();
  const { pathname } = useLocation();

  if (!signingEnabled) return null;
  if (isLoading || !v) return null;
  if (v.valid) return null;
  if (pathname.startsWith("/integrity")) return null;

  return (
    <Alert className="mb-7 border-destructive/40 bg-destructive/5 text-destructive [&>svg]:text-destructive">
      <ShieldAlert className="h-4 w-4 animate-pulse-dot" />
      <AlertDescription className="font-medium">
        INTEGRITY FAILURE — {v.errors.length} error(s) detected across {v.entry_count} entries.
      </AlertDescription>
    </Alert>
  );
}
