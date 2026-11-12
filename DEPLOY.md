# Deployment

The public repository contains reusable collector code and this service's own
observability package. Deployment topology, package catalog and source URLs live
in private deployment inputs, outside Git and public CI artifacts.

Configure the repository's `DEPLOY_ENABLED` variable and these secrets:

- `DEPLOY_SSH_KEY`: restricted SSH deployment credential.
- `DEPLOY_KNOWN_HOSTS`: pinned host keys for this repository's destinations only.
- `DEPLOY_TRANSPORT_JSON`: JSON policy with `targets` and optional `gateway`.
  Each node has `alias`, `host`, `port`, `user`, `key_alias`. Only declared targets
  are contacted; aliases and addresses never appear in public job output.
- `OBSERVABILITY_BUNDLE_1`, `_2`, `_3`: consecutive base64 chunks of a private gzip
  tar bundle (at most 30,000 characters each).
- `OBSERVABILITY_BUNDLE_SHA256`: SHA-256 of the decoded gzip bytes.

A private input directory contains `services.lock.json`, `packages/` and
`deployment.json`. The latter declares active host IDs, central destinations,
per-service local destinations, and optional observed component inventory. It is
provisioning data; never commit it or upload it as a public Actions artifact.
Logical `local` and `central` resource locations are resolved through that policy.
`make observability-sync PACKAGE=/absolute/path/package.tar` updates only the
ignored private catalog. `make observability-bundle` exports its versioned inputs.
Keep the bundle hash with release records for configuration rollback.

Normal CI checks the public template and synthetic parser fixtures. Deployment
receives the private bundle over the existing pinned SSH transport, validates
paths, input checksums, committed revisions and ownership, and retains previous
inputs before activation. No source code is copied outside the Git/CI workflow.
`make deploy` then validates the generated Alloy file, waits for receivers,
installs host-selected file providers, recreates the collector and runs smoke.
Set `OBSERVABILITY_INSTALL_ROOT` in the private `.env` when central providers
read a different directory from the collector's `MONITORING_DATA_DIR`. For example,
a collector can keep its targets in `~/.monitoring-wsl` while installing the
central Grafana providers in `~/.monitoring`. The default remains
`MONITORING_DATA_DIR`; existing collector mounts and state are preserved.

The generated runtime config is `.generated/observability/config.alloy`; Compose
mounts the `ALLOY_CONFIG_FILE` selected by Make. Persistent volumes, component IDs
and log positions are unchanged. Existing locally edited provider files stop
installation before mutation; known legacy files are adopted/retired by exact
hash. Private datasource credentials remain separate from packaged templates.

A deployment receipt on the receiver proves the checked Git revision; verify
runtime health, loaded config and current telemetry separately. A green CI job
without a deployment receipt is not a deployed release. Inactive destinations
must be removed from the private policy; do not probe or restore them.

The restricted receiver/bootstrap commands are explicit administrative tools.
Use a private policy and existing access; do not replace unknown authorized keys.
Additional local Compose overlays and private settings are preserved at rollout.

The receiver policy in `/etc/observability-deploy.json` also declares `projects`,
`repositories` (a project-to-HTTPS repository URL map), `sudo_make`, `host`, and
optionally `base_path`. The public receiver contains no organization, repository
catalog or machine inventory. Supply this mapping when explicitly bootstrapping
or updating the restricted receiver; keep the policy and its backups private.
