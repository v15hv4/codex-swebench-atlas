.PHONY: build shell down

build:
	docker compose build

shell:
	docker compose exec bench bash

down:
	docker compose down
