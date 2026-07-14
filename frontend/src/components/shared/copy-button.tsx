import { Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

export function CopyButton({ value, label }: { value: string; label?: string }) {
  return (
    <Button
      size="icon"
      variant="ghost"
      className="h-6 w-6 shrink-0"
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(value);
        toast.success(label ? `Copied ${label}` : "Copied");
      }}
      aria-label={label ? `Copy ${label}` : "Copy"}
    >
      <Copy className="h-3 w-3" />
    </Button>
  );
}
