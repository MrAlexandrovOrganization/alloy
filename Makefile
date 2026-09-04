DOCKER_COMPOSE = docker compose
DOCKER ?= docker
NET = loki-net
CONFIG ?= config.alloy

-include .env
export

up: init
	$(DOCKER_COMPOSE) up -d

init: network

network:
	@if $(DOCKER) network inspect $(NET) >/dev/null 2>&1; then \
		echo "network $(NET) already exists"; \
	else \
		$(DOCKER) network create $(NET) --opt com.docker.network.driver.mtu=1376; \
	fi

down:
	$(DOCKER_COMPOSE) down

logs:
	$(DOCKER_COMPOSE) logs -f

fmt:
	@set -eu; \
	image=$$($(DOCKER_COMPOSE) config --images alloy); \
	$(DOCKER) run --rm \
		-v "$(abspath $(CONFIG)):/work/config.alloy" \
		"$$image" fmt --write /work/config.alloy

fmt-check:
	@set -eu; \
	image=$$($(DOCKER_COMPOSE) config --images alloy); \
	formatted=$$(mktemp); \
	trap 'rm -f "$$formatted"' EXIT HUP INT TERM; \
	$(DOCKER) run --rm -i "$$image" fmt - \
		< "$(CONFIG)" > "$$formatted"; \
	diff -u "$(CONFIG)" "$$formatted"

install-hooks:
	@set -eu; \
	hooks=$$(git rev-parse --git-path hooks); \
	if [ -e "$$hooks/pre-commit" ] || [ -L "$$hooks/pre-commit" ]; then \
		echo "A pre-commit hook already exists; refusing to overwrite it." >&2; \
		exit 1; \
	fi; \
	mkdir -p "$$hooks"; \
	install -m 755 .githooks/pre-commit "$$hooks/pre-commit"

.PHONY: up down logs init network fmt fmt-check install-hooks
