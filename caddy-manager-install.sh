#!/usr/bin/env bash
set -euo pipefail

DEFAULT_BASE_URL="https://raw.githubusercontent.com/v-2841/shells/main"
BASE_URL="${CADDY_MANAGER_BASE_URL:-$DEFAULT_BASE_URL}"
PYTHON_URL="$BASE_URL/caddy-manager.py"

MANAGER_TARGET="/usr/local/sbin/caddy-manager"

if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
  BLUE=$'\033[38;5;75m'
  CYAN=$'\033[38;5;80m'
  GREEN=$'\033[38;5;78m'
  YELLOW=$'\033[38;5;221m'
  RED=$'\033[38;5;203m'
  BOLD=$'\033[1m'
  DIM=$'\033[2m'
  RESET=$'\033[0m'
else
  BLUE="" CYAN="" GREEN="" YELLOW="" RED="" BOLD="" DIM="" RESET=""
fi

info()    { printf '%s●%s %s\n' "$CYAN" "$RESET" "$*"; }
success() { printf '%s✓%s %s\n' "$GREEN" "$RESET" "$*"; }
warn()    { printf '%s!%s %s\n' "$YELLOW" "$RESET" "$*"; }
die()     { printf '%s✗%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

show_header() {
  printf '%s╭────────────────────────────────────────────────────────╮%s\n' "$BLUE" "$RESET"
  printf '%s│%s  %s%-54s%s%s│%s\n' \
    "$BLUE" "$RESET" "$BOLD" "Caddy Manager" "$RESET" "$BLUE" "$RESET"
  printf '%s╰────────────────────────────────────────────────────────╯%s\n' "$BLUE" "$RESET"
}

check_platform() {
  [[ -r /etc/os-release ]] || die "Не удалось определить систему: /etc/os-release отсутствует."
  # shellcheck disable=SC1091
  source /etc/os-release
  [[ "${ID:-}" == "fedora" ]] || \
    die "Поддерживается только Fedora. Обнаружено: ${PRETTY_NAME:-неизвестная система}."

  command -v python3 >/dev/null 2>&1 || \
    die "Python 3 не найден. Установите python3 и повторите запуск."
  python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 9))' || \
    die "Нужен Python 3.9 или новее. Обнаружено: $(python3 --version 2>&1)."
}

as_root() {
  if [[ "$EUID" -eq 0 ]]; then
    "$@"
  else
    command -v sudo >/dev/null 2>&1 || die "Нужны права root, но команда sudo не найдена."
    sudo -- "$@"
  fi
}

ask() {
  local prompt="$1" reply=""
  if [[ -r /dev/tty ]]; then
    read -r -p "$prompt" reply </dev/tty || reply=""
  else
    die "Для подтверждения удаления нужен интерактивный терминал."
  fi
  printf '%s' "$reply"
}

confirm() {
  local prompt="$1" default="${2:-yes}" reply marker
  if [[ "$default" == "yes" ]]; then
    marker="Y/n"
  else
    marker="y/N"
  fi
  reply="$(ask "$prompt [$marker] ")"
  reply="${reply,,}"
  if [[ -z "$reply" ]]; then
    [[ "$default" == "yes" ]]
  else
    [[ "$reply" == "y" || "$reply" == "yes" || "$reply" == "д" || "$reply" == "да" ]]
  fi
}

download() {
  local url="$1" target="$2"
  if [[ "$BASE_URL" == "$DEFAULT_BASE_URL" ]]; then
    curl --proto '=https' --tlsv1.2 -fsSL --retry 3 --retry-delay 1 "$url" -o "$target"
  else
    curl -fsSL --retry 3 --retry-delay 1 "$url" -o "$target"
  fi
}

fetch_manager() {
  local temp_dir="$1"
  info "Загружаю Caddy Manager из репозитория v-2841/shells…"
  download "$PYTHON_URL" "$temp_dir/caddy-manager.py"

  python3 - "$temp_dir/caddy-manager.py" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
compile(source, str(path), "exec")
if "APP_NAME = \"Caddy Manager\"" not in source:
    raise SystemExit("загружен неожиданный Python-файл")
PY
}

deploy_manager() {
  local action="install" temp_dir version
  if [[ -e "$MANAGER_TARGET" ]]; then
    action="update"
  fi
  temp_dir="$(mktemp -d -t caddy-manager-install.XXXXXXXX)"
  trap "rm -rf -- '$temp_dir'" EXIT

  fetch_manager "$temp_dir"
  version="$(python3 "$temp_dir/caddy-manager.py" --version | awk '{print $NF}')"

  as_root install -d -m 0755 /usr/local/sbin
  as_root install -m 0755 "$temp_dir/caddy-manager.py" /usr/local/sbin/.caddy-manager.new
  as_root mv -f /usr/local/sbin/.caddy-manager.new "$MANAGER_TARGET"

  if [[ "$action" == "install" ]]; then
    success "Caddy Manager $version установлен."
  else
    success "Caddy Manager обновлён до версии $version."
  fi
  printf '\nЗапуск: %scaddy-manager%s\n' "$BOLD" "$RESET"
  printf '%sСам Caddy этим действием не устанавливался и не изменялся.%s\n' "$DIM" "$RESET"
  rm -rf -- "$temp_dir"
  trap - EXIT
}

uninstall_manager() {
  if [[ ! -e "$MANAGER_TARGET" ]]; then
    warn "Caddy Manager уже удалён."
    return 0
  fi
  warn "Будет удалён только менеджер. Caddy, его служба, конфигурация и сертификаты сохранятся."
  confirm "Удалить Caddy Manager?" no || return 0
  as_root rm -f -- "$MANAGER_TARGET"
  success "Caddy Manager удалён. Caddy не изменялся."
}

main() {
  local action="${1:-}"
  show_header
  check_platform
  command -v curl >/dev/null 2>&1 || die "Команда curl не найдена."

  case "$action" in
    "") deploy_manager ;;
    uninstall) uninstall_manager ;;
    *) die "Неизвестное действие '$action'. Допустимо только: uninstall." ;;
  esac
}

main "$@"
