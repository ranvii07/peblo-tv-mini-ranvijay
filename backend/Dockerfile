FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so code edits do not invalidate the pip layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Seed data lives outside the backend directory in the repo; compose mounts it at
# /data/seed. SEED_DIR points there.
ENV SEED_DIR=/data/seed \
    STORAGE_ROOT=/storage

RUN chmod +x ./entrypoint.sh

EXPOSE 8000
CMD ["./entrypoint.sh"]
