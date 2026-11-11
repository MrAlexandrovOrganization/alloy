# Offline contract check/export; schema tool is vendored and checksum-pinned.
.PHONY: observability-check observability-export
observability-check:
	python3 scripts/observability_package.py check --root "$(CURDIR)"
observability-export: observability-check
	python3 scripts/observability_package.py export --root "$(CURDIR)" --output "$(CURDIR)/.observability/package.tar"
check: observability-check

OBSERVABILITY_OUTPUT ?= $(CURDIR)/.generated/observability
OBSERVABILITY_HOST ?= $(ALLOY_HOST)
.PHONY: observability-sync observability-generate observability-generated-check observability-install
# Updating inputs is explicit. Normal checks/builds never read sibling checkouts.
observability-sync:
	@test -n "$(PACKAGE)" || { echo 'Set PACKAGE to an exported package.tar'; exit 1; }
	python3 scripts/observability.py sync --collector "$(CURDIR)" --package "$(PACKAGE)"
observability-generate:
	python3 scripts/observability.py generate --output "$(OBSERVABILITY_OUTPUT)"
observability-generated-check:
	python3 scripts/observability.py generated-check --public
observability-install: observability-generated-check observability-generate
	python3 scripts/observability.py install --output "$(OBSERVABILITY_OUTPUT)" --monitoring-root "$(MONITORING_DATA_DIR)" --host "$(OBSERVABILITY_HOST)"
check config-check: observability-generated-check
format: observability-public-generate
# Install only assets selected by the private deployment configuration.
directories: observability-install

.PHONY: observability-release-check
observability-release-check: observability-generated-check
	python3 scripts/observability.py release-check


.PHONY: observability-public-generate observability-receive observability-bundle
observability-public-generate:
	python3 scripts/observability.py generate --public --output "$(CURDIR)/.generated/public"
observability-receive:
	python3 scripts/observability.py receive
observability-bundle:
	python3 scripts/observability.py bundle
