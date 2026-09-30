.PHONY: build up down logs shell check

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f app

shell:
	docker compose run --rm app sh

check:
	python -m py_compile main.py core.py qnhctl.py
