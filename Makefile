.PHONY: dev test jobs test-email migrate check install collectstatic css css-watch messages storage-up docker-up docker-test docker-down \
	docker-dev-up docker-dev-logs docker-dev-test docker-dev-shell docker-dev-down docker-dev-clean fly-deploy docker-build docker-migrate docker-logs docker-test docker-down

# The project's own environment, so no activation is needed. An exported
# PYTHON variable does not override this; `make dev PYTHON=python` does.
PYTHON = .venv/bin/python

dev: css
	$(PYTHON) -m uvicorn leadshub.asgi:application --host 127.0.0.1 --port 8000 --reload

# The task queue and scheduled jobs (django-q2, leadshub/jobs.py). With
# DEBUG on, queued tasks such as emails run at once without it (Q_SYNC);
# run it beside `make dev` for the schedules (the review SLA escalation),
# or with Q_SYNC=0 to try the real queue. On Fly it is the "jobs" process.
jobs:
	$(PYTHON) manage.py qcluster

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
# code changes need no rebuild. The images are rebuilt by themselves when
# requirements.txt or the Dockerfile is newer than the last build
# (.docker-images marks it).
DOCKER_IMAGES = .docker-images

$(DOCKER_IMAGES): requirements.txt Dockerfile
	docker compose --profile test build web jobs test
	touch $(DOCKER_IMAGES)

docker-up: $(DOCKER_IMAGES)
	docker compose up -d --wait db
	docker compose up -d storage
	docker compose run --rm web python manage.py migrate --noinput
	docker compose run --rm web python manage.py ensure_storage_bucket
	docker compose run --rm web python manage.py seed_demo
	docker compose up -d web jobs
	@echo "Open http://localhost:8000 (storage console: http://localhost:9001)"

# Rebuild now (docker-up and docker-test already do when needed).
docker-build:
	docker compose --profile test build web jobs test
	touch $(DOCKER_IMAGES)

# After pulling or adding migrations.
docker-migrate: $(DOCKER_IMAGES)
	docker compose run --rm web python manage.py migrate --noinput

docker-logs:
	docker compose logs -f web jobs css

docker-test: $(DOCKER_IMAGES)
	docker compose --profile test run --rm test

docker-down:
	docker compose down

# Fly.io (docs/DEPLOY.md). A first deploy creates one machine; add more with
# `fly scale count` once REDIS_URL is set (djust's page state and the channel
# layer are in memory without it). Later deploys keep the machine count.
fly-deploy:
	fly deploy --ha=false

# Docker development environment (see docker-compose-dev.yml): your checkout
# is mounted into the containers, so edits on the host reload the app and
# rebuild the CSS. Its own project, database and DB port (5433).
DEV_COMPOSE = docker compose -f docker-compose-dev.yml

docker-dev-up:
	$(DEV_COMPOSE) build
	$(DEV_COMPOSE) up -d --wait db
	$(DEV_COMPOSE) run --rm web python manage.py migrate --noinput
	$(DEV_COMPOSE) run --rm web python manage.py seed_demo
	$(DEV_COMPOSE) up -d -V web css
	@echo "Open http://localhost:8000 (logs: make docker-dev-logs)"

docker-dev-logs:
	$(DEV_COMPOSE) logs -f web css

docker-dev-test:
	$(DEV_COMPOSE) --profile test run --rm test

# A shell in the app container, e.g. for makemigrations or manage.py shell.
docker-dev-shell:
	$(DEV_COMPOSE) exec web bash

docker-dev-down:
	$(DEV_COMPOSE) down

# Also deletes the development database.
docker-dev-clean:
	$(DEV_COMPOSE) down -v
