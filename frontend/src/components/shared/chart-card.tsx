import { Card, CardContent } from "@/components/ui/card";
import type { ReactNode } from "react";

export function ChartCard({
  title,
  subtitle,
  children,
  height = 240,
}: {
  title: string;
  /** Optional secondary line under the title — for annotations like
   *  outlier counts that contextualise the chart without earning a KPI slot. */
  subtitle?: ReactNode;
  children: ReactNode;
  height?: number;
}) {
  return (
    <Card>
      <CardContent className="p-[22px]">
        <div>
          <span className="eyebrow-chip">{title}</span>
        </div>
        {subtitle ? (
          <div className="mt-2 text-[11px] text-muted-foreground">{subtitle}</div>
        ) : null}
        <div style={{ height }} className="relative mt-[18px]">
          {children}
        </div>
      </CardContent>
    </Card>
  );
}
