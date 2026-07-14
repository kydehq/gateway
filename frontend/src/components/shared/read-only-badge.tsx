import { Badge } from "@/components/ui/badge";

/** Shown in a page header when the current role can view but not modify the
 *  page (auditors on the Configuration surfaces). Pairs with disabled
 *  mutation controls — the badge explains why they're greyed out. */
export function ReadOnlyBadge() {
  return (
    <Badge variant="tag-muted" title="Your role has read-only access to this page">
      Read-only
    </Badge>
  );
}
