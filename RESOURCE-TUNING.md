# Hostkey Russia resource tuning

Opt-in Compose overlay `compose/compose.resources.yml` changes only CPU ceilings
and healthcheck timing. No images, commands, credentials, ports, networks, volumes
or restart policies are changed. CPU ceilings are not CPU reservations and cannot
remove hypervisor steal time. Existing 0.1/0.2 CPU limits are raised to 0.25 for
light applications and 0.5 for databases/Caddy so short operations can finish.
VPN ceilings stay unchanged. Healthchecks run every 60s for background services
and 30s for ingress; startup probes run every 5s for 30s. Three failures can take
up to roughly three intervals plus probe execution time to mark unhealthy.

For this configuration-only rollout, commits use `[resources-only]`; application
CI still validates/builds, but its deployment job is skipped so stopped services
are not activated. Resource Compose validation runs separately through Make.
Apply only services confirmed running immediately before the change, with their
current immutable images, using `make -f Makefile -f makefiles/resources.mk
resources-apply RESOURCE_SERVICES="..."` and a Compose selection including this
overlay. Do not run an unqualified project `up` during this operation.
For ordinary Make commands, append the overlay to the host's private COMPOSE_FILE.
Managed VLESS entry includes it automatically. Preserve private overrides before
this final overlay. Keep a private snapshot for rollback and compare normalized
Compose configurations before activation; only resources/healthcheck may differ.
Rollback removes this overlay from the selection and recreates the same selected
services with the recorded previous images/settings. Data volumes are retained.
