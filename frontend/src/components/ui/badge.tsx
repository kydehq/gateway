import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

// Base class: the original shadcn sizing for the default/secondary/destructive
// variants. The `tag-*` variants override sizing/rounding/weight via later
// utilities (twMerge picks the last conflicting class per property) — those
// are the tight, mono-cased status chips used in the sidebar (TPM/SOFTWARE,
// Data Integrity, etc.). Keep them together so new status chips have a
// single place to land.
const badgeVariants = cva(
  "inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
  {
    variants: {
      variant: {
        default:
          "border-transparent bg-primary text-primary-foreground hover:bg-primary/80",
        secondary:
          "border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80",
        destructive:
          "border-transparent bg-destructive text-destructive-foreground hover:bg-destructive/80",
        outline: "text-foreground",

        // ── Kyde status chips (Editorial Mono) ───────────────────────
        // Flat tinted pills (spec §6.6): light tint surface + colored
        // text, NOT solid fills. Tight 5px box, 11px mono uppercase.
        // They differ only in the color pair; override happens via later
        // utilities — twMerge picks the winning class per property.
        "tag-success":
          "gap-1 border-transparent rounded-[5px] px-2 py-0.5 text-[11px] font-mono font-semibold uppercase tracking-[0.06em] bg-success/10 text-success",
        "tag-warning":
          "gap-1 border-transparent rounded-[5px] px-2 py-0.5 text-[11px] font-mono font-semibold uppercase tracking-[0.06em] bg-warning/10 text-warning",
        "tag-info":
          "gap-1 border-transparent rounded-[5px] px-2 py-0.5 text-[11px] font-mono font-semibold uppercase tracking-[0.06em] bg-primary/10 text-primary",
        "tag-destructive":
          "gap-1 border-transparent rounded-[5px] px-2 py-0.5 text-[11px] font-mono font-semibold uppercase tracking-[0.06em] bg-destructive/10 text-destructive",
        "tag-muted":
          "gap-1 border-transparent rounded-[5px] px-2 py-0.5 text-[11px] font-mono font-semibold uppercase tracking-[0.06em] bg-secondary text-muted-foreground",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  )
}

export { Badge, badgeVariants }
