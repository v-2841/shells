#!/usr/bin/env bash
set -euo pipefail

sudo dnf install -y fish

fish -c '
printf "y\n" | fish_config prompt save terlar
printf "y\n" | fish_config theme save "ayu Dark"
alias --save up "sudo dnf update --refresh"
alias --save grub-update "sudo grub2-mkconfig -o /boot/grub2/grub.cfg"
set -U fish_prompt_pwd_dir_length 0
fish_update_completions
'

FISH_PATH="$(command -v fish)"
chsh -s "$FISH_PATH" "$USER"
