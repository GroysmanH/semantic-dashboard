.PHONY: up down reset logs seed test eval types psql validate-all

up:
	docker compose up -d --build
	@echo "backend  -> http://localhost:8000/health"
	@echo "frontend -> http://localhost:5173"

down:
	docker compose down

reset:
	docker compose down -v
	docker compose up -d --build

logs:
	docker compose logs -f --tail=100

seed:
	docker compose exec -T backend python /db/seed/seed.py

test:
	docker compose exec -T backend pytest -v

eval:
	docker compose exec -T backend python -m eval.run_eval

types:
	docker compose exec -T backend python /scripts/gen_types.py
	docker compose exec -T frontend npx json2ts -i src/api/schema.json -o src/api/types.gen.ts

validate-all:
	docker compose exec -T backend python -m app.cli validate-all

psql:
	docker compose exec db psql -U postgres -d semantic
