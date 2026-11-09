.PHONY: dev test migrate check install collectstatic css css-watch messages docker-up docker-test docker-down

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
