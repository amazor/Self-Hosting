"""Monitoring VM stack configuration — registry entry for deploy.py and bootstrap.

Defines the monitoring stack's required vars, compose overlays, bootstrap
steps, and post-deploy hooks.  Consumed by BootstrapRunner and deploy.py.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.homelab_logging import StepTracker

SCRIPT_DIR = Path(__file__).resolve().parent

from scripts.homelab_common import (
    is_placeholder,
    resolve_vm_identity,
    try_chown,
    try_sudo_chown,
)

# ---------------------------------------------------------------------------
# Stack identity
# ---------------------------------------------------------------------------

STACK_NAME = "monitoring"
COPIES_ENV_EXAMPLE = True
REQUIRES_DOCKER = True

REQUIRED_VARS = [
    "GRAFANA_ADMIN_PASSWORD",
]

COMPOSE_OVERLAYS: list[tuple[str, str, str, str]] = [
    ("ENABLE_OBSERVABILITY", "compose.observability.yml", "1", "Observability"),
    # Default ON: Alertmanager (compose.yml) and Grafana's own alerting both
    # deliver through ntfy — without it, alert rules load and evaluate but
    # nothing reaches a human. See docs/Chapter3b-monitoring-stack.md.
    ("ENABLE_NTFY", "compose.ntfy.yml", "1", "ntfy"),
]

# Container image UIDs
_PROMETHEUS_UID = 65534
_LOKI_UID = 10001
_GRAFANA_UID = 472
_ALERTMANAGER_UID = 65534
_MIN_DISK_GB = 10

# Default ntfy topic. Must match the url in alerting/grafana/contact-points.yml,
# which _render_alerting_file() rewrites to the configured NTFY_ALERT_TOPIC.
_DEFAULT_NTFY_TOPIC = "homelab-alerts"

# Written by bootstrap (root), consumed by post_deploy (deploy user): the set of
# services whose alerting config changed and therefore need to re-read it.
_RELOAD_MARKER = ".alerting-reload"
_RELOAD_ATTEMPTS = 3
_RELOAD_BACKOFF_S = 2


# ---------------------------------------------------------------------------
# Custom guardrails
# ---------------------------------------------------------------------------

def _validate_grafana_password(
    env: dict[str, str], tracker: StepTracker, *, force: bool,
) -> None:
    val = env.get("GRAFANA_ADMIN_PASSWORD", "")
    if val and not is_placeholder(val) and val.strip().lower() != "admin":
        tracker.detail("GRAFANA_ADMIN_PASSWORD... ok")
        return
    msg = "GRAFANA_ADMIN_PASSWORD is missing/placeholder or set to 'admin'"
    if force:
        tracker.warn(f"{msg} (continuing with --force)")
    else:
        tracker.fail(msg)
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Bootstrap step functions
# ---------------------------------------------------------------------------

def _ensure_config_directories(config_base: Path, tracker: StepTracker) -> None:
    dirs = [
        config_base / "grafana" / "data",
        config_base / "grafana" / "provisioning" / "datasources",
        config_base / "grafana" / "provisioning" / "dashboards",
        config_base / "grafana" / "provisioning" / "dashboards" / "json",
        config_base / "grafana" / "provisioning" / "plugins",
        config_base / "grafana" / "provisioning" / "alerting",
        config_base / "prometheus",
        config_base / "prometheus" / "data",
        config_base / "prometheus" / "rules",
        config_base / "alertmanager",
        config_base / "alertmanager" / "data",
        config_base / "loki",
        config_base / "loki" / "data",
        config_base / "blackbox-exporter",
        config_base / "uptime-kuma",
        config_base / "uptime-kuma" / "upload",
        config_base / "prometheus" / "targets",
        config_base / "ntfy",
        config_base / "ntfy" / "cache",
        config_base / "ntfy" / "templates",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    tracker.success(f"{len(dirs)} directories ready")


def _check_disk_space(config_base: Path, tracker: StepTracker) -> None:
    try:
        stat = os.statvfs(config_base)
        avail_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
        if avail_gb < _MIN_DISK_GB:
            tracker.warn(
                f"Low disk: {avail_gb:.1f} GB available (need {_MIN_DISK_GB} GB)"
            )
        else:
            tracker.detail(f"Disk space: {avail_gb:.1f} GB available")
    except OSError:
        pass


def _chown_dir(path: Path, uid: int, gid: int, label: str, tracker: StepTracker) -> None:
    if try_chown(path, uid, gid, recursive=True):
        tracker.detail(f"Set {label} ownership to {uid}:{gid}")
    elif try_sudo_chown(path, uid, gid, recursive=True):
        tracker.detail(f"Set {label} ownership to {uid}:{gid} (via sudo)")
    else:
        tracker.warn(f"Could not chown {path} to {uid}:{gid}")


def _fix_ownership(config_base: Path, env: dict[str, str], tracker: StepTracker) -> None:
    try:
        host_uid = int(env.get("PUID", "") or os.getuid())
        host_gid = int(env.get("PGID", "") or os.getegid())
    except ValueError:
        host_uid = os.getuid()
        host_gid = os.getegid()
    _chown_dir(config_base / "prometheus" / "data", _PROMETHEUS_UID, _PROMETHEUS_UID, "Prometheus data", tracker)
    _chown_dir(config_base / "alertmanager" / "data", _ALERTMANAGER_UID, _ALERTMANAGER_UID, "Alertmanager data", tracker)
    _chown_dir(config_base / "loki" / "data", _LOKI_UID, _LOKI_UID, "Loki data", tracker)
    _chown_dir(config_base / "grafana" / "data", _GRAFANA_UID, _GRAFANA_UID, "Grafana data", tracker)
    _chown_dir(config_base / "uptime-kuma", host_uid, host_gid, "Uptime Kuma data", tracker)
    tracker.success("Ownership set")


def _ensure_prometheus_config(env: dict[str, str], config_base: Path, tracker: StepTracker) -> None:
    conf = config_base / "prometheus" / "prometheus.yml"
    hostname, vm_role, node = resolve_vm_identity(env, SCRIPT_DIR)

    conf.write_text(
        f"""\
# Prometheus scrape config (generated by bootstrap).
global:
  scrape_interval: 15s
  evaluation_interval: 15s

# Alert rules (config/prometheus/rules/*.yml) — copied verbatim from
# docker_compose/monitoring/alerting/prometheus-rules.yml. Loaded and
# evaluated by Prometheus itself (see /api/v1/rules); firing alerts are
# handed to Alertmanager below.
rule_files:
  - "/etc/prometheus/rules/*.yml"

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["alertmanager:9093"]

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
      - files: ["/etc/prometheus/targets/scrape-targets-node-exporter.json"]
        refresh_interval: 5m

  - job_name: "cadvisor"
    # cadvisor embeds its own internal timestamps (from its ~10s housekeeping
    # cycle) in /metrics, which don't align with our scrape_interval — that
    # beat-frequency mismatch periodically yields a timestamp older than the
    # last stored sample, which Prometheus then drops as "out-of-order".
    # honor_timestamps: false makes Prometheus stamp samples with actual
    # scrape wall-clock time instead of trusting cadvisor's clock — the
    # standard fix for this well-known cadvisor/Prometheus interaction.
    honor_timestamps: false
    static_configs:
      - targets: ["host.docker.internal:8081"]
        labels:
          instance: "{hostname}"
          host: "{hostname}"
          vm_role: "{vm_role}"
          node: "{node}"
          env: "prod"
          service: "cadvisor"
    file_sd_configs:
      - files: ["/etc/prometheus/targets/scrape-targets-cadvisor.json"]
        refresh_interval: 5m
    metric_relabel_configs:
      - source_labels: [name]
        regex: '(.+)'
        target_label: container
      - source_labels: [name]
        regex: '(.+)'
        target_label: service
      - source_labels: [container_label_com_docker_compose_service]
        regex: '(.+)'
        target_label: service
      - source_labels: [container_label_com_homelab_service]
        regex: '(.+)'
        target_label: service
      - source_labels: [container_label_com_docker_compose_project]
        regex: '(.+)'
        target_label: compose_project
      - source_labels: [image]
        regex: '^([^@]+)(?:@.+)?$'
        replacement: '$1'
        target_label: image
      - action: labeldrop
        regex: 'id|container_label_.*'

  - job_name: "plex-exporter"
    file_sd_configs:
      - files: ["/etc/prometheus/targets/scrape-targets-plex-exporter.json"]
        refresh_interval: 5m

  - job_name: "expressvpn"
    metrics_path: /metrics.cgi
    file_sd_configs:
      - files: ["/etc/prometheus/targets/scrape-targets-expressvpn.json"]
        refresh_interval: 5m

  - job_name: "scraparr"
    file_sd_configs:
      - files: ["/etc/prometheus/targets/scrape-targets-scraparr.json"]
        refresh_interval: 5m

  - job_name: "qbittorrent"
    file_sd_configs:
      - files: ["/etc/prometheus/targets/scrape-targets-qbittorrent.json"]
        refresh_interval: 5m

  - job_name: "sabnzbd"
    file_sd_configs:
      - files: ["/etc/prometheus/targets/scrape-targets-sabnzbd.json"]
        refresh_interval: 5m

  - job_name: "blackbox-exporter"
    static_configs:
      - targets: ["blackbox-exporter:9115"]
        labels:
          instance: "{hostname}"
          host: "{hostname}"
          vm_role: "{vm_role}"
          node: "{node}"
          env: "prod"
          service: "blackbox-exporter"

  - job_name: "blackbox"
    metrics_path: /probe
    file_sd_configs:
      - files: ["/etc/prometheus/targets/blackbox-targets.json"]
        refresh_interval: 5m
    relabel_configs:
      - source_labels: [module]
        target_label: __param_module
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: "blackbox-exporter:9115"
      - action: labeldrop
        regex: module
"""
    )
    tracker.success("Prometheus config generated")


def _ensure_loki_config(env: dict[str, str], config_base: Path, tracker: StepTracker) -> None:
    conf = config_base / "loki" / "loki-config.yml"
    retention = env.get("LOKI_RETENTION", "30d")

    conf.write_text(
        f"""\
# Loki config (generated by bootstrap).
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
    tracker.success("Loki config generated")


def _ensure_blackbox_config(config_base: Path, tracker: StepTracker) -> None:
    conf = config_base / "blackbox-exporter" / "blackbox.yml"
    conf.write_text(
        """\
# Blackbox exporter config (generated by bootstrap).
modules:
  http_2xx:
    prober: http
    timeout: 5s
    http:
      valid_http_versions: ["HTTP/1.1", "HTTP/2.0"]
      follow_redirects: true
      preferred_ip_protocol: ip4

  http_post_2xx:
    prober: http
    timeout: 5s
    http:
      method: POST
      valid_http_versions: ["HTTP/1.1", "HTTP/2.0"]
      preferred_ip_protocol: ip4

  tcp_connect:
    prober: tcp
    timeout: 5s

  icmp:
    prober: icmp
    timeout: 5s
    icmp:
      preferred_ip_protocol: ip4

  dns_udp:
    prober: dns
    timeout: 5s
    dns:
      query_name: google.com
      query_type: A
      transport_protocol: udp
      preferred_ip_protocol: ip4
"""
    )
    tracker.success("Blackbox config generated")


def _ntfy_topic(env: dict[str, str]) -> str:
    return env.get("NTFY_ALERT_TOPIC", _DEFAULT_NTFY_TOPIC).strip() or _DEFAULT_NTFY_TOPIC


def _mark_reload(config_base: Path, services: set[str]) -> None:
    """Record which services need their config re-read after compose up.

    Bootstrap runs as root and in its own process; post_deploy runs later, as the
    deploy user. This little marker file is how the former tells the latter what
    it changed. Left in place if the reload fails, so the next deploy retries.
    """
    if not services:
        return
    marker = config_base / _RELOAD_MARKER
    existing = set(marker.read_text().split()) if marker.is_file() else set()
    marker.write_text("\n".join(sorted(existing | services)) + "\n")


def _ensure_alertmanager_config(env: dict[str, str], config_base: Path, tracker: StepTracker) -> None:
    conf = config_base / "alertmanager" / "alertmanager.yml"
    topic = _ntfy_topic(env)

    content = f"""\
# Alertmanager config (generated by bootstrap).
# Single route -> single receiver: ntfy, via its "alertmanager" webhook
# template (config/ntfy/templates/alertmanager.yml) since Alertmanager's
# raw JSON has no human-friendly title/message on its own.
route:
  receiver: ntfy
  group_by: ["alertname"]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

receivers:
  - name: ntfy
    webhook_configs:
      - url: "http://ntfy/{topic}?template=alertmanager"
        send_resolved: true
"""
    if not conf.is_file() or conf.read_text() != content:
        conf.write_text(content)
        # Alertmanager does not watch its own config file.
        _mark_reload(config_base, {"alertmanager"})
        tracker.success("Alertmanager config generated")
    else:
        tracker.success("Alertmanager config up to date")


def _render_alerting_file(src: Path, env: dict[str, str]) -> bytes:
    """Return the bytes to deploy for an alerting source file.

    Everything is copied verbatim except Grafana's contact point, whose ntfy URL
    carries the topic name. Alertmanager's topic comes from NTFY_ALERT_TOPIC via
    the generated config above; if this file were copied verbatim it would keep
    pointing at the default topic, so changing NTFY_ALERT_TOPIC would quietly
    split the two senders: Prometheus alerts would arrive on the new topic while
    the Grafana/Loki alerts went on publishing to a topic nobody is subscribed to.
    That is the precise failure this whole feature exists to prevent, so the topic
    is substituted here rather than left as a comment telling you to remember.
    """
    data = src.read_bytes()
    if src.name == "contact-points.yml":
        text = re.sub(
            r"http://ntfy/\S+", f"http://ntfy/{_ntfy_topic(env)}", data.decode(),
        )
        data = text.encode()
    return data


def _copy_alerting_files(env: dict[str, str], config_base: Path, tracker: StepTracker) -> None:
    """Sync static alerting sources (rules, ntfy templates, Grafana provisioning)
    into their runtime locations — same pattern as the dashboards/*.json copy
    below. Edit the sources under docker_compose/monitoring/alerting/, not the
    copies under config/.

    Records which services need a reload afterwards; see post_deploy().
    """
    alerting_src = SCRIPT_DIR / "alerting"
    if not alerting_src.is_dir():
        return

    # (source, destination, which service must re-read it)
    copy_map: list[tuple[Path, Path, str]] = [
        (alerting_src / "prometheus-rules.yml",
         config_base / "prometheus" / "rules" / "homelab-rules.yml", "prometheus"),
    ]

    ntfy_templates_src = alerting_src / "ntfy-templates"
    if ntfy_templates_src.is_dir():
        for src in sorted(ntfy_templates_src.glob("*.yml")):
            copy_map.append((src, config_base / "ntfy" / "templates" / src.name, "ntfy"))

    grafana_alerting_src = alerting_src / "grafana"
    if grafana_alerting_src.is_dir():
        for src in sorted(grafana_alerting_src.glob("*.yml")):
            copy_map.append((
                src, config_base / "grafana" / "provisioning" / "alerting" / src.name, "grafana",
            ))

    copied = 0
    changed_services: set[str] = set()
    for src, dst, service in copy_map:
        dst.parent.mkdir(parents=True, exist_ok=True)
        payload = _render_alerting_file(src, env)
        if not dst.is_file() or dst.read_bytes() != payload:
            dst.write_bytes(payload)
            changed_services.add(service)
            copied += 1

    _mark_reload(config_base, changed_services)
    tracker.success(f"Alerting config synced ({copied} file(s) updated)")


def _generate_scrape_targets(env: dict[str, str], config_base: Path, tracker: StepTracker) -> None:
    targets_dir = config_base / "prometheus" / "targets"
    targets_dir.mkdir(parents=True, exist_ok=True)

    _, _, default_node = resolve_vm_identity(env, SCRIPT_DIR)

    def _parse_targets(raw: str, port: int, service: str) -> list[dict]:
        entries: list[dict] = []
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
            entries.append({
                "targets": [f"{ip}:{port}"],
                "labels": {
                    "instance": name, "host": name, "vm_role": name,
                    "node": default_node, "env": "prod", "service": service,
                },
            })
        return entries

    def _parse_named(raw: str) -> list[tuple[str, str]]:
        """Split 'name:rest' pairs on the first colon, preserving any colons in `rest`."""
        pairs: list[tuple[str, str]] = []
        for pair in raw.split(","):
            pair = pair.strip()
            if ":" not in pair:
                continue
            name, rest = pair.split(":", 1)
            name, rest = name.strip(), rest.strip()
            if name and rest:
                pairs.append((name, rest))
        return pairs

    def _blackbox_entries(raw: str, module: str) -> list[dict]:
        return [
            {"targets": [target], "labels": {"module": module, "probe_name": name}}
            for name, target in _parse_named(raw)
        ]

    raw = env.get("SCRAPE_TARGETS", "").strip()
    ne_entries = _parse_targets(raw, 9100, "node-exporter") if raw else []
    ca_entries = _parse_targets(raw, 8081, "cadvisor") if raw else []
    plex_entries = _parse_targets(env.get("PLEX_EXPORTER_TARGETS", ""), 9000, "plex-exporter")
    evpn_entries = _parse_targets(env.get("EXPRESSVPN_EXPORTER_TARGETS", ""), 9797, "expressvpn")
    scraparr_entries = _parse_targets(env.get("SCRAPARR_EXPORTER_TARGETS", ""), 7100, "scraparr")
    qbt_entries = _parse_targets(env.get("QBITTORRENT_EXPORTER_TARGETS", ""), 17871, "qbittorrent-exporter")
    sab_entries = _parse_targets(env.get("SABNZBD_EXPORTER_TARGETS", ""), 9707, "sabnzbd-exporter")

    file_map = {
        "scrape-targets-node-exporter.json": ne_entries,
        "scrape-targets-cadvisor.json": ca_entries,
        "scrape-targets-plex-exporter.json": plex_entries,
        "scrape-targets-expressvpn.json": evpn_entries,
        "scrape-targets-scraparr.json": scraparr_entries,
        "scrape-targets-qbittorrent.json": qbt_entries,
        "scrape-targets-sabnzbd.json": sab_entries,
    }
    for fname, entries in file_map.items():
        (targets_dir / fname).write_text(json.dumps(entries, indent=2) + "\n")

    # Blackbox probe targets: HTTP(S) (module http_2xx), TCP (tcp_connect),
    # ICMP (icmp), and DNS resolver (dns_udp) checks — feeds D03/D00 connectivity
    # panels. Always regenerated from env, same as the exporter target files above.
    bb_entries = (
        _blackbox_entries(env.get("BLACKBOX_HTTP_TARGETS", ""), "http_2xx")
        + _blackbox_entries(env.get("BLACKBOX_TCP_TARGETS", ""), "tcp_connect")
        + _blackbox_entries(env.get("BLACKBOX_ICMP_TARGETS", ""), "icmp")
        + _blackbox_entries(env.get("BLACKBOX_DNS_TARGETS", ""), "dns_udp")
    )
    bb_file = targets_dir / "blackbox-targets.json"
    bb_file.write_text(json.dumps(bb_entries, indent=2) + "\n")

    legacy = targets_dir / "scrape-targets.json"
    if legacy.is_file():
        legacy.unlink()

    total_remote = len(ne_entries)
    tracker.success(f"Scrape targets generated ({total_remote} remote VMs, {len(bb_entries)} blackbox probes)")


def _ensure_grafana_provisioning(config_base: Path, tracker: StepTracker) -> None:
    ds_dir = config_base / "grafana" / "provisioning" / "datasources"
    ds_dir.mkdir(parents=True, exist_ok=True)
    (ds_dir / "datasources.yml").write_text(
        """\
apiVersion: 1

datasources:
  - name: Prometheus
    uid: prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: true

  - name: Loki
    uid: loki
    type: loki
    access: proxy
    url: http://loki:3100
    editable: true
"""
    )

    dash_prov_dir = config_base / "grafana" / "provisioning" / "dashboards"
    dash_prov_dir.mkdir(parents=True, exist_ok=True)
    (dash_prov_dir / "provider.yml").write_text(
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

    json_dir = dash_prov_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)

    repo_dashboards = SCRIPT_DIR / "dashboards"
    if repo_dashboards.is_dir():
        copied = 0
        for src in sorted(repo_dashboards.glob("*.json")):
            dst = json_dir / src.name
            if not dst.is_file() or src.read_bytes() != dst.read_bytes():
                shutil.copy2(src, dst)
                copied += 1
        if copied:
            tracker.detail(f"Copied {copied} dashboard(s)")

    tracker.success("Grafana provisioning configured")


# ---------------------------------------------------------------------------
# Bootstrap steps
# ---------------------------------------------------------------------------

def bootstrap_steps(
    env: dict[str, str], config_base: Path, args,  # noqa: ANN001
) -> list[tuple[str, object]]:
    return [
        ("Checking Grafana password", lambda t: _validate_grafana_password(env, t, force=args.force)),
        ("Creating config directories", lambda t: (
            _ensure_config_directories(config_base, t),
            _check_disk_space(config_base, t),
            _fix_ownership(config_base, env, t),
        )),
        ("Generating Prometheus config", lambda t: _ensure_prometheus_config(env, config_base, t)),
        ("Generating Alertmanager config", lambda t: _ensure_alertmanager_config(env, config_base, t)),
        ("Generating Loki config", lambda t: _ensure_loki_config(env, config_base, t)),
        ("Generating Blackbox config", lambda t: _ensure_blackbox_config(config_base, t)),
        ("Generating scrape targets", lambda t: _generate_scrape_targets(env, config_base, t)),
        ("Configuring Grafana provisioning", lambda t: _ensure_grafana_provisioning(config_base, t)),
        ("Syncing alerting config (rules, ntfy templates)",
         lambda t: _copy_alerting_files(env, config_base, t)),
    ]


# ---------------------------------------------------------------------------
# Post-deploy
# ---------------------------------------------------------------------------


def _http_reload(url: str) -> tuple[bool, str]:
    """POST to a /-/reload endpoint, retrying briefly while the service settles."""
    last = "no attempt made"
    for attempt in range(_RELOAD_ATTEMPTS):
        try:
            req = urllib.request.Request(url, data=b"", method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    return True, ""
                last = f"HTTP {resp.status}"
        except (urllib.error.URLError, OSError) as exc:
            last = str(exc)
        if attempt < _RELOAD_ATTEMPTS - 1:
            time.sleep(_RELOAD_BACKOFF_S)
    return False, last


def post_deploy(env: dict[str, str], config_base: Path, tracker: StepTracker) -> None:
    """Make the alerting config bootstrap just wrote actually take effect.

    Nothing re-reads these files on its own: Prometheus does not watch its
    rule_files, Alertmanager does not watch its config, and Grafana reads file
    provisioning only at startup. Without this step, the very first deploy of the
    alerting feature works by accident — adding the rules/ volume mount forces
    Docker to recreate the container, which reloads everything — and then every
    *subsequent* rule edit loads nothing and reports success anyway. An alert rule
    you think you shipped but which was never loaded is worse than no rule at all.

    Only the services whose config actually changed are touched (see
    _mark_reload). Prometheus and Alertmanager take a /-/reload; Grafana and ntfy
    read their files at startup only, so they get a restart. The marker survives a
    failed reload so the next deploy retries it.
    """
    marker = config_base / _RELOAD_MARKER
    if not marker.is_file():
        tracker.success("Alerting config unchanged — no reload needed")
        return

    services = {s for s in marker.read_text().split() if s}
    reloaded: list[str] = []
    failures: list[str] = []

    for service, url in (
        ("prometheus", "http://localhost:9090/-/reload"),
        ("alertmanager", "http://localhost:9093/-/reload"),
    ):
        if service not in services:
            continue
        ok, err = _http_reload(url)
        if ok:
            reloaded.append(service)
        else:
            failures.append(f"{service} reload failed ({err})")

    # Grafana reads file-provisioning at startup; ntfy loads its webhook templates
    # at startup. Neither has a reload endpoint we can rely on, so restart them.
    # Skip ntfy when it is switched off — its compose overlay is then not part of
    # the project at all, and asking to restart it would error.
    restartable = [s for s in ("grafana", "ntfy") if s in services]
    if "ntfy" in restartable and env.get("ENABLE_NTFY", "1") != "1":
        restartable.remove("ntfy")

    if restartable:
        from scripts.homelab_bootstrap import build_compose_command

        compose_files = build_compose_command(SCRIPT_DIR, COMPOSE_OVERLAYS, env)
        result = subprocess.run(
            ["docker", "compose", *compose_files, "restart", *restartable],
            cwd=SCRIPT_DIR, capture_output=True, text=True,
        )
        if result.returncode == 0:
            reloaded.extend(restartable)
        else:
            failures.append(
                f"restart of {', '.join(restartable)} failed "
                f"({(result.stderr or '').strip()})"
            )

    if failures:
        # Deliberately loud: the stack is up, but it is running the *old* alerting
        # config while the repo says otherwise. Keep the marker so a re-deploy
        # retries rather than losing track of the pending change.
        tracker.fail(
            "Alerting config is STALE — " + "; ".join(failures)
            + ". The new rules/routes are on disk but not loaded; "
            "re-run the deploy, or restart the service(s) by hand."
        )
        return

    marker.unlink()
    tracker.success(f"Alerting config applied ({', '.join(reloaded)})")
