#!/usr/bin/env python3
"""Monitoring VM bootstrap (idempotent).

Owns:
  - creating/validating local .env for this stack
  - creating config directories with correct ownership
  - generating starter configs (Prometheus, Loki, Grafana provisioning)
  - generating Alloy config via shared setup_observability_config()
  - generating Prometheus file_sd scrape targets from SCRAPE_TARGETS env var
  - validating compose syntax
  - optional local bring-up with --up

Does NOT own:
  - symlinks, shell helper functions, or cross-stack orchestration (deploy.py)

Usage:
  cd docker_compose/monitoring && python3 bootstrap.py
  cd docker_compose/monitoring && python3 bootstrap.py --up
  cd docker_compose/monitoring && python3 bootstrap.py --force
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.homelab_common import (
    is_placeholder,
    load_env,
    log,
    require_docker,
    resolve_config_base,
    resolve_vm_identity,
    setup_logging,
    setup_observability_config,
    try_chown,
    try_sudo_chown,
)

# Container image UIDs (from official Dockerfiles)
_PROMETHEUS_UID = 65534  # nobody
_LOKI_UID = 10001
_GRAFANA_UID = 472
_UPTIME_KUMA_UID = 1000  # louislam/uptime-kuma (node user when non-root)

_MIN_DISK_GB = 10


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitoring VM bootstrap (idempotent).",
        epilog=(
            "All monitoring data must live on local disk (no NFS).\n"
            "Prometheus TSDB, Loki TSDB, and Uptime Kuma SQLite all\n"
            "require POSIX-compliant filesystems.\n\n"
            "Tune PROMETHEUS_RETENTION and LOKI_RETENTION in .env to\n"
            "balance history depth vs disk usage (default: 30d each)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--up", action="store_true", help="Start stack after bootstrap."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip placeholder guardrails for local testing.",
    )
    parser.add_argument(
        "--non-interactive",
        "-y",
        action="store_true",
        help="Accepted for deploy.py compatibility (no-op).",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Bootstrap steps
# ---------------------------------------------------------------------------


def prepare_env_file() -> dict[str, str]:
    """Ensure .env exists (copy from .env.example if needed) and load it."""
    env_file = SCRIPT_DIR / ".env"
    env_example = SCRIPT_DIR / ".env.example"

    if env_file.is_file():
        return load_env(env_file)

    if not env_example.is_file():
        log.error(f"Missing {env_example}; cannot initialize .env.")
        raise SystemExit(1)

    shutil.copy2(env_example, env_file)
    log.info("Created .env from .env.example.")
    log.info(f"Fill real values in {env_file}, then re-run bootstrap.")
    raise SystemExit(1)


def validate_guardrails(env: dict[str, str]) -> None:
    """Verify required vars are not placeholders or weak defaults."""
    def grafana_password_ok(val: str | None) -> bool:
        if is_placeholder(val):
            return False
        # Treat simple default as placeholder; use --force to accept it.
        if (val or "").strip().lower() == "admin":
            return False
        return True

    checks = {
        "GRAFANA_ADMIN_PASSWORD": (
            "Change the password in .env (bootstrap only verifies it is not the default). "
            "Use --force to skip this check (e.g. local testing only)."
        ),
    }
    for var, hint in checks.items():
        if var == "GRAFANA_ADMIN_PASSWORD":
            ok = grafana_password_ok(env.get(var))
        else:
            ok = not is_placeholder(env.get(var))
        if not ok:
            msg = f"{var} is missing/placeholder in .env."
            if hint:
                msg += f"\n{hint}"
            log.error(msg)
            raise SystemExit(1)


def check_disk_space(config_base: Path) -> None:
    """Warn if available disk space is below the minimum."""
    try:
        stat = os.statvfs(config_base)
        avail_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
        if avail_gb < _MIN_DISK_GB:
            log.warning(
                f"Low disk space: {avail_gb:.1f} GB available at {config_base}. "
                f"Monitoring data needs at least {_MIN_DISK_GB} GB. "
                "Consider resizing the VM disk or reducing retention."
            )
        else:
            log.info(f"Disk space: {avail_gb:.1f} GB available at {config_base}.")
    except OSError:
        pass


def ensure_config_directories(config_base: Path) -> None:
    dirs = [
        config_base / "grafana" / "data",
        config_base / "grafana" / "provisioning" / "datasources",
        config_base / "grafana" / "provisioning" / "dashboards",
        config_base / "grafana" / "provisioning" / "dashboards" / "json",
        config_base / "grafana" / "provisioning" / "plugins",
        config_base / "grafana" / "provisioning" / "alerting",
        config_base / "prometheus",
        config_base / "prometheus" / "data",
        config_base / "loki",
        config_base / "loki" / "data",
        config_base / "uptime-kuma",
        config_base / "uptime-kuma" / "upload",
        config_base / "prometheus" / "targets",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    log.info(f"Ensured config directories under: {config_base}")


def _chown_dir(path: Path, uid: int, gid: int, label: str) -> None:
    """Set ownership on a directory, trying without sudo first."""
    if try_chown(path, uid, gid, recursive=True):
        log.info(f"Set {label} ownership to {uid}:{gid}.")
    elif try_sudo_chown(path, uid, gid, recursive=True):
        log.info(f"Set {label} ownership to {uid}:{gid} (via sudo).")
    else:
        log.warning(
            f"Could not chown {path} to {uid}:{gid}.\n"
            f"If {label} fails with permission errors, run once:\n"
            f"  sudo chown -R {uid}:{gid} {path}"
        )


def fix_ownership(config_base: Path) -> None:
    """Set ownership on data directories. Image UIDs for Prom/Loki/Grafana; host user for Uptime Kuma."""
    host_uid = os.getuid()
    host_gid = os.getegid()

    _chown_dir(
        config_base / "prometheus" / "data", _PROMETHEUS_UID, _PROMETHEUS_UID, "Prometheus data"
    )
    _chown_dir(config_base / "loki" / "data", _LOKI_UID, _LOKI_UID, "Loki data")
    _chown_dir(config_base / "grafana" / "data", _GRAFANA_UID, _GRAFANA_UID, "Grafana data")
    _chown_dir(config_base / "uptime-kuma", host_uid, host_gid, "Uptime Kuma data")


# ---------------------------------------------------------------------------
# Starter config generation (idempotent — only writes if file missing)
# ---------------------------------------------------------------------------


def ensure_prometheus_config(config_base: Path, env: dict[str, str]) -> None:
    conf = config_base / "prometheus" / "prometheus.yml"
    if conf.is_file():
        log.info("prometheus.yml already exists; not overwriting.")
        return

    hostname, vm_role, node = resolve_vm_identity(env, SCRIPT_DIR)

    conf.write_text(
        f"""\
# Prometheus scrape config (generated by bootstrap).
# Remote scrape targets are managed via SCRAPE_TARGETS in .env;
# bootstrap writes them to targets/scrape-targets.json (file_sd).
#
# Label contract (aligned with Alloy log labels):
#   instance, host, vm_role, node, env
# Every target MUST carry these so dashboard variables cascade correctly.

global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:9090"]
        labels:
          instance: "{hostname}"
          host: "{hostname}"
          vm_role: "{vm_role}"
          node: "{node}"
          env: "prod"
          service: "prometheus"

  - job_name: "node-exporter"
    static_configs:
      - targets: ["host.docker.internal:9100"]
        labels:
          instance: "{hostname}"
          host: "{hostname}"
          vm_role: "{vm_role}"
          node: "{node}"
          env: "prod"
          service: "node-exporter"
    file_sd_configs:
      - files: ["/etc/prometheus/targets/scrape-targets.json"]
        refresh_interval: 5m

  - job_name: "cadvisor"
    static_configs:
      - targets: ["host.docker.internal:8080"]
        labels:
          instance: "{hostname}"
          host: "{hostname}"
          vm_role: "{vm_role}"
          node: "{node}"
          env: "prod"
          service: "cadvisor"
    file_sd_configs:
      - files: ["/etc/prometheus/targets/scrape-targets.json"]
        refresh_interval: 5m
    metric_relabel_configs:
      # --- Container identity labels (aligned with Alloy/Loki label contract) ---
      #
      # container: stable container instance name (alias for cAdvisor 'name' label).
      - source_labels: [name]
        regex: '(.+)'
        target_label: container

      # service: logical service identity — mirrors Alloy's priority chain.
      # Rules are evaluated in order; last write wins, so list in ascending priority.
      #   1. container name (lowest priority / fallback)
      - source_labels: [name]
        regex: '(.+)'
        target_label: service
      #   2. Compose service name (stable; preferred for compose-managed containers)
      - source_labels: [container_label_com_docker_compose_service]
        regex: '(.+)'
        target_label: service
      #   3. Explicit homelab override (highest priority; set label com.homelab.service=xxx)
      - source_labels: [container_label_com_homelab_service]
        regex: '(.+)'
        target_label: service

      # compose_project: Docker Compose project/stack name.
      - source_labels: [container_label_com_docker_compose_project]
        regex: '(.+)'
        target_label: compose_project

      # Strip image digest to reduce churn (keep repo[:tag] only, drop @sha256:...).
      - source_labels: [image]
        regex: '^([^@]+)(?:@.+)?$'
        replacement: '$1'
        target_label: image

      # Drop high-cardinality and noisy labels:
      #   id                — container ID hash (changes on every restart)
      #   container_label_* — raw Docker labels (values extracted above; no longer needed)
      - action: labeldrop
        regex: 'id|container_label_.*'
"""
    )
    log.info(f"Created starter Prometheus config: {conf}")


def ensure_loki_config(config_base: Path, env: dict[str, str]) -> None:
    conf = config_base / "loki" / "loki-config.yml"
    if conf.is_file():
        log.info("loki-config.yml already exists; not overwriting.")
        return

    retention = env.get("LOKI_RETENTION", "30d")
    conf.write_text(
        f"""\
# Loki config (generated by bootstrap).
# Single-node deployment with filesystem storage.
# Options aligned with grafana/alloy-scenarios/docker-monitoring where applicable.

auth_enabled: false

limits_config:
  retention_period: {retention}
  allow_structured_metadata: true
  volume_enabled: true

server:
  http_listen_port: 3100
  grpc_listen_port: 9096

common:
  instance_addr: 127.0.0.1
  path_prefix: /loki
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory

schema_config:
  configs:
    - from: 2020-10-24
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h

compactor:
  working_directory: /loki/compactor
  retention_enabled: true
  delete_request_store: filesystem

# Pattern detection for Grafana Explore (docker-monitoring pattern_ingester).
pattern_ingester:
  enabled: true

query_range:
  results_cache:
    cache:
      embedded_cache:
        enabled: true
        max_size_mb: 100

analytics:
  reporting_enabled: false
"""
    )
    log.info(f"Created starter Loki config: {conf}")


def generate_scrape_targets(config_base: Path, env: dict[str, str]) -> None:
    """Parse SCRAPE_TARGETS from .env and write Prometheus file_sd JSON.

    Always regenerates (not skip-if-exists) so the file reflects the current
    .env on every bootstrap run.  Empty SCRAPE_TARGETS = empty JSON array.

    Format: hostname:ip,hostname:ip,...
    vm_role defaults to hostname; node defaults to PROXMOX_NODE.
    """
    targets_dir = config_base / "prometheus" / "targets"
    targets_dir.mkdir(parents=True, exist_ok=True)
    targets_file = targets_dir / "scrape-targets.json"

    raw = env.get("SCRAPE_TARGETS", "").strip()
    _, _, default_node = resolve_vm_identity(env, SCRIPT_DIR)

    entries: list[dict] = []
    if raw:
        for pair in raw.split(","):
            pair = pair.strip()
            if ":" not in pair:
                continue
            parts = pair.split(":")
            if len(parts) < 2:
                continue
            name, ip = parts[0].strip(), parts[1].strip()
            if not name or not ip:
                continue
            vm_role = name
            node = default_node
            base_labels = {
                "instance": name,
                "host": name,
                "vm_role": vm_role,
                "node": node,
                "env": "prod",
            }
            entries.append({
                "targets": [f"{ip}:9100"],
                "labels": {**base_labels, "service": "node-exporter"},
            })
            entries.append({
                "targets": [f"{ip}:8080"],
                "labels": {**base_labels, "service": "cadvisor"},
            })

    targets_file.write_text(json.dumps(entries, indent=2) + "\n")
    count = len(entries) // 2
    if count:
        log.info(f"Generated scrape targets for {count} remote VM(s): {targets_file}")
    else:
        log.info(f"No remote scrape targets (SCRAPE_TARGETS empty): {targets_file}")


def ensure_grafana_provisioning(config_base: Path) -> None:
    ds_dir = config_base / "grafana" / "provisioning" / "datasources"
    ds_file = ds_dir / "datasources.yml"
    if ds_file.is_file():
        log.info("Grafana datasources provisioning already exists; not overwriting.")
        return

    ds_dir.mkdir(parents=True, exist_ok=True)
    ds_file.write_text(
        """\
# Grafana datasource provisioning (generated by bootstrap).
# Prometheus and Loki are auto-configured on first boot.
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: true

  - name: Loki
    type: loki
    access: proxy
    url: http://loki:3100
    editable: true
"""
    )
    log.info(f"Created Grafana datasource provisioning: {ds_file}")


def ensure_grafana_dashboard_provisioning(config_base: Path) -> None:
    """Create dashboard provider YAML and copy dashboard JSONs into the config tree.

    Provider tells Grafana to load JSON files from a known directory.
    Dashboard source-of-truth lives in the repo (docker_compose/monitoring/dashboards/).
    Bootstrap copies them into the config tree so the read-only provisioning mount works.
    """
    dash_prov_dir = config_base / "grafana" / "provisioning" / "dashboards"
    dash_prov_dir.mkdir(parents=True, exist_ok=True)

    provider_file = dash_prov_dir / "provider.yml"
    if not provider_file.is_file():
        provider_file.write_text(
            """\
apiVersion: 1

providers:
  - name: homelab
    folder: Homelab
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: true
    options:
      path: /etc/grafana/provisioning/dashboards/json
      foldersFromFilesStructure: false
"""
        )
        log.info(f"Created Grafana dashboard provider: {provider_file}")

    json_dir = dash_prov_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)

    repo_dashboards = SCRIPT_DIR / "dashboards"
    if not repo_dashboards.is_dir():
        log.info("No repo dashboards directory found; skipping dashboard copy.")
        return

    copied = 0
    for src in sorted(repo_dashboards.glob("*.json")):
        dst = json_dir / src.name
        if not dst.is_file() or src.read_bytes() != dst.read_bytes():
            shutil.copy2(src, dst)
            copied += 1
    if copied:
        log.info(f"Copied {copied} dashboard(s) to {json_dir}")
    else:
        log.info("Dashboards already up to date.")


def validate_compose() -> None:
    result = subprocess.run(
        ["docker", "compose", "-f", str(SCRIPT_DIR / "compose.yml"), "config"],
        capture_output=True,
        cwd=SCRIPT_DIR,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode() if result.stderr else ""
        log.error(f"docker compose config validation failed.\n{stderr}")
        raise SystemExit(1)
    log.info("Compose file validates successfully.")


def bring_up_stack() -> None:
    log.info("Starting monitoring stack...")
    subprocess.run(
        ["docker", "compose", "-f", str(SCRIPT_DIR / "compose.yml"), "up", "-d"],
        check=True,
        cwd=SCRIPT_DIR,
    )
    log.info("Monitoring stack started.")


def print_summary(config_base: Path, env: dict[str, str]) -> None:
    retention_prom = env.get("PROMETHEUS_RETENTION", "30d")
    retention_loki = env.get("LOKI_RETENTION", "30d")

    log.info("")
    log.info(f"Config: {config_base}")
    log.info("")
    log.info("--- Service summary ---")
    log.info("  Grafana:       http://localhost:3000")
    log.info("  Prometheus:    http://localhost:9090")
    log.info("  Loki:          http://localhost:3100 (push endpoint)")
    log.info("  Uptime Kuma:   http://localhost:3001")
    obs = env.get("ENABLE_OBSERVABILITY", "1") == "1"
    if obs:
        log.info("  node_exporter: http://localhost:9100/metrics (host network)")
        log.info("  cAdvisor:      http://localhost:8080")
        log.info("  Alloy:         http://localhost:12345 (management UI)")
    else:
        log.info("  Observability sidecars: disabled (ENABLE_OBSERVABILITY=0)")
    log.info("")
    log.info(f"  Prometheus retention: {retention_prom}")
    log.info(f"  Loki retention:      {retention_loki}")
    log.info("")
    log.info(
        "Grafana datasources (Prometheus + Loki) and the Homelab Overview "
        "dashboard are auto-provisioned. Look in the 'Homelab' folder in Grafana."
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    setup_logging()
    log.info("--- Monitoring VM bootstrap ---")

    require_docker()
    env = prepare_env_file()

    if not args.force:
        validate_guardrails(env)

    config_base = resolve_config_base(
        env.get("CONFIG_ROOT", "./config"), SCRIPT_DIR
    )

    ensure_config_directories(config_base)
    check_disk_space(config_base)
    fix_ownership(config_base)

    ensure_prometheus_config(config_base, env)
    ensure_loki_config(config_base, env)
    generate_scrape_targets(config_base, env)
    ensure_grafana_provisioning(config_base)
    ensure_grafana_dashboard_provisioning(config_base)

    if env.get("ENABLE_OBSERVABILITY", "1") == "1":
        setup_observability_config(config_base, env, SCRIPT_DIR)

    validate_compose()

    deploy_mode = bool(os.environ.get("HOMELAB_DEPLOY"))

    if args.up:
        bring_up_stack()
    else:
        if deploy_mode:
            log.info("Bootstrap complete.")
        else:
            log.info(
                "Bootstrap complete. Run 'docker compose up -d' "
                "(or use the monitoring alias) when ready."
            )

    if not deploy_mode:
        print_summary(config_base, env)


if __name__ == "__main__":
    main()