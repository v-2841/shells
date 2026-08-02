# Fish installer (Fedora)

This repo contains a script that installs and configures Fish on Fedora.

### Requirements
- Fedora 38+ (`/etc/os-release` must report `ID=fedora`);
- sudo privileges (needed for package installation and `chsh`);

## Configuration
- installs `fish`;
- applies Ayu Dark через `fish_config theme save` (универсальные переменные);
- saves the Terlar prompt via `fish_config prompt save`;
- adds aliases `up` and `upp`;
- asks whether to save the `grub-update` alias (`Y` is the default when Enter is pressed);
- sets `fish_prompt_pwd_dir_length 0` and refreshes completions;
- sets the user’s login shell to `fish`.

## Installation
```bash
curl -fsSL https://raw.githubusercontent.com/v-2841/shells/main/fish-install.sh | bash
```
