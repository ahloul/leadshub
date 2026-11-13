.PHONY: dev test migrate check install collectstatic css css-watch messages docker-up docker-test docker-down \
	docker-dev-up docker-dev-logs docker-dev-test docker-dev-shell docker-dev-down docker-dev-clean

# The project's own environment, so no activation is needed. An exported
# PYTHON variable does not override this; `make dev PYTHON=python` does.
PYTHON = .venv/bin/python

dev: css
	$(PYTHON) -m uvicorn leadshub.asgi:application --host 127.0.0.1 --port 8000 --reload

test:
	$(PYTHON) manage.py test

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

# Docker test environment (see docker-compose.yml).
docker-up:
	docker compose build web
	docker compose up -d --wait db
	docker compose run --rm web python manage.py migrate --noinput
	docker compose run --rm web python manage.py seed_demo
	docker compose up -d web
	@echo "Open http://localhost:8000"

docker-test:
	docker compose --profile test run --rm --build test

docker-down:
	docker compose down

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
