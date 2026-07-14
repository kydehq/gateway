import { useState } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { TableHead } from "@/components/ui/table";
import { cn } from "@/lib/utils";

export type SortDir = "asc" | "desc";
export interface SortState<K extends string> {
  key: K;
  dir: SortDir;
}

export function useSort<K extends string>(initial: SortState<K>) {
  const [sort, setSort] = useState<SortState<K>>(initial);
  const toggle = (key: K) => {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key, dir: "asc" },
    );
  };
  return { sort, toggle };
}

export function SortableTh<K extends string>({
  sortKey,
  sort,
  toggle,
  children,
  className,
}: {
  sortKey: K;
  sort: SortState<K>;
  toggle: (k: K) => void;
  children: React.ReactNode;
  className?: string;
}) {
  const active = sort.key === sortKey;
  const Icon = !active ? ChevronsUpDown : sort.dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <TableHead className={className}>
      <button
        type="button"
        onClick={() => toggle(sortKey)}
        className={cn(
          "inline-flex items-center gap-1 text-left",
          active ? "text-foreground" : "hover:text-foreground",
        )}
      >
        {children}
        <Icon className="h-3 w-3 opacity-70" />
      </button>
    </TableHead>
  );
}
