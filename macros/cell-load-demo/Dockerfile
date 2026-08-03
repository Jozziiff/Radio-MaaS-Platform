# cell-load-demo (M1): hand-written Dockerfile.
# Later milestones replace this with an auto-generated Dockerfile from the
# AST engine — for M1 it is written by hand on purpose.

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY macro.py .

# Run as a non-root user (uid 1000) rather than the container's default root,
# so a compromised or buggy macro can't write outside its own mounted paths.
RUN useradd --uid 1000 --create-home macro
USER 1000

ENTRYPOINT ["python", "macro.py"]
