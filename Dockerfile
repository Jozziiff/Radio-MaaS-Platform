# M7 (production hardening): collapses the frontend into backend-api's own
# image -- one Deployment, one Service, same-origin (no CORS needed). See
# docs/superpowers/specs/2026-08-31-external-network-reachability-design.md.
#
# Two stages: the first builds the React app and is discarded entirely --
# Node/npm/node_modules never reach the final image, only the built dist/
# output does. This is why the build context is the repo root now (it
# needs both services/frontend/ and services/backend-api/), not
# services/backend-api/ alone as it was before this file existed.
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY services/frontend/package.json services/frontend/package-lock.json ./
RUN npm ci
COPY services/frontend/ ./
RUN npm run build

# The real runtime image -- same shape as the old services/backend-api/
# Dockerfile (python:3.11-slim + pip install + app code), plus one extra
# COPY --from= pulling in the frontend stage's build output as static/,
# which main.py's STATIC_DIR constant expects at exactly this path.
FROM python:3.11-slim

WORKDIR /app

COPY services/backend-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/backend-api/ .
COPY --from=frontend-build /frontend/dist ./static

# No --reload on the entrypoint: that flag watches the filesystem for
# source changes, meant for local dev only -- wrong, and wasteful, in a
# built, immutable image. Local dev keeps using `uvicorn main:app
# --reload` directly (services/backend-api/, unaffected by this file) and
# `npm run dev` (services/frontend/, via vite.config.js's dev-server
# proxy -- see Task 1), neither of which uses this Dockerfile at all.
ENTRYPOINT ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
