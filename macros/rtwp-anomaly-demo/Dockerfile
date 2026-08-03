FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY macro.py .

RUN useradd --uid 1000 --create-home macro
USER 1000

ENTRYPOINT ["python", "macro.py"]
