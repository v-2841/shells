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

sudo dnf install -y fish

run_as_target fish -c '
printf "y\n" | fish_config prompt save terlar
printf "y\n" | fish_config theme save "ayu Dark"
alias --save up "sudo dnf update --refresh"
alias --save grub-update "sudo grub2-mkconfig -o /boot/grub2/grub.cfg"
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
