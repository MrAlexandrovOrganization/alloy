DOCKER_COMPOSE ?= docker compose
DOCKER ?= docker
PRE_COMMIT ?= pre-commit
NETS = loki-net prometheus-net caddy-metrics-net
CONFIG ?= config.alloy

-include .env
ALLOY_HOST ?= $(shell hostname)
MONITORING_DATA_DIR ?= $(HOME)/.monitoring
export
include makefiles/versions.mk

up: init migrate-state
	$(DOCKER_COMPOSE) up -d

init: network directories

network:
	@for n in $(NETS); do \
		if ! $(DOCKER) network inspect $$n >/dev/null 2>&1; then \
			$(DOCKER) network create $$n --opt com.docker.network.driver.mtu=1376; \
		fi; \
	done

directories:
	@mkdir -p "$(MONITORING_DATA_DIR)/prometheus-targets"

migrate-state:
	python3 scripts/migrate-state.py

down:
	$(DOCKER_COMPOSE) down

logs:
	$(DOCKER_COMPOSE) logs -f

format format-check:
	@set -eu; \
	image=$$($(DOCKER_COMPOSE) config --images alloy); \
	formatted=$$(mktemp); \
	trap 'rm -f "$$formatted"' EXIT; \
	trap 'exit 1' HUP INT TERM; \
	$(DOCKER) run --rm -i "$$image" fmt - \
		< "$(CONFIG)" > "$$formatted"; \
	if [ "$@" = format ]; then \
		if ! cmp -s "$(CONFIG)" "$$formatted"; then \
			cat "$$formatted" > "$(CONFIG)"; \
		fi; \
	else \
		diff -u "$(CONFIG)" "$$formatted"; \
	fi

config-check:
	$(DOCKER_COMPOSE) config -q

test:
	python3 -m unittest discover -s tests -v

smoke:
	python3 scripts/smoke.py

check: format-check config-check test

install-hooks:
	@sh scripts/install-hooks.sh install

migrate-hooks:
	@sh scripts/install-hooks.sh migrate

.PHONY: up down logs init network directories migrate-state format format-check config-check test smoke check install-hooks migrate-hooks

.PHONY: versions recreate
versions:
	@$(foreach v,$(VERSION_VARS),printf '%s=%s\n' '$(v)' '$($(v))';)
recreate: init migrate-state
	$(DOCKER_COMPOSE) up -d --force-recreate alloy
