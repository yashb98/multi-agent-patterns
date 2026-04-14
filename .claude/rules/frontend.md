# Rules: Frontend (frontend/**/*)

## Stack
React + Three.js for 3D neural/galaxy visualization.
npm run dev starts on localhost:3000.

## Rules
- No direct API calls in components — use api/ client layer
- Three.js scenes must dispose geometries/materials on unmount to prevent memory leaks
- All data fetched from FastAPI backend at localhost:8000
- Simplicity first — no abstractions for single-use components, no speculative configurability
- Surgical changes — match existing component style, don't refactor adjacent code
- Test UI in browser before reporting complete — type checking verifies correctness, not feature behavior
