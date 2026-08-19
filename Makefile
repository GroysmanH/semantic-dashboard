.PHONY: up down reset logs seed test eval eval-anthropic models types psql validate-all

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

# Defaults to Gemini: 30 questions x 3 models x 2 arms is the one thing
# here that runs often enough for "free" to beat "familiar".
eval:
	docker compose exec -T backend python -m eval.run_eval

eval-anthropic:
	docker compose exec -T backend python -m eval.run_eval --provider anthropic

# Which model ids each configured API will actually accept.
models:
	docker compose exec -T backend python -m app.cli models

types:
	docker compose exec -T backend python /scripts/gen_types.py
	docker compose exec -T frontend npx json2ts -i src/api/schema.json -o src/api/types.gen.ts

validate-all:
	docker compose exec -T backend python -m app.cli validate-all

psql:
	docker compose exec db psql -U postgres -d semantic
