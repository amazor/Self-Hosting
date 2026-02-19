#!/usr/bin/env python3
"""Monitoring VM bootstrap (idempotent).

Owns:
  - creating/validating local .env for this stack
  - creating config directories with correct ownership
  - generating starter configs (Prometheus, Loki, Alloy, Grafana provisioning)
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
import os
import shutil
import socket
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
    setup_logging,
    try_chown,
    try_sudo_chown,
)

# Container image UIDs (from official Dockerfiles)
_PROMETHEUS_UID = 65534  # nobody
_LOKI_UID = 10001
_GRAFANA_UID = 472
_ALLOY_UID = 473  # grafana/alloy image
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
        config_base / "grafana" / "provisioning" / "plugins",
        config_base / "grafana" / "provisioning" / "alerting",
        config_base / "prometheus",
        config_base / "prometheus" / "data",
        config_base / "loki",
        config_base / "loki" / "data",
        config_base / "uptime-kuma",
        config_base / "uptime-kuma" / "upload",
        config_base / "alloy",
        config_base / "alloy" / "data",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    log.info(f"Ensured config directories under: {config_base}")


def _chown_dir(path: Path, uid: int, label: str) -> None:
    """Set ownership on a directory, trying without sudo first."""
    if try_chown(path, uid, uid, recursive=True):
        log.info(f"Set {label} ownership to {uid}:{uid}.")
    elif try_sudo_chown(path, uid, uid, recursive=True):
        log.info(f"Set {label} ownership to {uid}:{uid} (via sudo).")
    else:
        log.warning(
            f"Could not chown {path} to {uid}:{uid}.\n"
            f"If {label} fails with permission errors, run once:\n"
            f"  sudo chown -R {uid}:{uid} {path}"
        )


def fix_ownership(config_base: Path) -> None:
    """Set ownership on data directories per official image UIDs."""
    _chown_dir(
        config_base / "prometheus" / "data", _PROMETHEUS_UID, "Prometheus data"
    )
    _chown_dir(config_base / "loki" / "data", _LOKI_UID, "Loki data")
    _chown_dir(config_base / "grafana" / "data", _GRAFANA_UID, "Grafana data")
    _chown_dir(config_base / "alloy" / "data", _ALLOY_UID, "Alloy data")
    _chown_dir(config_base / "uptime-kuma", _UPTIME_KUMA_UID, "Uptime Kuma data")


# ---------------------------------------------------------------------------
# Starter config generation (idempotent — only writes if file missing)
# ---------------------------------------------------------------------------


def _detect_hostname() -> str:
    """Return the system hostname for labeling."""
    return socket.gethostname()


def ensure_prometheus_config(config_base: Path, env: dict[str, str]) -> None:
    conf = config_base / "prometheus" / "prometheus.yml"
    if conf.is_file():
        log.info("prometheus.yml already exists; not overwriting.")
        return

    hostname = env.get("MONITORING_HOSTNAME") or _detect_hostname()

    conf.write_text(
        f"""\
# Prometheus scrape config (generated by bootstrap).
# Edit to add remote VM targets as your lab grows.

global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:9090"]
        labels:
          instance: "{hostname}"

  - job_name: "node-exporter"
    static_configs:
      - targets: ["host.docker.internal:9100"]
        labels:
          instance: "{hostname}"
      # Add remote VMs (use their LAN IP):
      # - targets: ["192.168.1.110:9100"]
      #   labels:
      #     instance: "core"

  - job_name: "cadvisor"
    static_configs:
      - targets: ["cadvisor:8080"]
        labels:
          instance: "{hostname}"
      # Add remote VMs:
      # - targets: ["192.168.1.110:8080"]
      #   labels:
      #     instance: "core"
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


def ensure_alloy_config(config_base: Path, env: dict[str, str]) -> None:
    conf = config_base / "alloy" / "config.alloy"
    if conf.is_file():
        log.info("config.alloy already exists; not overwriting.")
        return

    hostname = env.get("MONITORING_HOSTNAME") or _detect_hostname()

    conf.write_text(
        f"""\
// Alloy config (generated by bootstrap).
// Aligned with grafana/alloy-scenarios/self-monitoring: discovery.docker targets +
// relabel_rules for Loki; optional self-metrics to Prometheus.

// --- Alloy self-metrics (health, pipeline) to Prometheus ---
prometheus.exporter.self "alloy_health" {{}}

discovery.relabel "alloy_health" {{
  targets = prometheus.exporter.self.alloy_health.targets

  rule {{
    target_label = "instance"
    replacement  = "{hostname}"
  }}

  rule {{
    target_label = "container"
    replacement  = "alloy"
  }}
}}

prometheus.scrape "alloy_health" {{
  targets    = discovery.relabel.alloy_health.output
  job_name   = "integrations/alloy"
  forward_to = [prometheus.remote_write.default.receiver]
}}

prometheus.remote_write "default" {{
  endpoint {{
    url = "http://prometheus:9090/api/v1/write"
  }}
}}

// --- Logging: Docker container logs → Loki ---
// Labels for dashboards: "host" = VM/Alloy instance (group by VM), "container" = service
// name, "level" = extracted when present so you can filter by severity in Grafana.
discovery.docker "local" {{
  host = "unix:///var/run/docker.sock"
}}

// Rules only (targets = []); loki.source.docker gets raw discovery targets.
// Container regex extracts compose service name: /project-service-1 -> service.
// We also set container_name, service_name, instance so Grafana Loki dashboards
// (e.g. built-in Loki v3 dashboards) work: they expect those label names.
// https://github.com/grafana/alloy-scenarios/blob/main/self-monitoring/config.alloy
discovery.relabel "docker" {{
  targets = []

  rule {{
    source_labels = ["__meta_docker_container_name"]
    regex         = "^/(?:.+?-)?([^-]+)-(?:\\\\d+)$"
    target_label  = "container"
  }}

  rule {{
    target_label = "host"
    replacement  = "{hostname}"
  }}

  // Dashboard compatibility: instance = VM/host, container_name/service_name/job = service.
  // job is used by "Logs / App" style dashboards (e.g. gnetId 24866) for the App dropdown.
  rule {{
    target_label  = "instance"
    replacement   = "{hostname}"
  }}
  rule {{
    source_labels = ["container"]
    target_label  = "container_name"
  }}
  rule {{
    source_labels = ["container"]
    target_label  = "service_name"
  }}
  rule {{
    source_labels = ["container"]
    target_label  = "job"
  }}
}}

loki.source.docker "default" {{
  host         = "unix:///var/run/docker.sock"
  targets      = discovery.docker.local.targets
  relabel_rules = discovery.relabel.docker.rules
  forward_to   = [loki.process.levels.receiver]
}}

// Extract log level from common patterns (ERROR, WARN, "level":"info", etc.)
// so you can filter by level in Grafana Explore (e.g. {{host="monitoring",level="error"}}).
// Logs without a match keep container/host labels but no level label.
loki.process "levels" {{
  stage.regex {{
    expression = "(?i)\\\\b(?P<level>trace|debug|info|warn|warning|error|fatal|critical)\\\\b"
  }}
  stage.labels {{
    values = {{ level = "" }}
  }}
  forward_to = [loki.write.local.receiver]
}}

loki.write "local" {{
  endpoint {{
    url = "http://loki:3100/loki/api/v1/push"
  }}
}}
"""
    )
    log.info(f"Created starter Alloy config: {conf}")


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
    log.info("  node_exporter: http://localhost:9100/metrics (host network)")
    log.info("  cAdvisor:      http://localhost:8080")
    log.info("  Alloy:         http://localhost:12345 (management UI)")
    log.info("")
    log.info(f"  Prometheus retention: {retention_prom}")
    log.info(f"  Loki retention:      {retention_loki}")
    log.info("")
    log.info(
        "Grafana datasources (Prometheus + Loki) are auto-provisioned. "
        "Import a community dashboard (e.g. Node Exporter Full, ID 1860) "
        "to get started."
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
    ensure_alloy_config(config_base, env)
    ensure_grafana_provisioning(config_base)

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
