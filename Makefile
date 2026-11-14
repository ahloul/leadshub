.PHONY: dev test test-email migrate check install collectstatic css css-watch messages storage-up docker-up docker-build docker-migrate docker-logs docker-test docker-down fly-deploy

# The project's own environment, so no activation is needed. An exported
# PYTHON variable does not override this; `make dev PYTHON=python` does.
PYTHON = .venv/bin/python

dev: css
	$(PYTHON) -m uvicorn leadshub.asgi:application --host 127.0.0.1 --port 8000 --reload

test:
	$(PYTHON) manage.py test

# Send one email through the configured server (Mailtrap in development, see
# .env.example): make test-email TO=you@example.com
test-email:
	@test -n "$(TO)" || { echo "Usage: make test-email TO=you@example.com"; exit 1; }
	$(PYTHON) manage.py sendtestemail $(TO)

migrate:
	$(PYTHON) manage.py migrate

check:
	$(PYTHON) manage.py djust_check

install:
	uv pip install --python .venv -r requirements.txt
	npm ci

collectstatic: css
	$(PYTHON) manage.py collectstatic --noinput

# Tailwind CSS: static/css/input.css -> static/css/output.css
css:
	npm run css

css-watch:
	npm run css:watch

# Extract translatable strings into locale/ar, then compile them.
messages:
	$(PYTHON) manage.py makemessages -l ar --ignore=.venv --ignore=node_modules
	$(PYTHON) manage.py compilemessages --ignore=.venv

# Local S3-compatible storage for uploads (RustFS in Docker), for `make dev`.
# Needs the AWS_* variables from .env.example in .env.
storage-up:
	docker compose up -d storage
	$(PYTHON) manage.py ensure_storage_bucket

# Docker environment (see docker-compose.yml). The source is mounted, so
# code changes need no rebuild; the image is built only if it is missing.
docker-up:
	docker compose up -d --wait db
	docker compose up -d storage
	docker compose run --rm web python manage.py migrate --noinput
	docker compose run --rm web python manage.py ensure_storage_bucket
	docker compose run --rm web python manage.py seed_demo
	docker compose up -d web
	@echo "Open http://localhost:8000 (storage console: http://localhost:9001)"

# Only needed after requirements.txt or the Dockerfile change.
docker-build:
	docker compose build web test

# After pulling or adding migrations.
docker-migrate:
	docker compose run --rm web python manage.py migrate --noinput

docker-logs:
	docker compose logs -f web css

docker-test:
	docker compose --profile test run --rm test

docker-down:
	docker compose down

# Fly.io (docs/DEPLOY.md). A first deploy creates one machine; add more with
# `fly scale count` once REDIS_URL is set (djust's page state and the channel
# layer are in memory without it). Later deploys keep the machine count.
fly-deploy:
	fly deploy --ha=false
