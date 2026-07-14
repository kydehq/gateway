import { useMemo, useState } from "react";
import { toast } from "sonner";
import { RefreshCw, ShieldBan } from "lucide-react";
import { PageHeader } from "@/components/shared/page-header";
import { ReadOnlyBadge } from "@/components/shared/read-only-badge";
import { RelativeTime } from "@/components/shared/relative-time";
import { SEV_STYLE } from "@/components/shared/dlp-alert-detail";
import { SortableTh, useSort } from "@/components/shared/sortable-th";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import {
  usePolicies,
  usePreventionBulk,
  useResyncPolicies,
  useSettings,
  useTogglePolicy,
  useUpdateSetting,
} from "@/api/queries";
import { useFeatures } from "@/hooks/use-features";
import { useMe } from "@/hooks/use-me";
import { PaidLock, PaidBadge } from "@/components/shared/upgrade-lock";
import type { Policy } from "@/api/types";

type SortKey =
  | "name"
  | "category"
  | "severity"
  | "hits"
  | "last_hit_at"
  | "enabled"
  | "prevention";

const SEV_ORDER: Record<string, number> = {
  CRITICAL: 4,
  HIGH: 3,
  MEDIUM: 2,
  LOW: 1,
};

function sortGroup(items: Policy[], key: SortKey, dir: "asc" | "desc"): Policy[] {
  const out = [...items];
  out.sort((a, b) => {
    const mult = dir === "asc" ? 1 : -1;
    switch (key) {
      case "hits":
        return (a.hits - b.hits) * mult;
      case "last_hit_at": {
        const av = a.last_hit_at ? Date.parse(a.last_hit_at) : 0;
        const bv = b.last_hit_at ? Date.parse(b.last_hit_at) : 0;
        return (av - bv) * mult;
      }
      case "enabled":
        return ((a.enabled ? 1 : 0) - (b.enabled ? 1 : 0)) * mult;
      case "prevention":
        return ((a.prevention ? 1 : 0) - (b.prevention ? 1 : 0)) * mult;
      case "severity": {
        const av = SEV_ORDER[a.severity?.toUpperCase()] ?? 0;
        const bv = SEV_ORDER[b.severity?.toUpperCase()] ?? 0;
        return (av - bv) * mult;
      }
      default: {
        const av = String(a[key] ?? "").toLowerCase();
        const bv = String(b[key] ?? "").toLowerCase();
        return av.localeCompare(bv) * mult;
      }
    }
  });
  return out;
}

function Switch({
  checked,
  pending,
  label,
  onColor = "emerald",
  onToggle,
}: {
  checked: boolean;
  pending: boolean;
  label: string;
  onColor?: "emerald" | "red";
  onToggle: (next: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={pending}
      onClick={() => onToggle(!checked)}
      className={cn(
        "inline-flex h-5 w-9 items-center rounded-full border transition-colors",
        checked
          ? onColor === "red"
            ? "border-destructive/60 bg-destructive/80"
            : "border-success/60 bg-success/80"
          : "border-border bg-muted",
        pending && "opacity-60",
      )}
    >
      <span
        className={cn(
          "inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5",
        )}
      />
    </button>
  );
}

/** Header card: the two global prevention master switches plus the
 * per-pattern bulk actions. Settings are admin-only — auditors see the
 * card with switches disabled. */
function PreventionCard({ isAdmin }: { isAdmin: boolean }) {
  const { data: settings, isLoading, isError } = useSettings();
  const { enforcementEnabled } = useFeatures();
  const updateM = useUpdateSetting();
  const bulkM = usePreventionBulk();
  const [pendingKey, setPendingKey] = useState<string | null>(null);

  const valueOf = (key: string): boolean => {
    const entry = settings?.find((s) => s.key === key);
    return entry ? entry.value === true || entry.value === "true" : false;
  };
  const regexOn = valueOf("DLP_REGEX_PREVENTION");
  const bertOn = valueOf("DLP_BERT_PREVENTION");
  // Auditors get a read-only view of the whole card (writes are admin-only).
  const readOnly = !isAdmin || isError || (!isLoading && !settings);

  const onFlip = async (key: string, label: string, next: boolean) => {
    setPendingKey(key);
    try {
      await updateM.mutateAsync({ key, value: String(next) });
      toast.success(
        next
          ? `${label} is ACTIVE — qualifying hits now block requests.`
          : `${label} is inactive — detect-only.`,
      );
    } catch (err) {
      toast.error((err as Error).message || "Update failed");
    } finally {
      setPendingKey(null);
    }
  };

  const onBulk = async (enabled: boolean) => {
    try {
      const r = await bulkM.mutateAsync(enabled);
      toast.success(
        enabled
          ? `Prevention enabled for ${r.updated} patterns`
          : `Prevention disabled for ${r.updated} patterns`,
      );
    } catch (err) {
      toast.error((err as Error).message || "Bulk update failed");
    }
  };

  return (
    <div className="mb-4 rounded-md border border-border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <ShieldBan className="mt-0.5 h-5 w-5 text-muted-foreground" />
          <div>
            <div className="flex items-center gap-2 text-sm font-medium">
              Prevention
              {!enforcementEnabled && <PaidBadge />}
            </div>
            <p className="max-w-xl text-xs text-muted-foreground">
              When active, qualifying DLP hits block the request with a 403
              before it reaches the upstream. Policy Prevention is opt-in per
              pattern (Prevention column below); BERT Prevention applies
              gateway-wide. Scanner outages fail open and raise an incident.
            </p>
          </div>
        </div>
        <PaidLock
          locked={!enforcementEnabled}
          hint="Inline blocking (prevention) is part of enforcement — available in the KYDE Enterprise edition. The sandbox edition detects and alerts only."
        >
        <div className="flex flex-wrap items-center gap-6">
          <div className="flex items-center gap-2">
            <Switch
              checked={regexOn}
              pending={pendingKey === "DLP_REGEX_PREVENTION" || readOnly}
              label="Toggle Policy Prevention"
              onColor="red"
              onToggle={(next) =>
                onFlip("DLP_REGEX_PREVENTION", "Policy Prevention", next)
              }
            />
            <span className="text-xs">
              Policy Prevention{" "}
              <span
                className={cn(
                  "font-mono",
                  regexOn ? "text-destructive" : "text-muted-foreground",
                )}
              >
                {regexOn ? "active" : "inactive"}
              </span>
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Switch
              checked={bertOn}
              pending={pendingKey === "DLP_BERT_PREVENTION" || readOnly}
              label="Toggle BERT Prevention"
              onColor="red"
              onToggle={(next) =>
                onFlip("DLP_BERT_PREVENTION", "BERT Prevention", next)
              }
            />
            <span className="text-xs">
              BERT Prevention{" "}
              <span
                className={cn(
                  "font-mono",
                  bertOn ? "text-destructive" : "text-muted-foreground",
                )}
              >
                {bertOn ? "active" : "inactive"}
              </span>
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={readOnly || bulkM.isPending}
              onClick={() => onBulk(true)}
            >
              Enable all
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={readOnly || bulkM.isPending}
              onClick={() => onBulk(false)}
            >
              Disable all
            </Button>
          </div>
        </div>
        </PaidLock>
      </div>
      {!enforcementEnabled ? (
        <p className="mt-2 text-xs text-muted-foreground">
          Detection and alerts run in the sandbox edition; inline blocking
          (prevention) is available in the KYDE Enterprise edition.
        </p>
      ) : readOnly ? (
        <p className="mt-2 text-xs text-sev-medium">
          Read-only — changing policies (master switches and per-pattern
          toggles below) requires an admin account.
        </p>
      ) : null}
    </div>
  );
}

export default function PoliciesPage() {
  const { isAdmin } = useMe();
  const { data, isLoading, isError, error, dataUpdatedAt } = usePolicies();
  const { enforcementEnabled } = useFeatures();
  const toggleM = useTogglePolicy();
  const resyncM = useResyncPolicies();
  const [pending, setPending] = useState<{ id: string; field: string } | null>(
    null,
  );

  const { sort, toggle } = useSort<SortKey>({ key: "hits", dir: "desc" });

  const grouped = useMemo(() => {
    if (!data) return [];
    const bySource = new Map<string, Policy[]>();
    for (const p of data) {
      const arr = bySource.get(p.source) ?? [];
      arr.push(p);
      bySource.set(p.source, arr);
    }
    return [...bySource.entries()]
      .map(([source, items]) => ({
        source,
        items: sortGroup(items, sort.key, sort.dir),
        total: items.length,
        disabled: items.filter((p) => !p.enabled).length,
        preventing: items.filter((p) => p.prevention).length,
      }))
      .sort((a, b) => a.source.localeCompare(b.source));
  }, [data, sort]);

  const onToggle = async (p: Policy, next: boolean) => {
    setPending({ id: p.id, field: "enabled" });
    try {
      await toggleM.mutateAsync({ id: p.id, enabled: next });
      toast.success(
        next
          ? `Enabled ${p.name}`
          : `Disabled ${p.name} — gateway will stop creating alerts for this pattern.`,
      );
    } catch (err) {
      toast.error((err as Error).message || "Toggle failed");
    } finally {
      setPending(null);
    }
  };

  const onTogglePrevention = async (p: Policy, next: boolean) => {
    setPending({ id: p.id, field: "prevention" });
    try {
      await toggleM.mutateAsync({ id: p.id, prevention: next });
      toast.success(
        next
          ? `${p.name} now BLOCKS requests when Policy Prevention is active.`
          : `${p.name} is detect-only again.`,
      );
    } catch (err) {
      toast.error((err as Error).message || "Toggle failed");
    } finally {
      setPending(null);
    }
  };

  const onResync = async () => {
    try {
      const body = await resyncM.mutateAsync();
      toast.success(`Pushed ${body.loaded} patterns to dlp-regex`);
    } catch (err) {
      toast.error((err as Error).message || "Re-sync failed");
    }
  };

  const colSpan = 8;

  return (
    <>
      <PageHeader
        title="Policies"
        description="Bundled DLP regex patterns. Disable noisy patterns to suppress their alerts gateway-wide; toggling re-pushes the active set to dlp-regex."
        lastUpdated={dataUpdatedAt}
        actions={
          <div className="flex items-center gap-2">
            {!isAdmin && <ReadOnlyBadge />}
            <Button
              size="sm"
              variant="outline"
              onClick={onResync}
              disabled={!isAdmin || resyncM.isPending}
            >
              <RefreshCw className={cn("mr-1 h-4 w-4", resyncM.isPending && "animate-spin")} />
              Re-sync to dlp-regex
            </Button>
          </div>
        }
      />

      <PreventionCard isAdmin={isAdmin} />

      <div className="overflow-x-auto rounded-md border border-border">
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-card">
            <TableRow>
              <SortableTh sortKey="name" sort={sort} toggle={toggle}>
                Name
              </SortableTh>
              <TableHead>Pattern</TableHead>
              <SortableTh sortKey="category" sort={sort} toggle={toggle}>
                Category
              </SortableTh>
              <SortableTh sortKey="severity" sort={sort} toggle={toggle}>
                Severity
              </SortableTh>
              <SortableTh sortKey="hits" sort={sort} toggle={toggle}>
                <span className="block text-right">Hits</span>
              </SortableTh>
              <SortableTh sortKey="last_hit_at" sort={sort} toggle={toggle}>
                Last hit
              </SortableTh>
              <SortableTh sortKey="enabled" sort={sort} toggle={toggle}>
                <span className="block text-right">Enabled</span>
              </SortableTh>
              <SortableTh sortKey="prevention" sort={sort} toggle={toggle}>
                <span className="block text-right">Prevention</span>
              </SortableTh>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 4 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={colSpan}>
                    <Skeleton className="h-4 w-full" />
                  </TableCell>
                </TableRow>
              ))
            ) : isError ? (
              <TableRow>
                <TableCell
                  colSpan={colSpan}
                  className="py-8 text-center text-sm text-destructive"
                >
                  Failed to load: {(error as Error)?.message ?? "unknown error"}
                </TableCell>
              </TableRow>
            ) : grouped.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={colSpan}
                  className="py-8 text-center text-sm text-muted-foreground"
                >
                  No bundled patterns. The gateway image was built without
                  any pattern YAML.
                </TableCell>
              </TableRow>
            ) : (
              grouped.flatMap((group) => [
                <TableRow
                  key={`hdr-${group.source}`}
                  className="bg-muted/40 hover:bg-muted/40"
                >
                  <TableCell
                    colSpan={colSpan}
                    className="py-1.5 text-xs font-mono uppercase text-muted-foreground"
                  >
                    {group.source} · {group.total} pattern
                    {group.total === 1 ? "" : "s"}
                    {group.disabled > 0 && (
                      <span className="ml-2 text-sev-medium">
                        · {group.disabled} disabled
                      </span>
                    )}
                    {group.preventing > 0 && (
                      <span className="ml-2 text-sev-critical">
                        · {group.preventing} preventing
                      </span>
                    )}
                  </TableCell>
                </TableRow>,
                ...group.items.map((p) => {
                  const sev = (p.severity || "").toUpperCase();
                  return (
                    <TableRow key={p.id}>
                      <TableCell>
                        <div className="text-sm">{p.name}</div>
                        <div className="text-[10px] font-mono text-muted-foreground">
                          {p.id}
                        </div>
                      </TableCell>
                      <TableCell
                        className="max-w-[260px] truncate font-mono text-[11px]"
                        title={p.pattern}
                      >
                        {p.pattern}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {p.category || "—"}
                      </TableCell>
                      <TableCell>
                        <span
                          className={cn(
                            "rounded-sm border px-1.5 py-0.5 text-[10px] font-mono uppercase",
                            SEV_STYLE[sev] ?? "bg-muted text-muted-foreground",
                          )}
                        >
                          {sev || "—"}
                        </span>
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {p.hits.toLocaleString()}
                      </TableCell>
                      <TableCell className="text-xs">
                        {p.last_hit_at ? (
                          <RelativeTime value={p.last_hit_at} />
                        ) : (
                          <span className="text-muted-foreground">never</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <Switch
                          checked={p.enabled}
                          pending={
                            !isAdmin ||
                            (pending?.id === p.id && pending.field === "enabled")
                          }
                          label={p.enabled ? "Disable policy" : "Enable policy"}
                          onToggle={(next) => onToggle(p, next)}
                        />
                      </TableCell>
                      <TableCell className="text-right">
                        <PaidLock
                          locked={!enforcementEnabled}
                          hint="Inline blocking (prevention) is part of enforcement — available in the KYDE Enterprise edition. The sandbox edition detects and alerts only."
                        >
                          <Switch
                            checked={p.prevention}
                            pending={
                              !isAdmin ||
                              (pending?.id === p.id &&
                                pending.field === "prevention")
                            }
                            label={
                              p.prevention
                                ? "Disable prevention for this policy"
                                : "Enable prevention for this policy"
                            }
                            onColor="red"
                            onToggle={(next) => onTogglePrevention(p, next)}
                          />
                        </PaidLock>
                      </TableCell>
                    </TableRow>
                  );
                }),
              ])
            )}
          </TableBody>
        </Table>
      </div>
    </>
  );
}
