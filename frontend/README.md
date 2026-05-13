# Frontend (PoC)

This is a minimal React + Vite frontend for the Train Simulation PoC.

Commands:

- npm install
- npm run dev

The frontend expects the backend FastAPI server to be available at the same host (http://localhost:8000).

Testing
-------

UI tests use `vitest` and `@testing-library/react`.

Install dev deps and run tests:

```bash
cd frontend
npm install
npm test
```

The `TrainEditor` component now includes a simple presets feature persisted to `localStorage` (key: `train_presets_v1`).
