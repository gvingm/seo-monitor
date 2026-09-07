# syntax=docker/dockerfile:1.6
# ── Build stage ──────────────────────────────────────────────
# python:3.11-slim to match runtime distroless python3.11 ABI
FROM python:3.11-slim AS build

WORKDIR /app

# Кэшируем pip
COPY requirements.txt .
RUN pip install --no-cache-dir --target=/deps -r requirements.txt

# Pre-create creds dir in build stage (distroless has no shell)
RUN mkdir -p /creds && chmod 755 /creds

# ── Runtime stage ──────────────────────────────────────────
# gcr.io/distroless/python3-debian12:nonroot ships python3.11
FROM gcr.io/distroless/python3-debian12:nonroot

# timezone data
COPY --from=build /usr/share/zoneinfo /usr/share/zoneinfo
ENV TZ=Europe/Moscow
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Tell Python where to find the third-party packages we built
ENV PYTHONPATH=/deps

WORKDIR /app

# copy installed packages and app code
COPY --from=build /deps /deps
COPY config.py .
COPY db.py .
COPY yandex_api.py .
COPY google_api.py .
COPY alerts.py .
COPY main.py .

# credentials directory (mount as volume, pre-created in build stage)
COPY --from=build --chown=nonroot:nonroot /creds /app/creds

# nonroot user is already set by distroless image
USER nonroot

EXPOSE 8788

# distroless python3-debian12 has python3.11 as default entrypoint
# just pass the script as CMD — the image's ENTRYPOINT is the python interpreter
CMD ["main.py"]
