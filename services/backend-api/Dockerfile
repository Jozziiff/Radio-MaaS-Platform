# M7 (production hardening): containerizes backend-api itself, so it can
# run as a real in-cluster Deployment instead of only ever via `uvicorn`
# on a developer's own machine -- see
# docs/decisions/009-backend-api-in-cluster-deployment.md (Task 6 of this
# plan writes the full decision doc).
#
# No --reload on the entrypoint: that flag watches the filesystem for
# source changes, meant for local dev only -- wrong, and wasteful, in a
# built, immutable image. Local dev keeps using `uvicorn main:app
# --reload` directly, unaffected by this file.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENTRYPOINT ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
