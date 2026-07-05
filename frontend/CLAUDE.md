# Frontend

## Gotchas

- No FE build tooling available: `frontend/node_modules` is absent and the `metalcrow-litsearch-frontend` Docker image is a prebuilt nginx-static image (no `node`/`npm` binary) — typecheck must happen via `bun run build` locally in dev, not in Docker.
- `LoadingButton` (`src/components/ui/loading-button.tsx`) already sets `disabled={loading || disabled}` internally — don't re-derive that in callers.
- Tailwind v4 `line-clamp-*` utilities work with no extra plugin in this repo's setup.
- `bunx biome check --write` aggressively reflows large inline JSON fixtures and multi-line `page.route()`/`.fill()` chains in Playwright specs — run it on new spec files before considering them done; `bun run build` alone won't catch formatting-only violations.
- `bun run build` = `tsc -p tsconfig.build.json && vite build` — a clean, fast typecheck+build gate, but it does NOT run biome/lint.
- Backend links a chat message to its literature search only via `message.message_metadata.search_id` (+ `litsearch_kind`), no DB FK — read this off message history rather than expecting a dedicated response field.
- `ChatMessageResponse.mode_used` is plain `str` on the backend, not an enum — the hand-written `ChatModeUsed` union (`src/lib/postChatMessage.ts`) is the only place enforcing the closed set of modes; keep it in sync manually when new modes ship on the backend.
- `frontend/tests/` had zero `page.route()` network-mocking precedent before the litsearch specs — all prior specs hit a real backend via `auth.setup.ts`; `chat-literature.spec.ts`/`literature-panel.spec.ts` are the first mocked ones.
