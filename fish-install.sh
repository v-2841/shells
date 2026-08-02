#!/usr/bin/env bash
set -euo pipefail

TARGET_USER="${SUDO_USER:-$USER}"

run_as_target() {
  if [[ "$TARGET_USER" == "$USER" ]]; then
    "$@"
  else
    sudo -u "$TARGET_USER" "$@"
  fi
}

prompt_yes_default() {
  local prompt="$1" reply=""

  if [[ -t 0 ]]; then
    read -r -p "$prompt [Y/n] " reply || reply=""
  elif [[ -t 1 ]] && [[ -r /dev/tty ]]; then
    read -r -p "$prompt [Y/n] " reply </dev/tty || reply=""
  else
    echo "$prompt [Y/n] (default: Y)" >&2
  fi

  case "${reply,,}" in
    n|no) return 1 ;;
    *) return 0 ;;
  esac
}

sudo dnf install -y fish fastfetch

SAVE_GRUB_ALIAS=0
if prompt_yes_default "Save the grub-update alias?"; then
  SAVE_GRUB_ALIAS=1
fi

run_as_target env SAVE_GRUB_ALIAS="$SAVE_GRUB_ALIAS" fish -c '
function fish_greeting
    fastfetch
end
funcsave fish_greeting
touch .hushlogin
mkdir -p ~/.local/bin
fish_add_path -m ~/.local/bin
printf "y\n" | fish_config prompt save terlar
printf "y\n" | fish_config theme save "ayu Dark"
alias --save up "sudo dnf update --refresh"
alias --save upp "up --setopt=max_parallel_downloads=9"
if test "$SAVE_GRUB_ALIAS" = 1
    alias --save grub-update "sudo grub2-mkconfig -o /boot/grub2/grub.cfg"
end
set -U fish_prompt_pwd_dir_length 0
fish_update_completions
'

FISH_PATH="$(command -v fish)"
CURRENT_SHELL="$(getent passwd "$TARGET_USER" | cut -d: -f7)"
if [[ "$CURRENT_SHELL" != "$FISH_PATH" ]]; then
  if [[ "$EUID" -eq 0 ]]; then
    chsh -s "$FISH_PATH" "$TARGET_USER"
  else
    sudo chsh -s "$FISH_PATH" "$TARGET_USER"
  fi
fi
