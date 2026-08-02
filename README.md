# Shell tools for Fedora

В репозитории находятся установщик Fish и интерактивный менеджер Caddy для Fedora.

## Fish

`fish-install.sh`:

- устанавливает `fish` и `fastfetch`;
- применяет тему Ayu Dark и приглашение Terlar;
- добавляет алиасы `up` и `upp`;
- спрашивает, сохранять ли алиас `grub-update` (Enter означает «да»);
- настраивает дополнения Fish и делает его login shell текущего пользователя.

Установка:

```bash
curl -fsSL https://raw.githubusercontent.com/v-2841/shells/main/fish-install.sh | bash
```

## Caddy Manager

`caddy-manager` — терминальное меню для установки и обслуживания статических
сборок Caddy.

Требования:

- Fedora (`ID=fedora` в `/etc/os-release`);
- Python 3.9 или новее;
- права `sudo` для системных изменений.

### Установка, обновление и удаление менеджера

Одна команда устанавливает Caddy Manager или обновляет уже установленную версию:

```bash
curl -fsSL https://raw.githubusercontent.com/v-2841/shells/main/caddy-manager-install.sh | bash
```

Удаление менеджера:

```bash
curl -fsSL https://raw.githubusercontent.com/v-2841/shells/main/caddy-manager-install.sh | bash -s -- uninstall
```

После установки:

```bash
caddy-manager
```

Пункты меню выбираются одной цифрой, без нажатия Enter.

Удаление менеджера не удаляет Caddy, его systemd-службу, конфигурацию или
сертификаты. Сам Caddy удаляется отдельным пунктом главного меню.

### Что умеет меню

- устанавливать Caddy из официального сервиса сборок в `/usr/local/bin/caddy`;
- создавать пользователя `caddy`, systemd unit и безопасный
  стартовый Caddyfile;
- находить аддоны в официальном каталоге Caddy и добавлять их по номеру;
- добавлять Go-пакет вручную, удалять и обновлять аддоны;
- обновлять Caddy с сохранением зафиксированных версий аддонов либо обновлять
  всё вместе;
- проверять новую сборку и текущий Caddyfile до замены бинарника;
- атомарно заменять бинарник, хранить одну предыдущую сборку и откатываться;
- управлять службой, применять конфигурацию без простоя, показывать логи и
  выполнять диагностику;
- обновлять сам Caddy Manager из главного меню;
- удалять Caddy с сохранением данных или выполнять отдельно подтверждённую
  полную очистку.

Если обнаружен Caddy из RPM/DNF, менеджер сначала предлагает удалить пакет,
сохранив `/etc/caddy` и `/var/lib/caddy`.

Основные пути:

```text
/usr/local/bin/caddy                 бинарник Caddy
/etc/systemd/system/caddy.service    systemd unit
/etc/caddy/Caddyfile                 конфигурация
/var/lib/caddy                       сертификаты и данные Caddy
/var/lib/caddy-manager/state.json    версии Caddy и аддонов
/usr/local/sbin/caddy-manager        команда запуска менеджера
```
