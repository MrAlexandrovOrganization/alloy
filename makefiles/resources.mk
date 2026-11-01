# Explicit host resource tuning; default application lifecycle is unchanged.
.PHONY: resources-check resources-apply
resources-check:
	HEADSCALE_URL=https://headscale.invalid MONITORING_DATA_DIR=/tmp/alloy-compose-check PROMETHEUS_REMOTE_WRITE_URL=http://127.0.0.1:9090/api/v1/write $(DOCKER_COMPOSE_BASE) --env-file /dev/null -f compose/docker-compose.yml -f compose/compose.resources.yml --profile '*' config --no-env-resolution --quiet
resources-apply:
	@test -n "$(RESOURCE_SERVICES)" || { echo 'Specify previously running services'; exit 1; }
	$(DOCKER_COMPOSE) up -d --timeout 120 --no-deps --no-build --pull never --wait --wait-timeout 240 $(RESOURCE_SERVICES)
