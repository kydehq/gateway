import { Navigate } from "react-router-dom";
import { useMe } from "@/hooks/use-me";
import { Skeleton } from "@/components/ui/skeleton";
import type { ReactNode } from "react";

export function RequireAdmin({ children }: { children: ReactNode }) {
  const { isLoading, isAdmin } = useMe();
  if (isLoading) return <Skeleton className="h-8 w-48" />;
  if (!isAdmin) return <Navigate to="/" replace />;
  return <>{children}</>;
}
