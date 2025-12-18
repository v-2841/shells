# Shell installers (Fedora)

This repo contains scripts to install or remove tuned shells on Fedora.

### Requirements
- Fedora 38+ (`/etc/os-release` must report `ID=fedora`);
- sudo privileges (needed for package installation and `chsh`);

## Zsh installer (`zsh-install.sh`)
- installs any missing packages `zsh`, `git`, `curl`;
- installs or updates Oh My Zsh for the invoking user;
- installs or updates the `zsh-autosuggestions` and `zsh-syntax-highlighting` plugins;
- switches the theme to `gnzh`;
- manages a custom aliases block with system-maintenance helpers;
- sets the user’s login shell to `zsh`.

### Installation
Run the command under the user that should receive the Zsh setup. It downloads `zsh-install.sh` from GitHub and executes it:

```bash
curl -fsSL https://raw.githubusercontent.com/v-2841/zsh-install/main/zsh-install.sh | bash
```

The script prints the target user, home directory, and progress. Once it finishes, log out/in or open a new terminal so Zsh becomes the default shell.

### Uninstall
Pass the `uninstall` argument to remove `~/.oh-my-zsh`, optionally delete `~/.zshrc` (default: keep), and switch the login shell back to `bash`:

```bash
curl -fsSL https://raw.githubusercontent.com/v-2841/zsh-install/main/zsh-install.sh | bash -s -- uninstall
```

### Re-running
Running the installer again keeps existing settings intact while updating Oh My Zsh, the managed plugins, and the aliases block, so it is safe to re-run whenever you want to refresh the setup.

## Fish installer (`fish-install.sh`)
- installs `fish`;
- applies Ayu Dark через `fish_config theme save` (универсальные переменные);
- saves the Terlar prompt via `fish_config prompt save`;
- adds aliases `up`/`grub-update`, sets `fish_prompt_pwd_dir_length 0`, refreshes completions;
- sets the user’s login shell to `fish`.

### Installation
```bash
curl -fsSL https://raw.githubusercontent.com/v-2841/zsh-install/main/fish-install.sh | bash
```
