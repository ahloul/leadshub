# syntax=docker/dockerfile:1

# --- Stage 1: build Tailwind CSS -------------------------------------------
FROM node:22-slim AS css
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
# Tailwind scans these for class names (see static/css/input.css @source).
COPY static ./static
COPY leadshub/templates ./leadshub/templates
COPY accounts/templates ./accounts/templates
COPY templates ./templates
RUN npm run css

# --- Stage 2: the app -------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# gettext compiles the Arabic translations.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
COPY --from=css /app/static/css/output.css ./static/css/output.css
# Build steps load the settings but need no real secret, so they get a
# throwaway key (production refuses to start without SECRET_KEY).
# collectstatic fills /app/staticfiles, which Fly serves directly
# ([[statics]] in fly.toml).
RUN SECRET_KEY=build-only python manage.py compilemessages --ignore=.venv --ignore=node_modules \
    && SECRET_KEY=build-only python manage.py collectstatic --noinput

RUN useradd --create-home app && chown -R app /app
USER app

EXPOSE 8000
# Migrations run once per deploy (release_command in fly.toml; `make
# docker-up` locally), not on every start. --proxy-headers takes the client
# IP from Fly's proxy, which axes needs to lock out the right address.
# One process per machine; for more, add machines with REDIS_URL set
# (docs/DEPLOY.md), which shares djust's page state and the channel layer.
CMD ["uvicorn", "leadshub.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
