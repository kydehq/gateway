# Typed API client

`client.ts` — a thin `fetchJSON<T>()` over `window.fetch`. Use it from new
TypeScript modules when you want the response type checked.

`schema.d.ts` — generated from the backend's `/openapi.json` by running
`npm run openapi:sync` against a live dashboard. Commit the result so
frontend builds don't need a running backend.

## First-time setup

```bash
# Start the stack (gateway + dashboard + postgres)
docker compose up -d

# Inside frontend/
npm run openapi:sync
```

Re-run `npm run openapi:sync` whenever an `/api/*` route is added, removed,
or its response shape changes.

## Example usage

```ts
import type { paths } from "./api/schema";
import { fetchJSON, qs } from "./api/client";

type EntriesResponse =
  paths["/api/entries"]["get"]["responses"]["200"]["content"]["application/json"];

const page = await fetchJSON<EntriesResponse>(
  "/api/entries" + qs({ limit: 50, cursor: cursor ?? null })
);
```
