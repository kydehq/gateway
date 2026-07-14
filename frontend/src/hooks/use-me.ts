import { useMe as useMeQuery } from "@/api/queries";

// Role helper. useMe() returns TanStack Query result; role checks are
// convenience booleans that treat a still-loading me as "not admin" so
// nothing admin-only flashes pre-auth.
export function useMe() {
  const q = useMeQuery();
  const roles = q.data?.roles ?? [];
  return {
    ...q,
    me: q.data,
    isAdmin: roles.includes("admin"),
    isAuditor: roles.includes("auditor"),
    roles,
  };
}
