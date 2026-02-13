#!/usr/bin/env bash
# Homelab deploy — orchestrates stack deployment (state, symlink, bootstrap, compose up, shell helpers).
# Run from repo root: ./deploy.sh <stack>  or  ./deploy.sh all [--default <stack>]
# Requires: .env exists per stack directory (create from .env.example; deploy does not copy it).
# Flags: --force (skip overridable checks), --non-interactive / -y (bootstrap skips prompts).

set -e

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

FORCE=0
NONINTERACTIVE=0
DEFAULT_STACK=""
STACKS=()

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
    core)  echo "AUTHENTIK_SECRET_KEY AUTHENTIK_POSTGRES_PASSWORD PUBLIC_BASE_DOMAIN AUTHENTIK_FQDN WHOAMI_FQDN" ;;
    *)     echo "" ;;
  esac
}

# Deployment-level validation only checks that required env inputs exist and look intentional.
# Deep role-specific provisioning remains in each stack bootstrap script.
_validate_stack_env() {
  local stack="$1"
  local dir
  dir="$(_stack_dir "$stack")"
  local env_file="$dir/.env"
  local var val

  if [[ ! -f "$env_file" ]]; then
    echo "Missing .env for $stack (create from $dir/.env.example in $dir, configure, then re-run deploy)." >&2
    return 1
  fi

  set -a
  # shellcheck source=/dev/null
  source "$env_file" 2>/dev/null || true
  set +a

  for var in $(_required_vars_for_stack "$stack"); do
    val="${!var:-}"
    if [[ -z "$val" ]]; then
      echo "Required var $var is unset in $env_file for stack $stack." >&2
      return 1
    fi
  done

  case "$stack" in
    core)
      for var in AUTHENTIK_SECRET_KEY AUTHENTIK_POSTGRES_PASSWORD PUBLIC_BASE_DOMAIN AUTHENTIK_FQDN WHOAMI_FQDN; do
        val="${!var:-}"
        if _is_placeholder_value "$val"; then
          echo "Required var $var is placeholder-like in $env_file for stack $stack." >&2
          return 1
        fi
      done
      ;;
    *) ;;
  esac

  return 0
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
    echo "Missing or invalid .env for: ${failed[*]}. Create .env from .env.example in each stack directory, configure required vars, then re-run deploy." >&2
    return 1
  fi
  return 0
}

_build_media_compose_files() {
  local dir="$1"
  local files="-f $dir/compose.yml"
  if [[ -f "$dir/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$dir/.env" 2>/dev/null || true
    set +a
  fi
  [[ "${ENABLE_BUILDARR_RECYCLARR:-0}" = "1" ]] && files="$files -f $dir/compose.buildarr-recyclarr.yml"
  [[ "${ENABLE_CLEANUPARR:-0}" = "1" ]]         && files="$files -f $dir/compose.cleanuparr.yml"
  [[ "${ENABLE_SABNZBD:-0}" = "1" ]]            && files="$files -f $dir/compose.sabnzbd.yml"
  [[ "${ENABLE_BAZARR:-0}" = "1" ]]             && files="$files -f $dir/compose.bazarr.yml"
  [[ "${ENABLE_NTFY:-0}" = "1" ]]               && files="$files -f $dir/compose.ntfy.yml"
  echo "$files"
}

_build_stack_compose_files() {
  local stack="$1"
  local dir="$2"
  case "$stack" in
    media) _build_media_compose_files "$dir" ;;
    *)     echo "-f $dir/compose.yml" ;;
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
  local mode="$3" # "install" or "update"
  local -a bootstrap_args=()

  [[ ! -x "$script" ]] && return 0

  # Deploy remains non-interactive on update. First install honors caller's mode.
  if [[ "$mode" = "update" ]]; then
    bootstrap_args+=(--non-interactive)
    export HOMELAB_DEPLOY=1
  elif [[ $NONINTERACTIVE -eq 1 ]]; then
    bootstrap_args+=(--non-interactive)
    export HOMELAB_DEPLOY=1
  fi
  [[ $FORCE -eq 1 ]] && bootstrap_args+=(--force)

  echo "Running bootstrap for $stack..."
  if ! "$script" "${bootstrap_args[@]}"; then
    echo "Bootstrap failed for $stack. Fix errors and re-run deploy." >&2
    unset HOMELAB_DEPLOY 2>/dev/null || true
    return 1
  fi
  unset HOMELAB_DEPLOY 2>/dev/null || true
  return 0
}

_append_media_helper() {
  local dir="$1"
  cat >> "$STACK_FUNCTIONS" << MEDIAEOF

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
    [[ "\${ENABLE_CLEANUPARR:-0}" = "1" ]]         && compose_files="\$compose_files -f \$dir/compose.cleanuparr.yml"
    [[ "\${ENABLE_SABNZBD:-0}" = "1" ]]            && compose_files="\$compose_files -f \$dir/compose.sabnzbd.yml"
    [[ "\${ENABLE_BAZARR:-0}" = "1" ]]             && compose_files="\$compose_files -f \$dir/compose.bazarr.yml"
    [[ "\${ENABLE_NTFY:-0}" = "1" ]]               && compose_files="\$compose_files -f \$dir/compose.ntfy.yml"
  fi
  (cd "\$dir" && docker compose \$compose_files "\$@")
}
MEDIAEOF
}

_append_generic_helper() {
  local name="$1"
  local dir="$2"
  echo "${name}() { (cd $dir && docker compose -f compose.yml \"\$@\"); }" >> "$STACK_FUNCTIONS"
}

_write_stack_functions() {
  mkdir -p "$BASHRC_D"
  local default_stack
  default_stack="$(cat "$DEFAULT_FILE" 2>/dev/null || true)"

  cat > "$STACK_FUNCTIONS" << 'STACKFUNCEOF'
# Homelab stack helpers — generated by deploy.sh. Do not edit by hand.

STACKFUNCEOF

  for s in "$INSTALLED_DIR"/*; do
    [[ -f "$s" ]] || continue
    name="$(basename "$s")"
    dir="$REAL_HOME/$name"
    case "$name" in
      media) _append_media_helper "$dir" ;;
      *) _append_generic_helper "$name" "$dir" ;;
    esac
  done

  if [[ -n "$default_stack" ]]; then
    echo "" >> "$STACK_FUNCTIONS"
    echo "stack() { local d=\"$default_stack\"; [ -n \"\$d\" ] && \$d \"\$@\"; }" >> "$STACK_FUNCTIONS"
  fi
}

_ensure_bashrc_sources() {
  if [[ ! -f "$REAL_HOME/.bashrc" ]]; then
    printf "%s\n[[ -f ~/.bashrc.d/stack-functions.sh ]] && source ~/.bashrc.d/stack-functions.sh\n" "$BASHRC_MARKER" >> "$REAL_HOME/.bashrc"
    return
  fi
  if grep -qF "$BASHRC_MARKER" "$REAL_HOME/.bashrc" 2>/dev/null; then
    return
  fi
  echo "" >> "$REAL_HOME/.bashrc"
  echo "$BASHRC_MARKER" >> "$REAL_HOME/.bashrc"
  echo "[[ -f ~/.bashrc.d/stack-functions.sh ]] && source ~/.bashrc.d/stack-functions.sh" >> "$REAL_HOME/.bashrc"
  echo "Added source for stack-functions.sh to .bashrc."
}

_deploy_one() {
  local stack="$1"
  local stack_dir
  stack_dir="$(_stack_dir "$stack")"
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

  if [[ ! -s "$DEFAULT_FILE" ]]; then
    mkdir -p "$STATE_DIR"
    echo "$stack" > "$DEFAULT_FILE"
    echo "Set default stack to $stack."
  fi
  if [[ -n "$DEFAULT_STACK" ]] && [[ "$DEFAULT_STACK" = "$stack" ]]; then
    echo "$stack" > "$DEFAULT_FILE"
  fi

  echo "Starting $stack stack..."
  _run_stack_up "$stack" "$stack_dir"
  _write_stack_functions
  return 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --non-interactive|-y) NONINTERACTIVE=1; shift ;;
    --default)
      shift
      [[ $# -gt 0 ]] && DEFAULT_STACK="$1" && shift
      ;;
    all)
      STACKS=()
      shift
      while [[ $# -gt 0 ]] && [[ "$1" != --* ]]; do
        [[ "$1" = "--default" ]] && break
        STACKS+=("$1")
        shift
      done
      if [[ ${#STACKS[@]} -eq 0 ]]; then
        for d in "$REPO_ROOT/docker_compose"/*/; do
          [[ -f "${d}compose.yml" ]] || continue
          name="$(basename "$d")"
          STACKS+=("$name")
        done
      fi
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      STACKS+=("$1")
      shift
      ;;
  esac
done

if [[ ${#STACKS[@]} -eq 0 ]]; then
  echo "Usage: $0 <stack> | $0 all [--default <stack>]" >&2
  echo "  Flags: --force, --non-interactive | -y" >&2
  echo "  Example: $0 core   # or: $0 media" >&2
  exit 1
fi

if [[ ${#STACKS[@]} -gt 1 ]]; then
  if ! _validate_all_envs; then
    exit 1
  fi
fi

for stack in "${STACKS[@]}"; do
  if [[ ! -f "$(_stack_dir "$stack")/compose.yml" ]]; then
    echo "No such stack: $stack (missing docker_compose/$stack/compose.yml)." >&2
    exit 1
  fi
  _deploy_one "$stack" || exit 1
done

_ensure_bashrc_sources
echo ""
echo "Deploy done. Source ~/.bashrc (or open a new shell), then use stack helpers like: core up -d | logs -f | down, media up -d, stack ps."
