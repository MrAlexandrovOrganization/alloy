# Explicit host resource tuning; default application lifecycle is unchanged.
.PHONY: resources-check resources-apply
resources-check:
	$(DOCKER_COMPOSE_BASE) --env-file /dev/null -f compose/docker-compose.yml -f compose/compose.resources.yml --profile '*' config --no-interpolate --no-env-resolution --quiet
resources-apply:
	@test -n "$(RESOURCE_SERVICES)" || { echo 'Specify previously running services'; exit 1; }
	$(DOCKER_COMPOSE) up -d --no-deps --no-build --pull never --wait --wait-timeout 240 $(RESOURCE_SERVICES)
