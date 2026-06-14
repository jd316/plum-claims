# Frontend — Plum Claims UI

React + TypeScript + Vite single-page app for claim submission and decision review.

## What's here

- **Submit** (`src/pages/Submit.tsx`) — a mobile-first wizard: member + treatment details,
  per-category document drop-zones that classify each file *as you upload it* (shift-left
  document verification), then submit.
- **Claim review** (`src/pages/Claim.tsx`) — verdict, financial breakdown, ranked reason
  codes, confidence bars, the full expandable trace timeline, a split-screen document viewer,
  decision replay, and the ops inline field-correction / re-decide panel.
- **Claims list** (`src/pages/Claims.tsx`), **Eval** (`src/pages/Eval.tsx`, run all 12 cases),
  and the ops pages (worklist, dashboard, fraud, policy studio).

Theme + brand live in `src/theme.tsx`, `src/index.css`, and `tailwind.config.js`, and follow
[`plum_design_system.md`](plum_design_system.md) — the Plum brand reference (palette,
typography, feel) this UI implements.

## Develop

```bash
npm install
npm run dev      # Vite dev server on :5173, proxies /api → http://localhost:8000
npm run build    # tsc -b && vite build  → dist/
npm run lint     # eslint
```

The backend must be running on port 8000 for the dev proxy. For the full Docker stack (nginx
serves this build and reverse-proxies `/api`), see the root [`README.md`](../README.md).
