#!/usr/bin/env bash
set -euo pipefail

# Fedora-only guard
if [[ -f /etc/os-release ]]; then
  . /etc/os-release
  if [[ "${ID:-}" != "fedora" ]]; then
    echo "This script is Fedora-only. Detected ID=${ID:-unknown}" >&2
    exit 1
  fi
else
  echo "Cannot detect OS (missing /etc/os-release)." >&2
  exit 1
fi

ACTION="${1:-install}"
case "$ACTION" in
  install|uninstall) ;;
  -h|--help)
    cat <<'EOF'
Usage: zsh-install.sh [install|uninstall]
  install   Default action: install Oh My Zsh and configure plugins/theme.
  uninstall Remove Oh My Zsh data, optionally delete ~/.zshrc, switch shell back to bash.
EOF
    exit 0
    ;;
  *)
    echo "Unknown action: $ACTION" >&2
    echo "Usage: $0 [install|uninstall]" >&2
    exit 1
    ;;
esac

need_cmd() { command -v "$1" >/dev/null 2>&1; }

prompt_keep() {
  local prompt="$1" reply=""
  if [[ -t 0 ]]; then
    read -r -p "$prompt [Y/n] " reply || reply=""
  elif [[ -t 1 ]] && [[ -r /dev/tty ]]; then
    read -r -p "$prompt [Y/n] " reply </dev/tty || reply=""
  else
    echo "$prompt [Y/n] (default: Y)" >&2
    reply=""
  fi

  case "${reply,,}" in
    n|no) return 1 ;;
    *) return 0 ;;
  esac
}

run_as_target() {
  if [[ "$TARGET_USER" == "$USER" ]]; then
    "$@"
  else
    sudo -u "$TARGET_USER" "$@"
  fi
}

TARGET_USER="${SUDO_USER:-$USER}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
ZSHRC="${TARGET_HOME}/.zshrc"
OHMY_DIR="${TARGET_HOME}/.oh-my-zsh"
ZSH_CUSTOM="${ZSH_CUSTOM:-${OHMY_DIR}/custom}"
PLUGINS_DIR="${ZSH_CUSTOM}/plugins"

echo "Target user: ${TARGET_USER}"
echo "Home: ${TARGET_HOME}"

if [[ "$ACTION" == "uninstall" ]]; then
  BASH_PATH="$(command -v bash)"
  if [[ -z "$BASH_PATH" ]]; then
    echo "bash not found in PATH." >&2
    exit 1
  fi

  if [[ -d "$OHMY_DIR" ]]; then
    echo "Removing Oh My Zsh directory: $OHMY_DIR"
    run_as_target rm -rf "$OHMY_DIR"
  else
    echo "Oh My Zsh directory not found, skipping."
  fi

  if [[ -f "$ZSHRC" ]]; then
    if prompt_keep "Keep $ZSHRC?"; then
      echo "Keeping $ZSHRC"
    else
      echo "Removing $ZSHRC"
      run_as_target rm -f "$ZSHRC"
    fi
  else
    echo "$ZSHRC not found, nothing to remove."
  fi

  CURRENT_SHELL="$(getent passwd "$TARGET_USER" | cut -d: -f7)"
  if [[ "$CURRENT_SHELL" == "$BASH_PATH" ]]; then
    echo "Default shell already set to bash: $BASH_PATH"
  else
    echo "Changing default shell to: $BASH_PATH"
    if [[ "$EUID" -eq 0 ]]; then
      chsh -s "$BASH_PATH" "$TARGET_USER"
    else
      sudo chsh -s "$BASH_PATH" "$TARGET_USER"
    fi
  fi

  echo
  echo "Uninstall complete."
  exit 0
fi

# 1) Packages (Fedora)
PKGS=()
need_cmd zsh  || PKGS+=("zsh")
need_cmd git  || PKGS+=("git")
need_cmd curl || PKGS+=("curl")
if ((${#PKGS[@]})); then
  sudo dnf -y install "${PKGS[@]}"
fi

ZSH_PATH="$(command -v zsh)"

# 2) Oh My Zsh (unattended)
if [[ ! -d "$OHMY_DIR" ]]; then
  echo "Installing Oh My Zsh..."
  sudo -u "$TARGET_USER" env HOME="$TARGET_HOME" \
    RUNZSH=no CHSH=no sh -c \
    'sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended'
else
  echo "Oh My Zsh already installed: $OHMY_DIR"
fi

# 3) Plugins
sudo -u "$TARGET_USER" mkdir -p "$PLUGINS_DIR"

clone_or_update() {
  local repo="$1" dest="$2"
  if [[ -d "$dest/.git" ]]; then
    echo "Updating $(basename "$dest")..."
    sudo -u "$TARGET_USER" git -C "$dest" pull --ff-only
  else
    echo "Cloning $(basename "$dest")..."
    sudo -u "$TARGET_USER" git clone --depth 1 "$repo" "$dest"
  fi
}

clone_or_update "https://github.com/zsh-users/zsh-autosuggestions"     "${PLUGINS_DIR}/zsh-autosuggestions"
clone_or_update "https://github.com/zsh-users/zsh-syntax-highlighting" "${PLUGINS_DIR}/zsh-syntax-highlighting"

# 4) Update ~/.zshrc: theme (+ blank line), plugins, aliases block (managed, no duplicates)
if [[ ! -f "$ZSHRC" ]]; then
  echo "ERROR: $ZSHRC not found (unexpected after OMZ install)." >&2
  exit 1
fi

sudo -u "$TARGET_USER" env ZSHRC="$ZSHRC" python3 - <<'PY'
import os, re

path = os.environ["ZSHRC"]
with open(path, "r", encoding="utf-8") as f:
    s = f.read()

# Ensure PATH export is uncommented if present
path_line = 'export PATH=$HOME/bin:$HOME/.local/bin:/usr/local/bin:$PATH'
s = re.sub(r'^\s*#?\s*export PATH=\$HOME/bin:\$HOME/\.local/bin:/usr/local/bin:\$PATH\s*$',
           path_line, s, flags=re.M)
s = re.sub(rf'^({re.escape(path_line)})\s*\n*', r'\1\n\n', s, flags=re.M)

# Theme -> gnzh
if re.search(r'^\s*ZSH_THEME\s*=\s*".*?"\s*$', s, flags=re.M):
    s = re.sub(r'^\s*ZSH_THEME\s*=\s*".*?"\s*$',
               'ZSH_THEME="gnzh"', s, flags=re.M)
else:
    s = s.rstrip() + '\nZSH_THEME="gnzh"\n'

# Ensure EXACTLY one empty line after theme line (i.e. "\n\n")
s = re.sub(r'^(ZSH_THEME="gnzh")\s*\n*', r'\1\n\n', s, flags=re.M)

# Plugins: keep existing, add missing. Ensure syntax-highlighting last.
need = ["zsh-autosuggestions", "zsh-syntax-highlighting"]

m = re.search(r'^\s*plugins=\((.*?)\)\s*$', s, flags=re.M|re.S)
if m:
    inside = m.group(1)
    current = re.findall(r"[^\s]+", inside)
else:
    current = ["git"]

for p in need:
    if p not in current:
        current.append(p)

if "zsh-syntax-highlighting" in current:
    current = [p for p in current if p != "zsh-syntax-highlighting"] + ["zsh-syntax-highlighting"]

new_plugins_line = "plugins=(" + " ".join(current) + ")"
if m:
    s = s[:m.start()] + new_plugins_line + s[m.end():]
else:
    s += "\n" + new_plugins_line + "\n"

# Managed aliases block (aliases go consecutively)
begin = "# Custom aliases"
block = "\n\n".join([
    begin,
    'alias up="sudo dnf update --refresh"',
    'alias grub-update="sudo grub2-mkconfig -o /boot/grub2/grub.cfg"',
]) + "\n"

pattern = re.compile(rf'(?ms)^\s*{re.escape(begin)}\s*\n(?:alias[^\n]*\n)+')
if pattern.search(s):
    s = pattern.sub(block, s)
else:
    s = s.rstrip() + "\n\n" + block

with open(path, "w", encoding="utf-8") as f:
    f.write(s)

print("Updated:", path)
print("Theme: gnzh (+ blank line after)")
print("Plugins:", " ".join(current))
print("Aliases block ensured.")
PY

# 5) Change default shell to zsh
CURRENT_SHELL="$(getent passwd "$TARGET_USER" | cut -d: -f7)"
if [[ "$CURRENT_SHELL" == "$ZSH_PATH" ]]; then
  echo "Default shell already set to zsh: $ZSH_PATH"
else
  echo "Changing default shell to: $ZSH_PATH"
  if [[ "$EUID" -eq 0 ]]; then
    chsh -s "$ZSH_PATH" "$TARGET_USER"
  else
    sudo chsh -s "$ZSH_PATH" "$TARGET_USER"
  fi
fi

echo
echo "Done. Re-login (or open a new terminal) to start using zsh as default."
