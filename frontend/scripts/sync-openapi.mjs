#!/usr/bin/env node
// Regenerate `src/api/schema.d.ts` from the running FastAPI app.
//
// Usage:   npm run openapi:sync [--url http://host:port]
// Default: http://localhost:8501/openapi.json
//
// The generated file is checked into git so the frontend can build without
// a running backend. Re-run this script whenever you add or change a
// Python route under /api/*.

import { execSync } from "node:child_process";
import { argv } from "node:process";
import { mkdirSync } from "node:fs";

const urlArgIndex = argv.indexOf("--url");
const url = urlArgIndex >= 0 ? argv[urlArgIndex + 1] : "http://localhost:8501/openapi.json";
const outPath = "src/api/schema.d.ts";

mkdirSync("src/api", { recursive: true });

console.log(`Fetching schema from ${url}`);
try {
  execSync(`npx openapi-typescript "${url}" -o "${outPath}"`, {
    stdio: "inherit",
  });
  console.log(`\nWrote ${outPath}. Commit it alongside the API change.`);
} catch (err) {
  console.error(
    `\nFailed to fetch or convert schema.\n` +
      `  Is the dashboard reachable at ${url}?\n` +
      `  Start the stack with: docker compose up -d\n`,
  );
  process.exit(1);
}
