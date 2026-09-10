.PHONY: up down reset logs seed seed-realistic test test-realistic benchmark-realistic eval eval-anthropic eval-chat models types psql validate-all

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

# Explicitly opt-in and isolated from both the browser's semantic database and
# the ordinary suite's semantic_test database. The provisioner is idempotent
# for a complete profile and refuses to truncate any populated database.
REALISTIC_DB = semantic_realistic
REALISTIC_DB_URLS = \
	ADMIN_URL=postgresql://postgres:$${POSTGRES_PASSWORD:-postgres}@db:5432/$(REALISTIC_DB) \
	APP_URL=postgresql://app_rw:$${APP_PASSWORD:-appsecret}@db:5432/$(REALISTIC_DB) \
	WAREHOUSE_URL=postgresql://warehouse_ro:$${WAREHOUSE_PASSWORD:-warehouse}@db:5432/$(REALISTIC_DB)

seed-realistic:
	docker compose exec -T backend python /scripts/ensure_realistic_db.py \
		--confirm "$(CONFIRM)" $(if $(END_DATE),--end-date "$(END_DATE)",)

test-realistic: seed-realistic
	docker compose exec -T backend env $(REALISTIC_DB_URLS) \
		SEED_PROFILE=realistic pytest -v tests/realistic/test_realistic_profile.py -s

benchmark-realistic: test-realistic

# A database of its own, bootstrapped on first use and cheap thereafter.
# The suite deletes every board it finds afterwards and cannot tell one a
# test made from one a person made, so sharing a database with the browser
# meant `make test` could remove a dashboard somebody was building.
# Deployment settings are pinned here too, not just the URLs. `.env` is
# where a real deployment is configured -- a production warehouse, one
# visible schema -- and env_file hands all of it to the container. A suite
# that inherited those would test the operator's configuration instead of
# the code, and would start failing the moment somebody deployed.
TEST_DB_URLS = \
	ADMIN_URL=postgresql://postgres:$${POSTGRES_PASSWORD:-postgres}@db:5432/semantic_test \
	APP_URL=postgresql://app_rw:$${APP_PASSWORD:-appsecret}@db:5432/semantic_test \
	WAREHOUSE_URL=postgresql://warehouse_ro:$${WAREHOUSE_PASSWORD:-warehouse}@db:5432/semantic_test \
	DEFAULT_SCHEMA=ddh \
	VISIBLE_SCHEMAS=

test:
	docker compose exec -T backend python /scripts/ensure_test_db.py
	docker compose exec -T -e ADMIN_URL -e APP_URL -e WAREHOUSE_URL backend \
		env $(TEST_DB_URLS) pytest -v

# Anthropic by default. Gemini's free tier meters at 20 requests per day
# per model and a full sweep is 360 calls; on Haiku it costs about $0.30.
# Evals build and tear down real boards, so they run against the test
# database too rather than leaving debris among somebody's dashboards.
eval:
	docker compose exec -T backend python /scripts/ensure_test_db.py
	docker compose exec -T backend env $(TEST_DB_URLS) python -m eval.run_eval

eval-chat:
	docker compose exec -T backend python /scripts/ensure_test_db.py
	docker compose exec -T backend env $(TEST_DB_URLS) python -m eval.run_chat_eval

eval-gemini:
	docker compose exec -T backend python /scripts/ensure_test_db.py
	docker compose exec -T backend env $(TEST_DB_URLS) \
		python -m eval.run_eval --provider gemini


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
