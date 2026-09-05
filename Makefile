DOCKER_COMPOSE ?= docker compose
DOCKER ?= docker
PRE_COMMIT ?= pre-commit
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

fmt fmt-check:
	@set -eu; \
	image=$$($(DOCKER_COMPOSE) config --images alloy); \
	formatted=$$(mktemp); \
	trap 'rm -f "$$formatted"' EXIT; \
	trap 'exit 1' HUP INT TERM; \
	$(DOCKER) run --rm -i "$$image" fmt - \
		< "$(CONFIG)" > "$$formatted"; \
	if [ "$@" = fmt ]; then \
		if ! cmp -s "$(CONFIG)" "$$formatted"; then \
			cat "$$formatted" > "$(CONFIG)"; \
		fi; \
	else \
		diff -u "$(CONFIG)" "$$formatted"; \
	fi

config-check:
	$(DOCKER_COMPOSE) config -q

check: fmt-check config-check

install-hooks:
	@sh scripts/install-hooks.sh install

migrate-hooks:
	@sh scripts/install-hooks.sh migrate

.PHONY: up down logs init network fmt fmt-check config-check check install-hooks migrate-hooks
