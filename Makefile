DOCKER_COMPOSE ?= docker compose
DOCKER ?= docker
PRE_COMMIT ?= pre-commit
NETS = loki-net prometheus-net caddy-metrics-net
CONFIG ?= config.alloy
TEST_PATTERN ?= test*.py

-include .env
ALLOY_HOST ?= $(shell hostname)
MONITORING_DATA_DIR ?= $(HOME)/.monitoring
export
include makefiles/versions.mk

up: prepare-collectors
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

queue-init:
	$(DOCKER) volume create observability_vmagent_data >/dev/null
	$(DOCKER) run --rm --network none --read-only --user 0 --cap-drop ALL --cap-add CHOWN --security-opt no-new-privileges --entrypoint sh -v observability_vmagent_data:/queue victoriametrics/vmagent:$(VMAGENT_VERSION) -c 'chown 65534:65534 /queue'

prepare-collectors: init queue-init
	$(DOCKER_COMPOSE) up -d --wait --wait-timeout 90 vmagent
	python3 scripts/migrate-queue.py
	$(MAKE) migrate-state

down:
	$(DOCKER_COMPOSE) down

logs:
	$(DOCKER_COMPOSE) logs -f

format format-check:
	@set -eu; \
	image=grafana/alloy:$(ALLOY_VERSION); \
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
	sh -n scripts/start.sh
	$(DOCKER_COMPOSE) config -q
	$(DOCKER) run --rm --network none -e ALLOY_HOST=validation -e ALLOY_PROCFS_PATH=/proc -e ALLOY_ROOTFS_PATH=/rootfs -e LOKI_PUSH_URL=http://127.0.0.1:3100/loki/api/v1/push -e PROMETHEUS_REMOTE_WRITE_URL=http://127.0.0.1:9090/api/v1/write -i --entrypoint sh grafana/alloy:$(ALLOY_VERSION) -c 'cat > /tmp/check.alloy; /bin/alloy validate /tmp/check.alloy' < "$(CONFIG)"

test:
	python3 -m unittest discover -s tests -p "$(TEST_PATTERN)" -v

smoke:
	python3 scripts/smoke.py

# Explicit live fault injection; never run automatically from CI/deploy/check.
replay-check:
	python3 scripts/check-replay.py

check: format-check config-check test

install-hooks:
	@sh scripts/install-hooks.sh install

migrate-hooks:
	@sh scripts/install-hooks.sh migrate

.PHONY: up down logs init network directories migrate-state queue-init prepare-collectors format format-check config-check test smoke replay-check check install-hooks migrate-hooks

.PHONY: versions recreate
versions:
	@$(foreach v,$(VERSION_VARS),printf '%s=%s\n' '$(v)' '$($(v))';)
recreate: prepare-collectors
	$(DOCKER_COMPOSE) up -d --force-recreate alloy

include makefiles/ci-deploy.mk
