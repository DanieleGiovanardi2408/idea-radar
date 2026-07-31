# Idea Radar — frontend

Interfaccia web del radar: Vite + React 19 + TypeScript, Tailwind CSS v4, React Router, TanStack Query. Il design system ("radar room") vive in `src/index.css`. Per la panoramica del progetto vedi il [README principale](../README.md).

## Comandi

```bash
npm install        # prima volta
npm run dev        # http://localhost:5173, proxy verso il backend su :8000
npm test           # vitest, una passata
npm run test:watch # vitest in watch mode
npm run lint       # oxlint
npm run typecheck  # tsc -b
npm run build      # typecheck + build di produzione
```

In dev il backend è raggiunto tramite il proxy Vite (`vite.config.ts`): niente CORS. Se il backend è su un'altra porta: `BACKEND_URL=http://localhost:8001 npm run dev`. Ogni rotta API nuova va aggiunta alla lista del proxy in `vite.config.ts`.

## Struttura

```
src/
  App.tsx          # shell: header, nav su URL, drawer con deep-link ?idea=<id>
  api.ts           # client tipato verso il backend
  types.ts         # tipi condivisi delle risposte API
  hooks/           # useRadarData (TanStack Query), useFocusTrap
  components/      # RadarScope, IdeaCard, IdeaDetail, Nav, ui.tsx, motion.tsx
  views/           # Radar, Topics, Trends, Monitor
  index.css        # tema Tailwind v4 + design system
```

I test stanno accanto ai file che verificano (`*.test.tsx`); setup condiviso in `src/test/`.
