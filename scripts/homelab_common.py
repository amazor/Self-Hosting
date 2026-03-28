"""Shared helpers for homelab bootstrap and deploy scripts."""

from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

log = logging.getLogger("homelab")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class _Formatter(logging.Formatter):
    """Plain for INFO, prefixed for WARNING/ERROR/DEBUG."""

    def format(self, record: logging.LogRecord) -> str:
        if record.levelno >= logging.WARNING:
            return f"[{record.levelname}] {record.getMessage()}"
        if record.levelno <= logging.DEBUG:
            return f"[DEBUG] {record.getMessage()}"
        return record.getMessage()


def setup_logging(*, verbose: bool = False) -> None:
    """Configure console logging with a consistent homelab format."""
    logger = logging.getLogger("homelab")
    if logger.handlers:
        return
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setFormatter(_Formatter())
    handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.addHandler(handler)


# ---------------------------------------------------------------------------
# .env helpers
# ---------------------------------------------------------------------------

_PLACEHOLDER_VALUES = frozenset({"example.com", "0.0.0.0"})
_PLACEHOLDER_SUFFIXES = (".example.com",)
_PLACEHOLDER_SUBSTRINGS = ("MONITORING_VM_IP",)


def is_placeholder(val: str | None) -> bool:
    """Return True if *val* is empty or looks like an unfilled placeholder."""
    if not val:
        return True
    if val.startswith("CHANGE_ME"):
        return True
    if "${" in val:
        return True
    if val in _PLACEHOLDER_VALUES:
        return True
    if any(val.endswith(s) for s in _PLACEHOLDER_SUFFIXES):
        return True
    return any(s in val for s in _PLACEHOLDER_SUBSTRINGS)


def clear_env_var(env_file: Path, key: str) -> bool:
    """Set *key* to empty in *env_file*, preserving file structure.

    Finds the first non-comment line whose key matches and replaces the value
    with an empty string (``KEY=``).  Returns True if the key was found and
    cleared, False if it was not present or already empty.
    """
    if not env_file.is_file():
        return False
    lines = env_file.read_text().splitlines(keepends=True)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, _, v = stripped.partition("=")
        if k.strip() != key:
            continue
        v = v.strip().strip("\"'")
        if not v:
            return False
        lines[i] = f"{key}=\n"
        env_file.write_text("".join(lines))
        return True
    return False


def set_env_var(env_file: Path, key: str, value: str) -> bool:
    """Set *key* to *value* in *env_file*, preserving file structure.

    Finds the first line whose key matches (commented or not) and replaces it
    with ``KEY=value``.  If the key is not found, appends it.  Returns True if
    the file was modified, False if the key already had the target value.
    """
    import re

    if not env_file.is_file():
        env_file.write_text(f"{key}={value}\n")
        return True

    lines = env_file.read_text().splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        commented = re.match(rf"^#\s*{re.escape(key)}\s*=(.*)", stripped)
        uncommented = re.match(rf"^{re.escape(key)}\s*=(.*)", stripped)
        if commented or uncommented:
            existing = (uncommented or commented).group(1).strip().strip("\"'")
            if uncommented and existing == value:
                return False
            lines[i] = f"{key}={value}"
            env_file.write_text("\n".join(lines) + "\n")
            return True

    lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n")
    return True


def load_env(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict (comments and blanks are skipped).

    Handles ``KEY=VALUE``, optional surrounding quotes, and empty values.
    ``${VAR}`` expansion is supported using any variable defined in the same
    file regardless of ordering.  Circular references are left unexpanded.
    """
    _var_re = re.compile(r"\$\{([^}]+)\}")
    raw_env: dict[str, str] = {}
    if not path.is_file():
        return raw_env
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        raw_env[key] = value

    def _expand(value: str, resolving: frozenset[str] = frozenset()) -> str:
        def _replacer(m: re.Match) -> str:
            name = m.group(1)
            if name in resolving or name not in raw_env:
                return m.group(0)
            return _expand(raw_env[name], resolving | {name})
        return _var_re.sub(_replacer, value)

    return {k: _expand(v, frozenset({k})) for k, v in raw_env.items()}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def resolve_config_base(config_root: str, script_dir: Path) -> Path:
    """Resolve CONFIG_ROOT to an absolute path relative to *script_dir*."""
    p = Path(config_root)
    if not p.is_absolute():
        p = script_dir / p
    return p.resolve()


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------


def require_docker() -> None:
    """Exit if Docker Engine or the Compose v2 plugin is unavailable."""
    if not shutil.which("docker"):
        log.error("Docker is not installed. Install Docker Engine first.")
        raise SystemExit(1)

    try:
        subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        log.error(
            "docker compose plugin not found. Install Docker Compose v2 plugin."
        )
        raise SystemExit(1)

    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True)
    except subprocess.CalledProcessError:
        log.error(
            "Cannot connect to the Docker daemon (permission denied on socket).\n"
            "Add your user to the docker group and start a new login session:\n"
            "  sudo usermod -aG docker $USER\n"
            "  # then log out and back in, or run: newgrp docker"
        )
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# System / user helpers
# ---------------------------------------------------------------------------


def get_real_user() -> tuple[str, Path]:
    """Return (username, home_dir) for the real (pre-sudo) user."""
    user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"
    try:
        result = subprocess.run(
            ["getent", "passwd", user],
            capture_output=True,
            text=True,
            check=True,
        )
        home = result.stdout.strip().split(":")[5]
    except (subprocess.CalledProcessError, IndexError):
        home = os.environ.get("HOME", f"/home/{user}")
    return user, Path(home)


def try_chown(
    path: Path, uid: int, gid: int, *, recursive: bool = False
) -> bool:
    """Attempt ``chown``; return True on success."""
    cmd = ["chown"]
    if recursive:
        cmd.append("-R")
    cmd += [f"{uid}:{gid}", str(path)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, PermissionError, FileNotFoundError):
        return False


def try_sudo_chown(
    path: Path, uid: int, gid: int, *, recursive: bool = False
) -> bool:
    """Attempt ``sudo -n chown``; return True on success."""
    cmd = ["sudo", "-n", "chown"]
    if recursive:
        cmd.append("-R")
    cmd += [f"{uid}:{gid}", str(path)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, PermissionError, FileNotFoundError):
        return False


def get_user_shell(user: str) -> str:
    """Return the login shell for *user* (e.g. '/bin/bash')."""
    try:
        result = subprocess.run(
            ["getent", "passwd", user],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip().split(":")[-1]
    except (subprocess.CalledProcessError, IndexError):
        return "/bin/bash"


# ---------------------------------------------------------------------------
# VM identity helpers (used by observability config generation)
# ---------------------------------------------------------------------------


def detect_hostname() -> str:
    """Return the system hostname for labeling."""
    return socket.gethostname()


def read_proxmox_node() -> str | None:
    """Read Proxmox node name written by cloud-init pre-start hook.

    Returns None if file missing or unreadable.
    """
    path = Path("/etc/homelab/proxmox-node")
    if not path.is_file():
        return None
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


def resolve_vm_identity(
    env: dict[str, str], script_dir: Path
) -> tuple[str, str, str]:
    """Return (hostname, vm_role, node) from env with sensible fallbacks.

    Resolution order per field:
      hostname — VM_HOSTNAME env var → socket.gethostname()
      vm_role  — VM_ROLE env var → script_dir.name (stack directory)
      node     — PROXMOX_NODE env var → /etc/homelab/proxmox-node file → "pve1"
    """
    hostname = env.get("VM_HOSTNAME") or detect_hostname()
    vm_role = env.get("VM_ROLE") or script_dir.name
    node = env.get("PROXMOX_NODE") or read_proxmox_node() or "pve1"
    return hostname, vm_role, node


# ---------------------------------------------------------------------------
# Observability sidecar config (Alloy)
# ---------------------------------------------------------------------------


_ALLOY_CONFIG_TEMPLATE = """\
// ============================================================================
// Homelab Alloy (production-grade pattern)
// Goals:
// - Strict label contract (logs + metrics aligned where possible)
// - Clean drilldowns: Overview -> vm_role -> host -> service -> container -> logs
// - Dynamic dashboards (no hardcoded VM names)
// - Log normalization at ingestion (levels)
// - Multi-node ready (node label)
// - Compatibility labels preserved (job, instance)
//
// TODO (future phase): Distributed tracing
// - Add correlation IDs / trace IDs via app instrumentation (OpenTelemetry or X-Request-ID)
// - Then *extract* request_id/trace_id/span_id fields in loki.process (do NOT label them)
// - Optional: add Tempo + OTLP pipeline in Alloy for trace<->log linking in Grafana
// ============================================================================

// Identity values are inlined by bootstrap — see setup_observability_config()
// in scripts/homelab_common.py.  VM_ROLE, PROXMOX_NODE, and hostname are
// resolved at generation time.

// ----------------------------------------------------------------------------
// Alloy self-metrics -> Prometheus remote_write
// ----------------------------------------------------------------------------
prometheus.exporter.self "alloy_health" {{}}

discovery.relabel "alloy_health" {{
  targets = prometheus.exporter.self.alloy_health.targets

  rule {{
    target_label = "instance"
    replacement  = "{hostname}"
  }}

  // Align with contract for metrics too (helps join concepts across signals)
  rule {{
    target_label = "host"
    replacement  = "{hostname}"
  }}
  rule {{
    target_label = "vm_role"
    replacement  = "{vm_role}"
  }}
  rule {{
    target_label = "node"
    replacement  = "{node}"
  }}
  rule {{
    target_label = "env"
    replacement  = "prod"
  }}

  rule {{
    target_label = "container"
    replacement  = "alloy"
  }}
  rule {{
    target_label = "service"
    replacement  = "alloy"
  }}
}}

prometheus.scrape "alloy_health" {{
  targets    = discovery.relabel.alloy_health.output
  job_name   = "integrations/alloy"
  forward_to = [prometheus.remote_write.default.receiver]
}}

// NOTE: This assumes Prometheus is running with remote_write receiver enabled.
// If you later move to Mimir/VM/Thanos receiver, update endpoint accordingly.
prometheus.remote_write "default" {{
  endpoint {{
    url = "{prometheus_url}/api/v1/write"
  }}
}}

// ----------------------------------------------------------------------------
// Logs: Docker -> Loki
// Contract-required labels on every log stream:
//   node, host, vm_role, service, container, source
//
// Strongly recommended:
//   env, compose_project, image, stream
//
// Compatibility (do not treat as canonical):
//   job = service
//   instance = host
// ----------------------------------------------------------------------------
discovery.docker "local" {{
  host = "unix:///var/run/docker.sock"
}}

// Relabel processes real targets and outputs a target set.
// This removes rules-only ambiguity and allows earlier validation of relabel logic.
discovery.relabel "docker_contract" {{
  targets = discovery.docker.local.targets

  // --- Contract base labels (always present) ---
  rule {{
    target_label = "node"
    replacement  = "{node}"
  }}
  rule {{
    target_label = "host"
    replacement  = "{hostname}"
  }}
  rule {{
    target_label = "vm_role"
    replacement  = "{vm_role}"
  }}
  rule {{
    target_label = "env"
    replacement  = "prod"
  }}
  rule {{
    target_label = "source"
    replacement  = "docker"
  }}

  // Compatibility: many Loki dashboards expect these
  rule {{
    target_label = "instance"
    replacement  = "{hostname}"
  }}

  // --- Strongly recommended metadata ---
  // Compose project (stack-ish)
  rule {{
    source_labels = ["__meta_docker_container_label_com_docker_compose_project"]
    regex         = "(.+)"
    target_label  = "compose_project"
  }}

  // Optional but useful: compose container number (low cardinality, helps when scaled)
  rule {{
    source_labels = ["__meta_docker_container_label_com_docker_compose_container_number"]
    regex         = "(.+)"
    target_label  = "compose_container_number"
  }}

  // Docker image (sanitized: strip digest if present to reduce churn)
  // Avoid repo@sha256:... churn; keep repo[:tag]
  rule {{
    source_labels = ["__meta_docker_container_image"]
    regex         = "^([^@]+)(?:@.+)?$"
    replacement   = "$1"
    target_label  = "image"
  }}

  // stdout/stderr stream if present (may be absent depending on SD implementation)
  rule {{
    source_labels = ["__meta_docker_container_log_stream"]
    regex         = "(.+)"
    target_label  = "stream"
  }}

  // --------------------------------------------------------------------------
  // Canonical identity extraction (no intermediate labels emitted)
  //
  // Priority order:
  //   1) Explicit override label: com.homelab.service
  //   2) Compose service label: com.docker.compose.service
  //   3) Parsed from container name: project-service-1 / project_service_1
  //   4) Final fallback: service = container
  //
  // Also:
  //   service   = stable logical identity
  //   container = actual container instance identity (human-readable)
  // --------------------------------------------------------------------------

  // container: actual container instance identity (strip leading '/')
  rule {{
    source_labels = ["__meta_docker_container_name"]
    regex         = "^/(.+)$"
    replacement   = "$1"
    target_label  = "container"
  }}

  // (1) Explicit override label (reserved for future use)
  // Example: label com.homelab.service=sonarr to force service identity.
  rule {{
    source_labels = ["__meta_docker_container_label_com_homelab_service"]
    regex         = "(.+)"
    target_label  = "service"
  }}

  // (2) Compose service label (preferred; stable) — fill only if service is empty
  rule {{
    source_labels = ["service", "__meta_docker_container_label_com_docker_compose_service"]
    regex         = "^$;(.+)"
    replacement   = "$1"
    target_label  = "service"
  }}

  // (3) Parse from container name ONLY if service is empty AND container matches.
  // Matches: project-service-1 OR project_service_1 (conservative on purpose)
  rule {{
    source_labels = ["service", "container"]
    regex         = "^$;(.+?)[-_]([A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*)[-_](\\\\d+)$"
    replacement   = "$2"
    target_label  = "service"
  }}

  // (4) Final fallback: service = container (guarantee non-empty)
  rule {{
    source_labels = ["service", "container"]
    regex         = "^$;(.+)"
    replacement   = "$1"
    target_label  = "service"
  }}

  // Compatibility alias (DO NOT use as canonical in new dashboards)
  rule {{
    source_labels = ["service"]
    target_label  = "job"
  }}
}}

// Use relabeled output targets rather than raw discovery targets.
loki.source.docker "docker_logs" {{
  host       = "unix:///var/run/docker.sock"
  targets    = discovery.relabel.docker_contract.output
  forward_to = [loki.process.normalize.receiver]
}}

// ----------------------------------------------------------------------------
// Log normalization pipeline
// - Parse JSON first (high confidence)
// - Parse logfmt second
// - Regex fallback last (guarded patterns)
// - Canonicalize to: trace, debug, info, warn, error, fatal
//
// IMPORTANT: Do NOT turn trace_id/request_id into labels (future high-cardinality).
// ----------------------------------------------------------------------------
loki.process "normalize" {{

  // --- (1) Try JSON parsing ---
  stage.json {{
    expressions = {{
      level    = "level",
      lvl      = "lvl",
      severity = "severity",
      // future: trace_id = "trace_id", span_id = "span_id", request_id = "request_id"
    }}
  }}

  // --- (2) Try logfmt parsing (skip JSON / bare-equals lines to suppress parser noise) ---
  // Lines starting with {{ " [ or = are not logfmt — guard prevents noisy decode errors.
  // Lines containing " anywhere often break logfmt (e.g. JSON fragments, quoted messages).
  stage.match {{
    selector = `{{container=~".+"}} !~ "^[{{\\\"=\\\\[]" !~ \"\\\"\"
`
    stage.logfmt {{
      mapping = {{
        level    = "level",
        lvl      = "lvl",
        severity = "severity",
        // future: trace_id = "trace_id", span_id = "span_id", request_id = "request_id"
      }}
    }}
  }}

  // --- (2a) Per-container level extraction ---
  // These run before level_raw consolidation so their extracted `level` feeds step (3).
  // Each stage.match is a no-op on VMs where that container doesn't exist.

  // Postgres: "... [1] LOG:  message" / "FATAL:  ..." / "WARNING:  ..." etc.
  // Captures the severity word directly into `level`.
  stage.match {{
    selector = `{{service="authentik-postgresql"}}`
    stage.regex {{
      expression = `(?i)\\b(?P<level>log|fatal|warning|notice|debug|panic)\\s*:`
    }}
  }}

  // Redis: sigil-based levels in "PID:Role DD Mon YYYY HH:MM:SS.mmm <sigil> message"
  //   * = info (notice/ready), # = warn, . = debug, - = debug (verbose)
  // Regex extracts the sigil into redis_sig; template maps it to a level word.
  // Falls back to preserving any existing level if the sigil is absent.
  stage.match {{
    selector = `{{service="authentik-redis"}}`
    stage.regex {{
      expression = `\\d{{2}}:\\d{{2}}:\\d{{2}}\\.\\d{{3}} (?P<redis_sig>[*#.\\-]) `
    }}
    stage.template {{
      source   = "level"
      template = `{{{{ if .redis_sig }}}}{{{{ if eq .redis_sig "*" }}}}info{{{{ else if eq .redis_sig "#" }}}}warn{{{{ else }}}}debug{{{{ end }}}}{{{{ else }}}}{{{{ .level }}}}{{{{ end }}}}`
    }}
  }}

  // ExpressVPN (vpn): plain-text "2006-01-02T15:04:05Z INFO [module] message"
  // ISO timestamp anchor prevents false positives; no-op for JSON-format ExpressVPN logs
  // since stage.json already extracted level from those in step (1).
  stage.match {{
    selector = `{{service="vpn"}}`
    stage.regex {{
      expression = `(?i)T\\d{{2}}:\\d{{2}}:\\d{{2}}Z (?P<level>info|warn|warning|error|debug|fatal) `
    }}
  }}

  // --- (3) Normalize "raw" level candidate into a single key: level_raw ---
  stage.template {{
    source   = "level_raw"
    template = "{{{{ if .level }}}}{{{{ .level }}}}{{{{ else if .lvl }}}}{{{{ .lvl }}}}{{{{ else if .severity }}}}{{{{ .severity }}}}{{{{ end }}}}"
  }}

  // Lowercase it for consistency
  stage.template {{
    source   = "level_raw"
    template = "{{{{ ToLower .level_raw }}}}"
  }}

  // --- (4) Guarded regex fallback only if level_raw is empty ---
  stage.regex {{
    expression = `(?i)(?:\\b(?:level|lvl|severity)\\b\\s*[:=]\\s*"?(?P<level_fallback>trace|debug|info|warn|warning|error|err|fatal|critical|panic)"?|\\[(?P<level_bracket>trace|debug|info|warn|warning|error|err|fatal|critical|panic)\\])`
  }}

  stage.template {{
    source   = "level_raw"
    template = "{{{{ if .level_raw }}}}{{{{ .level_raw }}}}{{{{ else if .level_fallback }}}}{{{{ ToLower .level_fallback }}}}{{{{ else if .level_bracket }}}}{{{{ ToLower .level_bracket }}}}{{{{ end }}}}"
  }}

  // --- (5) Canonical mapping with safe default (never empty) ---
  // Aliases: warning→warn, err→error, critical/panic→fatal, log/notice→info (Postgres)
  stage.template {{
    source   = "level"
    template = `{{{{ if eq .level_raw "warning" }}}}warn{{{{ else if eq .level_raw "err" }}}}error{{{{ else if or (eq .level_raw "critical") (eq .level_raw "panic") }}}}fatal{{{{ else if or (eq .level_raw "log") (eq .level_raw "notice") }}}}info{{{{ else if or (eq .level_raw "trace") (eq .level_raw "debug") (eq .level_raw "info") (eq .level_raw "warn") (eq .level_raw "error") (eq .level_raw "fatal") }}}}{{{{ .level_raw }}}}{{{{ else }}}}unknown{{{{ end }}}}`
  }}

  // --- (6) Attach canonical level as a label (now always non-empty) ---
  stage.labels {{
    values = {{ level = "level" }}
  }}

  forward_to = [loki.write.local.receiver]
}}

loki.write "local" {{
  endpoint {{
    url = "{loki_url}/loki/api/v1/push"
  }}
}}
"""


def setup_observability_config(
    config_base: Path,
    env: dict[str, str],
    script_dir: Path,
) -> None:
    """Create Alloy config directories and generate config.alloy.

    Idempotent: creates directories, sets ownership, and writes config.alloy
    only if it does not already exist.  Called by each stack's bootstrap when
    ENABLE_OBSERVABILITY is on.
    """
    alloy_dir = config_base / "alloy"
    alloy_data = alloy_dir / "data"
    alloy_dir.mkdir(parents=True, exist_ok=True)
    alloy_data.mkdir(parents=True, exist_ok=True)

    # Use PUID/PGID from the stack env so ownership matches the container user.
    # os.getuid() would return 0 when bootstrap re-execs via sudo, leaving
    # alloy/data owned by root and causing Alloy's "permission denied" on startup.
    try:
        host_uid = int(env.get("PUID", "") or os.getuid())
        host_gid = int(env.get("PGID", "") or os.getgid())
    except ValueError:
        host_uid = os.getuid()
        host_gid = os.getgid()
    if not try_chown(alloy_data, host_uid, host_gid, recursive=True):
        try_sudo_chown(alloy_data, host_uid, host_gid, recursive=True)

    conf = alloy_dir / "config.alloy"
    if conf.is_file():
        log.info("config.alloy already exists; not overwriting.")
        return

    hostname, vm_role, node = resolve_vm_identity(env, script_dir)
    loki_url = env.get("LOKI_URL", "http://host.docker.internal:3100")
    prometheus_url = env.get("PROMETHEUS_URL", "http://host.docker.internal:9090")

    # Strip trailing slash to avoid double-slash in URL paths
    loki_url = loki_url.rstrip("/")
    prometheus_url = prometheus_url.rstrip("/")

    conf.write_text(
        _ALLOY_CONFIG_TEMPLATE.format(
            hostname=hostname,
            vm_role=vm_role,
            node=node,
            loki_url=loki_url,
            prometheus_url=prometheus_url,
        )
    )
    log.info(f"Created starter Alloy config: {conf}")
