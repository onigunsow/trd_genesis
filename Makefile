.PHONY: redeploy logs ps stop help

# @MX:NOTE: SPEC-TRADING-016 REQ-016-1-2 — single entry point for container redeploy.
# Forces fresh build with current git HEAD as BUILD_COMMIT, then recreates services.
# Healthcheck (check_build_commit) verifies HOST_BUILD_COMMIT == /app/.build_commit.

help:
	@echo "Trading project Makefile targets:"
	@echo "  make redeploy   - Rebuild app image with current git commit and restart services"
	@echo "  make logs       - Tail scheduler logs (Ctrl+C to exit)"
	@echo "  make ps         - Show container status"
	@echo "  make stop       - Stop scheduler/bot/app containers (postgres stays up)"

# Single-command rebuild + restart with current git HEAD as BUILD_COMMIT.
# scheduler/bot reuse the trading-app:latest image built by the `app` service,
# so we rebuild `app` once and force-recreate all three.
redeploy:
	@COMMIT=$$(git rev-parse HEAD); \
	if [ -z "$$COMMIT" ]; then echo "Error: not a git repo or no commit"; exit 1; fi; \
	echo "Building with commit=$$COMMIT"; \
	HOST_BUILD_COMMIT=$$COMMIT docker compose build --no-cache --build-arg BUILD_COMMIT=$$COMMIT app; \
	HOST_BUILD_COMMIT=$$COMMIT docker compose up -d --force-recreate scheduler bot app; \
	docker compose ps; \
	echo ""; \
	echo "Tail logs: make logs"
	@echo ""

logs:
	docker compose logs -f scheduler

ps:
	docker compose ps

stop:
	docker compose stop scheduler bot app
