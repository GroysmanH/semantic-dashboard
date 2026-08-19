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

# Anthropic by default. Gemini's free tier meters at 20 requests per day
# per model and a full sweep is 360 calls; on Haiku it costs about $0.30.
eval:
	docker compose exec -T backend python -m eval.run_eval

eval-gemini:
	docker compose exec -T backend python -m eval.run_eval --provider gemini

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
