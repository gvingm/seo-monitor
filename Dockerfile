# syntax=docker/dockerfile:1.6
# ── Build stage ──────────────────────────────────────────────
FROM python:3.13-slim AS build

WORKDIR /app

# Кэшируем pip
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ──────────────────────────────────────────
FROM gcr.io/distroless/python3-debian12:nonroot

# timezone data
COPY --from=build /usr/share/zoneinfo /usr/share/zoneinfo
ENV TZ=Europe/Moscow
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# copy installed packages
COPY --from=build /install /usr/local

# copy app code
COPY config.py .
COPY db.py .
COPY yandex_api.py .
COPY google_api.py .
COPY alerts.py .
COPY main.py .

# credentials directory (mount as volume)
RUN mkdir -p /app/creds && chmod 755 /app/creds

# nonroot user is already set by distroless image
USER nonroot

EXPOSE 8788

CMD ["python", "main.py"]
