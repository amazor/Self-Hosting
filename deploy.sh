#!/usr/bin/env bash
# Homelab deploy orchestrator.
#
# Owns:
#   - stack-level validation before deploy
#   - running per-stack bootstrap scripts
#   - creating ~/STACK symlinks and shell helper functions
#   - docker compose up -d for selected stacks
#
# Does NOT own:
#   - role-specific provisioning details (kept in each stack bootstrap.sh)
#   - generating per-stack .env files from .env.example
#
# Usage examples:
#   ./deploy.sh core
#   ./deploy.sh media --non-interactive
#   ./deploy.sh all --default core

set -e

# ---------------------------
# constants and defaults
# ---------------------------
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -d "$DEPLOY_DIR/docker_compose" ]]; then
  REPO_ROOT="$DEPLOY_DIR"
else
  REPO_ROOT="$(cd "$DEPLOY_DIR/.." && pwd)"
fi

REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME="$(getent passwd "$REAL_USER" 2>/dev/null | cut -d: -f6)"
[[ -z "$REAL_HOME" ]] && REAL_HOME="$HOME"

STATE_DIR="$REAL_HOME/.homelab"
INSTALLED_DIR="$STATE_DIR/installed"
DEFAULT_FILE="$STATE_DIR/default"
BASHRC_D="$REAL_HOME/.bashrc.d"
STACK_FUNCTIONS="$BASHRC_D/stack-functions.sh"
BASHRC_MARKER="# homelab stack-functions"
STACK_FUNCTIONS_SOURCE="[[ -f ~/.bashrc.d/stack-functions.sh ]] && source ~/.bashrc.d/stack-functions.sh"

FORCE=0
NONINTERACTIVE=0
DEFAULT_STACK=""
DEPLOY_ALL=0
STACKS=()

# ---------------------------
# usage and argument parsing
# ---------------------------
usage() {
  cat <<EOF
Usage:
  $0 <stack> [flags]
  $0 all [stack1 stack2 ...] [flags]

Flags:
  --force                  Continue when overridable validation fails.
  --non-interactive, -y    Pass non-interactive mode to bootstrap scripts.
  --default <stack>        Set default 'stack' helper target.
  --help, -h               Show this help text.

Notes:
  - Each stack must already have a configured .env file.
  - Deploy validates required vars, then runs bootstrap and compose up -d.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --force)
        FORCE=1
        shift
        ;;
      --non-interactive|-y)
        NONINTERACTIVE=1
        shift
        ;;
      --default)
        shift
        if [[ $# -eq 0 ]]; then
          echo "--default requires a stack name." >&2
          usage >&2
          exit 1
        fi
        DEFAULT_STACK="$1"
        shift
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      all)
        DEPLOY_ALL=1
        shift
        ;;
      -*)
        echo "Unknown option: $1" >&2
        usage >&2
        exit 1
        ;;
      *)
        STACKS+=("$1")
        shift
        ;;
    esac
  done
}

collect_all_stacks_if_requested() {
  local name
  local d
  [[ $DEPLOY_ALL -eq 1 ]] || return 0
  [[ ${#STACKS[@]} -gt 0 ]] && return 0

  for d in "$REPO_ROOT/docker_compose"/*/; do
    [[ -f "${d}compose.yml" ]] || continue
    name="$(basename "$d")"
    STACKS+=("$name")
  done
}

# ---------------------------
# helper functions
# ---------------------------
_stack_dir() {
  local stack="$1"
  echo "$REPO_ROOT/docker_compose/$stack"
}

_is_placeholder_value() {
  local val="$1"
  [[ -z "$val" ]] && return 0
  [[ "$val" == CHANGE_ME* ]] && return 0
  [[ "$val" == "example.com" ]] && return 0
  [[ "$val" == *.example.com ]] && return 0
  return 1
}

_required_vars_for_stack() {
  local stack="$1"
  case "$stack" in
    media) echo "OPENVPN_USER OPENVPN_PASSWORD MEDIA_ROOT CONFIG_ROOT" ;;
    core) echo "AUTHENTIK_SECRET_KEY AUTHENTIK_POSTGRES_PASSWORD PUBLIC_BASE_DOMAIN AUTHENTIK_FQDN WHOAMI_FQDN" ;;
    *) echo "" ;;
  esac
}

# Read one variable from .env in a clean process so parent-shell variables
# and previously read stacks cannot contaminate deploy validation.
_env_var_from_file() {
  local env_file="$1"
  local var_name="$2"
  env -i bash -c '
    set -a
    source "$1" >/dev/null 2>&1 || true
    set +a
    key="$2"
    printf "%s" "${!key:-}"
  ' bash "$env_file" "$var_name"
}

_validate_stack_env() {
  local stack="$1"
  local dir="$(_stack_dir "$stack")"
  local env_file="$dir/.env"
  local var
  local val

  if [[ ! -f "$env_file" ]]; then
    echo "Missing .env for $stack. Create from $dir/.env.example, configure it, then re-run deploy." >&2
    return 1
  fi

  for var in $(_required_vars_for_stack "$stack"); do
    val="$(_env_var_from_file "$env_file" "$var")"
    if [[ -z "$val" ]]; then
      echo "Required var $var is unset in $env_file for stack $stack." >&2
      return 1
    fi
  done

  case "$stack" in
    core)
      for var in AUTHENTIK_SECRET_KEY AUTHENTIK_POSTGRES_PASSWORD PUBLIC_BASE_DOMAIN AUTHENTIK_FQDN WHOAMI_FQDN; do
        val="$(_env_var_from_file "$env_file" "$var")"
        if _is_placeholder_value "$val"; then
          echo "Required var $var is placeholder-like in $env_file for stack $stack." >&2
          return 1
        fi
      done
      ;;
    *) ;;
  esac
}

_validate_all_envs() {
  local failed=()
  local stack
  for stack in "${STACKS[@]}"; do
    if ! _validate_stack_env "$stack" >/dev/null 2>&1; then
      failed+=("$stack")
    fi
  done
  if [[ ${#failed[@]} -gt 0 ]]; then
    echo "Missing or invalid .env for: ${failed[*]}. Configure each stack .env and re-run deploy." >&2
    return 1
  fi
}

_build_media_compose_files() {
  local dir="$1"
  local env_file="$dir/.env"
  local files="-f $dir/compose.yml"

  local enable_buildarr_recyclarr
  local enable_cleanuparr
  local enable_sabnzbd
  local enable_bazarr
  local enable_ntfy

  if [[ -f "$env_file" ]]; then
    enable_buildarr_recyclarr="$(_env_var_from_file "$env_file" "ENABLE_BUILDARR_RECYCLARR")"
    enable_cleanuparr="$(_env_var_from_file "$env_file" "ENABLE_CLEANUPARR")"
    enable_sabnzbd="$(_env_var_from_file "$env_file" "ENABLE_SABNZBD")"
    enable_bazarr="$(_env_var_from_file "$env_file" "ENABLE_BAZARR")"
    enable_ntfy="$(_env_var_from_file "$env_file" "ENABLE_NTFY")"
  fi

  [[ "${enable_buildarr_recyclarr:-0}" = "1" ]] && files="$files -f $dir/compose.buildarr-recyclarr.yml"
  [[ "${enable_cleanuparr:-0}" = "1" ]] && files="$files -f $dir/compose.cleanuparr.yml"
  [[ "${enable_sabnzbd:-0}" = "1" ]] && files="$files -f $dir/compose.sabnzbd.yml"
  [[ "${enable_bazarr:-0}" = "1" ]] && files="$files -f $dir/compose.bazarr.yml"
  [[ "${enable_ntfy:-0}" = "1" ]] && files="$files -f $dir/compose.ntfy.yml"
  echo "$files"
}

_build_stack_compose_files() {
  local stack="$1"
  local dir="$2"
  case "$stack" in
    media) _build_media_compose_files "$dir" ;;
    *) echo "-f $dir/compose.yml" ;;
  esac
}

_run_stack_up() {
  local stack="$1"
  local dir="$2"
  local compose_files
  compose_files="$(_build_stack_compose_files "$stack" "$dir")"
  (cd "$dir" && docker compose $compose_files up -d)
}

_run_bootstrap() {
  local stack="$1"
  local script="$2"
  local mode="$3" # install|update
  local -a bootstrap_args=()

  [[ ! -x "$script" ]] && return 0

  # Updates stay non-interactive by default. First install follows caller flags.
  if [[ "$mode" = "update" ]] || [[ $NONINTERACTIVE -eq 1 ]]; then
    bootstrap_args+=(--non-interactive)
    export HOMELAB_DEPLOY=1
  fi
  [[ $FORCE -eq 1 ]] && bootstrap_args+=(--force)

  echo "Running bootstrap for $stack..."
  if ! "$script" "${bootstrap_args[@]}"; then
    echo "Bootstrap failed for $stack. Fix issues and re-run deploy." >&2
    unset HOMELAB_DEPLOY 2>/dev/null || true
    return 1
  fi
  unset HOMELAB_DEPLOY 2>/dev/null || true
}

_append_media_helper() {
  local dir="$1"
  cat >> "$STACK_FUNCTIONS" <<MEDIAEOF

media() {
  local dir="$dir"
  local arg1="\${1:-}"
  if [[ "\$arg1" = "boot" ]] || [[ "\$arg1" = "bootstrap" ]]; then
    local files="-f \$dir/compose.yml"
    [[ -f "\$dir/.env" ]] && source "\$dir/.env" 2>/dev/null
    [[ "\${ENABLE_BUILDARR_RECYCLARR:-0}" = "1" ]] && files="\$files -f \$dir/compose.buildarr-recyclarr.yml"
    (cd "\$dir" && docker compose \$files --profile bootstrap run --rm buildarr run) 2>/dev/null || true
    (cd "\$dir" && docker compose \$files --profile bootstrap run --rm recyclarr sync) 2>/dev/null || true
    return
  fi

  local compose_files="-f \$dir/compose.yml"
  if [[ -f "\$dir/.env" ]]; then
    source "\$dir/.env" 2>/dev/null
    [[ "\${ENABLE_BUILDARR_RECYCLARR:-0}" = "1" ]] && compose_files="\$compose_files -f \$dir/compose.buildarr-recyclarr.yml"
    [[ "\${ENABLE_CLEANUPARR:-0}" = "1" ]] && compose_files="\$compose_files -f \$dir/compose.cleanuparr.yml"
    [[ "\${ENABLE_SABNZBD:-0}" = "1" ]] && compose_files="\$compose_files -f \$dir/compose.sabnzbd.yml"
    [[ "\${ENABLE_BAZARR:-0}" = "1" ]] && compose_files="\$compose_files -f \$dir/compose.bazarr.yml"
    [[ "\${ENABLE_NTFY:-0}" = "1" ]] && compose_files="\$compose_files -f \$dir/compose.ntfy.yml"
  fi
  (cd "\$dir" && docker compose \$compose_files "\$@")
}
MEDIAEOF
}

_append_generic_helper() {
  local name="$1"
  local dir="$2"
  echo "${name}() { (cd \"$dir\" && docker compose -f compose.yml \"\$@\"); }" >> "$STACK_FUNCTIONS"
}

_write_stack_functions() {
  local default_stack
  local name
  local dir
  local marker_file

  mkdir -p "$BASHRC_D"
  default_stack="$(cat "$DEFAULT_FILE" 2>/dev/null || true)"

  cat > "$STACK_FUNCTIONS" <<'STACKFUNCEOF'
# Homelab stack helpers — generated by deploy.sh. Do not edit by hand.
STACKFUNCEOF

  for marker_file in "$INSTALLED_DIR"/*; do
    [[ -f "$marker_file" ]] || continue
    name="$(basename "$marker_file")"
    dir="$REAL_HOME/$name"
    case "$name" in
      media) _append_media_helper "$dir" ;;
      *) _append_generic_helper "$name" "$dir" ;;
    esac
  done

  if [[ -n "$default_stack" ]]; then
    {
      echo ""
      echo "stack() { local d=\"$default_stack\"; [ -n \"\$d\" ] && \$d \"\$@\"; }"
    } >> "$STACK_FUNCTIONS"
  fi
}

_ensure_rc_sources() {
  local rc_file="$1"
  local rc_label="$2"

  if [[ ! -f "$rc_file" ]]; then
    printf "%s\n%s\n" "$BASHRC_MARKER" "$STACK_FUNCTIONS_SOURCE" >> "$rc_file"
    echo "Added stack helper source line to $rc_label."
    return 0
  fi

  if grep -qF "$BASHRC_MARKER" "$rc_file" 2>/dev/null; then
    return 0
  fi

  {
    echo ""
    echo "$BASHRC_MARKER"
    echo "$STACK_FUNCTIONS_SOURCE"
  } >> "$rc_file"
  echo "Added stack helper source line to $rc_label."
}

_ensure_shell_rc_sources() {
  local real_shell
  local bashrc="$REAL_HOME/.bashrc"
  local zshrc="$REAL_HOME/.zshrc"

  real_shell="$(getent passwd "$REAL_USER" 2>/dev/null | cut -d: -f7)"
  if [[ "$real_shell" == *"zsh" ]]; then
    _ensure_rc_sources "$zshrc" ".zshrc"
  elif [[ "$real_shell" == *"bash" ]]; then
    _ensure_rc_sources "$bashrc" ".bashrc"
  else
    _ensure_rc_sources "$bashrc" ".bashrc"
    _ensure_rc_sources "$zshrc" ".zshrc"
  fi
}

_set_default_stack_if_needed() {
  local stack="$1"
  mkdir -p "$STATE_DIR"

  if [[ ! -s "$DEFAULT_FILE" ]]; then
    echo "$stack" > "$DEFAULT_FILE"
    echo "Set default stack to $stack."
    return 0
  fi

  if [[ -n "$DEFAULT_STACK" ]] && [[ "$DEFAULT_STACK" = "$stack" ]]; then
    echo "$stack" > "$DEFAULT_FILE"
  fi
}

_deploy_one() {
  local stack="$1"
  local stack_dir="$(_stack_dir "$stack")"
  local env_file="$stack_dir/.env"
  local link_path="$REAL_HOME/$stack"
  local bootstrap_script="$stack_dir/bootstrap.sh"
  local deploy_mode="install"

  if [[ -f "$INSTALLED_DIR/$stack" ]]; then
    deploy_mode="update"
    echo "Stack $stack already installed; updating (bootstrap, compose up -d, shell helpers)."
  fi

  if [[ ! -f "$env_file" ]]; then
    echo "No .env found. Create .env from .env.example in $stack_dir, configure it, then re-run deploy." >&2
    return 1
  fi

  if ! _validate_stack_env "$stack" 2>/dev/null; then
    if [[ $FORCE -eq 1 ]]; then
      echo "Validation failed for $stack; continuing with --force."
    else
      _validate_stack_env "$stack"
      return 1
    fi
  fi

  _run_bootstrap "$stack" "$bootstrap_script" "$deploy_mode" || return 1

  mkdir -p "$INSTALLED_DIR"
  if [[ ! -L "$link_path" ]] && [[ ! -d "$link_path" ]]; then
    ln -sfn "$stack_dir" "$link_path"
    echo "Created symlink $link_path -> $stack_dir"
  elif [[ -L "$link_path" ]]; then
    ln -sfn "$stack_dir" "$link_path"
  fi
  touch "$INSTALLED_DIR/$stack"

  _set_default_stack_if_needed "$stack"

  echo "Starting $stack stack..."
  _run_stack_up "$stack" "$stack_dir"
  _write_stack_functions
}

# ---------------------------
# main
# ---------------------------
parse_args "$@"
collect_all_stacks_if_requested

if [[ ${#STACKS[@]} -eq 0 ]]; then
  echo "No stack selected." >&2
  usage >&2
  exit 1
fi

if [[ ${#STACKS[@]} -gt 1 ]]; then
  _validate_all_envs || exit 1
fi

for stack in "${STACKS[@]}"; do
  if [[ ! -f "$(_stack_dir "$stack")/compose.yml" ]]; then
    echo "No such stack: $stack (missing docker_compose/$stack/compose.yml)." >&2
    exit 1
  fi
  _deploy_one "$stack" || exit 1
done

_ensure_shell_rc_sources
echo ""
echo "Deploy done. Source your shell rc file (e.g. ~/.bashrc or ~/.zshrc), or open a new shell."
echo "Then use stack helpers like: core up -d | logs -f | down, media up -d, stack ps."
