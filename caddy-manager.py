#!/usr/bin/env python3
"""Interactive Caddy manager."""

from __future__ import annotations

import argparse
import ast
import contextlib
import fcntl
import getpass
import grp
import hashlib
import json
import os
import platform
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import termios
import textwrap
import time
import tty
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


APP_VERSION = "0.3.0"
APP_NAME = "Caddy Manager"

DEFAULT_REPOSITORY_URL = "https://raw.githubusercontent.com/v-2841/shells/main"
REPOSITORY_URL = os.environ.get("CADDY_MANAGER_BASE_URL", DEFAULT_REPOSITORY_URL).rstrip("/")
MANAGER_DOWNLOAD_URL = f"{REPOSITORY_URL}/caddy-manager.py"
MANAGER_TARGET = Path("/usr/local/sbin/caddy-manager")

CADDY_BINARY = Path("/usr/local/bin/caddy")
CADDY_UNIT = Path("/etc/systemd/system/caddy.service")
CADDY_CONFIG_DIR = Path("/etc/caddy")
CADDY_CONFIG = CADDY_CONFIG_DIR / "Caddyfile"
CADDY_SNIPPETS = CADDY_CONFIG_DIR / "Caddyfile.d"
CADDY_DATA = Path("/var/lib/caddy")

STATE_DIR = Path("/var/lib/caddy-manager")
STATE_FILE = STATE_DIR / "state.json"
PREVIOUS_STATE_FILE = STATE_DIR / "previous-state.json"
BACKUP_DIR = STATE_DIR / "backups"
PREVIOUS_BINARY = BACKUP_DIR / "caddy.previous"
CATALOG_CACHE = STATE_DIR / "packages.json"
LOCK_FILE = Path("/run/caddy-manager.lock")

DOWNLOAD_API = "https://caddyserver.com/api/download"
PACKAGES_API = "https://caddyserver.com/api/packages"
LATEST_RELEASE_API = "https://api.github.com/repos/caddyserver/caddy/releases/latest"
USER_AGENT = f"caddy-manager/{APP_VERSION} (+https://github.com/v-2841/shells)"

PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~/-]*$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+~-]*$")

CADDY_SERVICE = """# caddy.service
# Based on Caddy's official systemd unit:
# https://github.com/caddyserver/dist/blob/master/init/caddy.service

[Unit]
Description=Caddy
Documentation=https://caddyserver.com/docs/
After=network.target network-online.target
Requires=network-online.target

[Service]
Type=notify
User=caddy
Group=caddy
ExecStart=/usr/local/bin/caddy run --environ --config /etc/caddy/Caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile --force
TimeoutStopSec=5s
LimitNOFILE=1048576
PrivateTmp=true
ProtectSystem=full
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE
Restart=on-failure
RestartPreventExitStatus=1
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""

DEFAULT_CADDYFILE = """# Caddy configuration
# Documentation: https://caddyserver.com/docs/caddyfile

# Safe local placeholder. Replace it with your domain or reverse proxy.
http://localhost {
    bind 127.0.0.1
    respond "Caddy is running" 200
}

# Optional additional site blocks can be placed here.
import Caddyfile.d/*.caddyfile
"""


class ManagerError(RuntimeError):
    """A user-facing error which does not need a traceback."""


class Palette:
    def __init__(self) -> None:
        self.enabled = sys.stdout.isatty() and "NO_COLOR" not in os.environ

    def paint(self, value: str, *codes: str) -> str:
        if not self.enabled or not value:
            return value
        return f"\033[{';'.join(codes)}m{value}\033[0m"

    def bold(self, value: str) -> str:
        return self.paint(value, "1")

    def dim(self, value: str) -> str:
        return self.paint(value, "2")

    def blue(self, value: str) -> str:
        return self.paint(value, "38", "5", "75")

    def cyan(self, value: str) -> str:
        return self.paint(value, "38", "5", "80")

    def green(self, value: str) -> str:
        return self.paint(value, "38", "5", "78")

    def yellow(self, value: str) -> str:
        return self.paint(value, "38", "5", "221")

    def red(self, value: str) -> str:
        return self.paint(value, "38", "5", "203")


class UI:
    def __init__(self) -> None:
        self.c = Palette()
        self.interactive = sys.stdin.isatty() and sys.stdout.isatty()

    @staticmethod
    def width() -> int:
        return max(54, min(86, shutil.get_terminal_size((72, 24)).columns))

    def clear(self) -> None:
        if self.interactive:
            print("\033[2J\033[H", end="")

    def header(self, subtitle: str = "") -> None:
        width = self.width()
        inner_width = width - 2
        print(self.c.blue("╭" + "─" * (width - 2) + "╮"))
        title = f"  {APP_NAME}"
        title = title[:inner_width]
        print(
            self.c.blue("│")
            + self.c.bold(title)
            + " " * (inner_width - len(title))
            + self.c.blue("│")
        )
        if subtitle:
            plain = f"  {subtitle}"[:inner_width]
            print(
                self.c.blue("│")
                + self.c.dim(plain)
                + " " * (inner_width - len(plain))
                + self.c.blue("│")
            )
        print(self.c.blue("╰" + "─" * (width - 2) + "╯"))

    def menu(self, title: str, items: Sequence[Tuple[str, str]]) -> str:
        print()
        print(self.c.bold(title))
        for key, label in items:
            key_view = self.c.cyan(f"{key:>2}")
            print(f"  {key_view}  {label}")
        print()
        if self.interactive:
            allowed = {key for key, _ in items}
            print(self.c.dim("Выберите пункт: "), end="", flush=True)
            while True:
                key = self.read_key()
                if key in allowed:
                    print(key)
                    return key
                if not key:
                    print()
                    return "0"
                print("\a", end="", flush=True)
        try:
            return input(self.c.dim("Выберите пункт: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return "0"

    def read_key(self) -> str:
        descriptor = sys.stdin.fileno()
        previous = termios.tcgetattr(descriptor)
        try:
            tty.setcbreak(descriptor)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)

    def ask(self, prompt: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        try:
            value = input(f"{prompt}{self.c.dim(suffix)}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        return value or default

    def confirm(self, prompt: str, default: bool = True) -> bool:
        marker = "Y/n" if default else "y/N"
        answer = self.ask(f"{prompt} [{marker}]").lower()
        if not answer:
            return default
        return answer in {"y", "yes", "д", "да"}

    def info(self, message: str) -> None:
        print(f"{self.c.cyan('●')} {message}")

    def success(self, message: str) -> None:
        print(f"{self.c.green('✓')} {message}")

    def warning(self, message: str) -> None:
        print(f"{self.c.yellow('!')} {message}")

    def error(self, message: str) -> None:
        print(f"{self.c.red('✗')} {message}", file=sys.stderr)

    def rule(self) -> None:
        print(self.c.dim("─" * self.width()))

    def pause(self) -> None:
        if not self.interactive:
            return
        print(self.c.dim("\nНажмите любую клавишу, чтобы продолжить…"), end="", flush=True)
        self.read_key()
        print()

    def progress(self, label: str, done: int, total: Optional[int]) -> None:
        if not self.interactive:
            return
        if total:
            percent = min(100, int(done * 100 / total))
            message = f"{label}: {percent:3d}%"
        else:
            message = f"{label}: {human_size(done)}"
        print(f"\r{self.c.cyan('↓')} {message:<48}", end="", flush=True)

    def progress_done(self) -> None:
        if self.interactive:
            print("\r" + " " * 60 + "\r", end="", flush=True)


ui = UI()


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024
    return f"{size} B"


def read_os_release() -> Dict[str, str]:
    result: Dict[str, str] = {}
    try:
        for raw_line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" not in raw_line or raw_line.lstrip().startswith("#"):
                continue
            key, value = raw_line.split("=", 1)
            result[key] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return result


def require_fedora() -> Dict[str, str]:
    release = read_os_release()
    if release.get("ID") != "fedora":
        name = release.get("PRETTY_NAME", "неизвестная система")
        raise ManagerError(f"Поддерживается только Fedora. Обнаружено: {name}.")
    return release


def require_root() -> None:
    if os.geteuid() == 0:
        return
    sudo = shutil.which("sudo")
    if not sudo:
        raise ManagerError("Для работы нужны права root, но команда sudo не найдена.")
    ui.info(f"Запрашиваю права администратора для пользователя {getpass.getuser()}…")
    args = [sudo, "--", sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
    os.execv(sudo, args)


@contextlib.contextmanager
def process_lock() -> Iterable[None]:
    handle = LOCK_FILE.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise ManagerError("Другой экземпляр Caddy Manager уже запущен.") from exc
    try:
        handle.write(str(os.getpid()))
        handle.flush()
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def run_command(
    command: Sequence[str],
    *,
    check: bool = False,
    capture: bool = True,
    timeout: Optional[int] = 120,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ManagerError(f"Не найдена системная команда: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ManagerError(f"Команда не завершилась вовремя: {' '.join(command)}") from exc
    if check and result.returncode != 0:
        details = (result.stderr or result.stdout or "неизвестная ошибка").strip()
        raise ManagerError(f"Команда завершилась с ошибкой: {' '.join(command)}\n{details}")
    return result


def command_ok(command: Sequence[str]) -> bool:
    return run_command(command, timeout=30).returncode == 0


def atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, content: str, mode: int) -> None:
    atomic_write(path, content.encode("utf-8"), mode)


def atomic_copy(source: Path, target: Path, mode: int) -> None:
    """Copy a file and replace the destination inode in one atomic step."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary)
        os.chown(temporary, 0, 0)
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def default_state() -> Dict[str, Any]:
    return {
        "schema": 1,
        "caddy_version": None,
        "sha256": None,
        "architecture": None,
        "modules": [],
        "updated_at": None,
    }


def load_json(path: Path, fallback: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return fallback


def load_state(path: Path = STATE_FILE) -> Dict[str, Any]:
    state = load_json(path, default_state())
    if not isinstance(state, dict) or not isinstance(state.get("modules", []), list):
        return default_state()
    merged = default_state()
    merged.update(state)
    return merged


def save_state(state: Dict[str, Any], path: Path = STATE_FILE) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write_text(path, payload, 0o600)


def fetch_json(url: str, timeout: int = 30) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        details = exc.read(1000).decode("utf-8", "replace").strip()
        raise ManagerError(f"Сервер вернул HTTP {exc.code}: {details or exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ManagerError(f"Не удалось получить данные с {url}: {exc}") from exc


def architecture() -> str:
    machine = platform.machine().lower()
    mapping = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "armv7l": "arm",
        "armv6l": "arm",
        "ppc64le": "ppc64le",
        "s390x": "s390x",
        "riscv64": "riscv64",
    }
    try:
        return mapping[machine]
    except KeyError as exc:
        raise ManagerError(f"Архитектура {machine!r} не поддерживается сервисом сборок Caddy.") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_package_spec(spec: str) -> Tuple[str, Optional[str]]:
    raw = spec.strip()
    package, version = raw, None
    if "@" in raw:
        package, version = raw.rsplit("@", 1)
    if not PACKAGE_RE.fullmatch(package):
        raise ManagerError("Некорректный путь пакета. Пример: github.com/caddy-dns/cloudflare")
    if version and not VERSION_RE.fullmatch(version):
        raise ManagerError("Некорректная версия модуля. Пример: v0.2.2")
    return package, version


def manager_source_version(source: str) -> str:
    try:
        tree = ast.parse(source)
        compile(source, "caddy-manager.py", "exec")
    except (SyntaxError, ValueError) as exc:
        raise ManagerError(f"Загруженный файл содержит ошибку: {exc}") from exc

    values: Dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                values[target.id] = node.value.value
    if values.get("APP_NAME") != APP_NAME or not values.get("APP_VERSION"):
        raise ManagerError("Сервер вернул неожиданный файл.")
    return values["APP_VERSION"]


def module_specs(modules: Sequence[Dict[str, Any]], pin_versions: bool = True) -> List[str]:
    specs: List[str] = []
    for module in modules:
        package = str(module.get("package", "")).strip()
        version = str(module.get("version") or "").strip()
        parse_package_spec(package)
        specs.append(f"{package}@{version}" if pin_versions and version else package)
    return specs


class Build:
    def __init__(self, path: Path, version: str, checksum: str, modules: List[Dict[str, Any]]) -> None:
        self.path = path
        self.version = version
        self.checksum = checksum
        self.modules = modules


class CaddyManager:
    def __init__(self) -> None:
        self.state = load_state()

    def manual_installed(self) -> bool:
        return CADDY_BINARY.is_file() and CADDY_UNIT.is_file()

    def rpm_installed(self) -> bool:
        return command_ok(["rpm", "-q", "caddy"])

    def service_active(self) -> bool:
        return command_ok(["systemctl", "is-active", "--quiet", "caddy"])

    def service_enabled(self) -> bool:
        return command_ok(["systemctl", "is-enabled", "--quiet", "caddy"])

    def binary_version(self, binary: Path = CADDY_BINARY) -> Optional[str]:
        if not binary.is_file():
            return None
        result = run_command([str(binary), "version"], timeout=30)
        if result.returncode != 0:
            return None
        output = result.stdout.strip()
        return output.split()[0] if output else None

    def status_lines(self) -> List[str]:
        manual = self.manual_installed()
        rpm = self.rpm_installed()
        if manual:
            install_text = ui.c.green("ручная установка")
        elif rpm:
            install_text = ui.c.yellow("RPM-пакет")
        else:
            install_text = ui.c.dim("не установлен")
        if self.service_active():
            service_text = ui.c.green("работает")
        elif self.service_enabled():
            service_text = ui.c.yellow("остановлен, автозапуск включён")
        else:
            service_text = ui.c.dim("остановлен")
        binary = CADDY_BINARY if manual else Path(shutil.which("caddy") or "")
        version = self.binary_version(binary) if str(binary) else None
        modules = self.state.get("modules", []) if manual else []
        return [
            f"Установка: {install_text}",
            f"Служба:    {service_text}",
            f"Версия:    {version or '—'}",
            f"Аддоны:    {len(modules)}",
        ]

    def show_status(self) -> None:
        print()
        for line in self.status_lines():
            print(f"  {line}")

    def run(self) -> None:
        while True:
            self.state = load_state()
            ui.clear()
            ui.header()
            self.show_status()
            installed = self.manual_installed()
            if installed:
                items = [
                    ("1", "Установить заново или восстановить"),
                    ("2", "Обновления и откат"),
                    ("3", "Управление аддонами"),
                    ("4", "Служба и конфигурация"),
                    ("5", "Диагностика"),
                    ("6", "Удалить Caddy"),
                    ("7", "Обновить Caddy Manager"),
                    ("0", "Выход"),
                ]
            else:
                items = [
                    ("1", "Установить Caddy"),
                    ("5", "Диагностика"),
                    ("7", "Обновить Caddy Manager"),
                    ("0", "Выход"),
                ]
            choice = ui.menu("Главное меню", items)
            try:
                if choice == "1":
                    self.install_or_repair()
                elif choice == "2" and installed:
                    self.update_menu()
                elif choice == "3" and installed:
                    self.addons_menu()
                elif choice == "4" and installed:
                    self.service_menu()
                elif choice == "5":
                    self.doctor()
                elif choice == "6" and installed:
                    self.uninstall_caddy()
                elif choice == "7":
                    self.update_manager()
                elif choice == "0":
                    return
                else:
                    ui.warning("Такого пункта нет.")
                    ui.pause()
            except (ManagerError, OSError) as exc:
                ui.error(str(exc))
                ui.pause()
            except KeyboardInterrupt:
                print()
                ui.warning("Операция прервана. Установленные файлы не изменены, если замена ещё не началась.")
                ui.pause()

    def update_manager(self) -> None:
        ui.clear()
        ui.header("Обновление Caddy Manager")
        ui.info("Проверяю обновления…")
        request = urllib.request.Request(MANAGER_DOWNLOAD_URL, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read(2 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            details = exc.read(1000).decode("utf-8", "replace").strip()
            raise ManagerError(f"Сервер вернул HTTP {exc.code}: {details or exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ManagerError(f"Не удалось проверить обновления: {exc}") from exc

        if len(payload) > 2 * 1024 * 1024:
            raise ManagerError("Загруженный файл имеет неожиданный размер.")
        try:
            source = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ManagerError("Сервер вернул неожиданный файл.") from exc
        available_version = manager_source_version(source)

        if available_version == APP_VERSION:
            ui.success(f"Установлена актуальная версия {APP_VERSION}.")
            ui.pause()
            return
        if not ui.confirm(f"Обновить Caddy Manager {APP_VERSION} → {available_version}?", True):
            return

        atomic_write(MANAGER_TARGET, payload, 0o755)
        if shutil.which("restorecon"):
            run_command(["restorecon", "-F", str(MANAGER_TARGET)])
        ui.success(f"Caddy Manager обновлён до версии {available_version}.")
        ui.info("Новая версия будет запущена при следующем открытии меню.")
        ui.pause()

    def download_build(
        self,
        modules: Sequence[Dict[str, Any]],
        *,
        pin_versions: bool,
        directory: Path,
    ) -> Build:
        arch = architecture()
        specs = module_specs(modules, pin_versions=pin_versions)
        parameters: List[Tuple[str, str]] = [
            ("os", "linux"),
            ("arch", arch),
            ("idempotency", uuid.uuid4().hex),
        ]
        parameters.extend(("p", spec) for spec in specs)
        url = f"{DOWNLOAD_API}?{urllib.parse.urlencode(parameters)}"
        target = directory / "caddy"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

        ui.info("Запрашиваю официальную сборку Caddy…")
        if specs:
            for spec in specs:
                print(f"    {ui.c.dim('＋')} {spec}")
        try:
            with urllib.request.urlopen(request, timeout=900) as response, target.open("wb") as output:
                length_header = response.headers.get("Content-Length")
                total = int(length_header) if length_header and length_header.isdigit() else None
                downloaded = 0
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    ui.progress("Получение сборки", downloaded, total)
        except urllib.error.HTTPError as exc:
            ui.progress_done()
            details = exc.read(4000).decode("utf-8", "replace").strip()
            raise ManagerError(f"Сервис сборок Caddy вернул HTTP {exc.code}:\n{details or exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            ui.progress_done()
            raise ManagerError(f"Не удалось скачать сборку Caddy: {exc}") from exc
        ui.progress_done()

        if not target.is_file() or target.stat().st_size < 1024 * 1024:
            details = target.read_text(encoding="utf-8", errors="replace")[:2000] if target.exists() else ""
            raise ManagerError(f"Сервис вернул некорректный бинарник. {details}")
        os.chmod(target, 0o755)

        version = self.binary_version(target)
        if not version:
            raise ManagerError("Скачанный файл не запускается как Caddy.")
        resolved_modules = self.resolve_module_versions(target, modules)
        checksum = sha256_file(target)
        ui.success(f"Сборка {version} получена и проверена ({human_size(target.stat().st_size)}).")
        return Build(target, version, checksum, resolved_modules)

    def resolve_module_versions(
        self, binary: Path, requested: Sequence[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if not requested:
            return []
        result = run_command([str(binary), "build-info"], check=True, timeout=60)
        dependencies: Dict[str, str] = {}
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 3 and fields[0] == "dep":
                dependencies[fields[1]] = fields[2]

        resolved: List[Dict[str, Any]] = []
        missing: List[str] = []
        for item in requested:
            package = str(item["package"])
            version = dependencies.get(package)
            if not version:
                old_version = str(item.get("version") or "")
                if old_version:
                    version = old_version
                else:
                    missing.append(package)
                    continue
            resolved.append({"package": package, "version": version})
        if missing:
            raise ManagerError(
                "Не удалось подтвердить наличие модулей в сборке: " + ", ".join(missing)
            )
        return resolved

    def validate_config(self, binary: Path = CADDY_BINARY) -> Tuple[bool, str]:
        if not CADDY_CONFIG.is_file():
            return False, f"Файл {CADDY_CONFIG} не найден."
        result = run_command(
            [str(binary), "validate", "--config", str(CADDY_CONFIG), "--adapter", "caddyfile"],
            timeout=120,
        )
        output = (result.stderr or result.stdout or "").strip()
        return result.returncode == 0, output

    def ensure_user_and_directories(self) -> None:
        try:
            group = grp.getgrnam("caddy")
        except KeyError:
            run_command(["groupadd", "--system", "caddy"], check=True, capture=False)
            group = grp.getgrnam("caddy")
        try:
            user = pwd.getpwnam("caddy")
        except KeyError:
            run_command(
                [
                    "useradd",
                    "--system",
                    "--gid",
                    "caddy",
                    "--create-home",
                    "--home-dir",
                    str(CADDY_DATA),
                    "--shell",
                    "/usr/sbin/nologin",
                    "--comment",
                    "Caddy web server",
                    "caddy",
                ],
                check=True,
                capture=False,
            )
            user = pwd.getpwnam("caddy")

        CADDY_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CADDY_SNIPPETS.mkdir(parents=True, exist_ok=True)
        CADDY_DATA.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        os.chown(CADDY_CONFIG_DIR, 0, group.gr_gid)
        os.chmod(CADDY_CONFIG_DIR, 0o750)
        os.chown(CADDY_SNIPPETS, 0, group.gr_gid)
        os.chmod(CADDY_SNIPPETS, 0o750)
        os.chown(CADDY_DATA, user.pw_uid, group.gr_gid)
        os.chmod(CADDY_DATA, 0o750)
        os.chmod(STATE_DIR, 0o700)
        os.chmod(BACKUP_DIR, 0o700)

        if not CADDY_CONFIG.exists():
            atomic_write_text(CADDY_CONFIG, DEFAULT_CADDYFILE, 0o640)
        os.chown(CADDY_CONFIG, 0, group.gr_gid)
        os.chmod(CADDY_CONFIG, 0o640)

    def write_service(self) -> None:
        atomic_write_text(CADDY_UNIT, CADDY_SERVICE, 0o644)
        if shutil.which("restorecon"):
            run_command(["restorecon", "-F", str(CADDY_UNIT)], capture=True)
        run_command(["systemctl", "daemon-reload"], check=True)

    def remove_rpm_conflict(self) -> None:
        if not self.rpm_installed():
            return
        ui.warning("Обнаружен пакет Caddy, установленный через RPM/DNF.")
        print("  Ручная и пакетная установки не должны одновременно управлять caddy.service.")
        if not ui.confirm("Удалить RPM-пакет Caddy, сохранив конфигурацию и данные?", True):
            raise ManagerError("Установка отменена: сначала нужно убрать конфликтующий RPM-пакет.")

        snapshot: Optional[bytes] = None
        packaged_default = False
        if CADDY_CONFIG.is_file():
            snapshot = CADDY_CONFIG.read_bytes()
            # If any packaged file differs, preserve the existing Caddyfile
            # conservatively instead of assuming that it is still the default.
            verification = run_command(["rpm", "-V", "caddy"])
            packaged_default = verification.returncode == 0 and not verification.stdout.strip()
        run_command(
            ["dnf", "remove", "--no-autoremove", "-y", "caddy"],
            check=True,
            capture=False,
            timeout=None,
        )
        if packaged_default:
            CADDY_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write_text(CADDY_CONFIG, DEFAULT_CADDYFILE, 0o640)
            ui.info("Стандартный RPM-Caddyfile заменён безопасной локальной заготовкой.")
        elif snapshot is not None and not CADDY_CONFIG.exists():
            CADDY_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write(CADDY_CONFIG, snapshot, 0o640)
        ui.success("RPM-пакет удалён; /etc/caddy и /var/lib/caddy сохранены.")

    def activate_build(self, build: Build) -> None:
        CADDY_BINARY.parent.mkdir(parents=True, exist_ok=True)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        old_exists = CADDY_BINARY.is_file()
        old_state = load_state()
        active_before = self.service_active()

        if old_exists:
            shutil.copy2(CADDY_BINARY, PREVIOUS_BINARY)
            os.chmod(PREVIOUS_BINARY, 0o700)
            save_state(old_state, PREVIOUS_STATE_FILE)

        try:
            atomic_copy(build.path, CADDY_BINARY, 0o755)
            if shutil.which("restorecon"):
                run_command(["restorecon", "-F", str(CADDY_BINARY)])

            new_state = {
                "schema": 1,
                "caddy_version": build.version,
                "sha256": build.checksum,
                "architecture": architecture(),
                "modules": build.modules,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            if active_before:
                restart = run_command(["systemctl", "restart", "caddy"])
                if restart.returncode != 0 or not self.service_active():
                    details = (restart.stderr or restart.stdout or "служба не запустилась").strip()
                    raise ManagerError(f"Новая сборка не запустилась: {details}")
            save_state(new_state)
            self.state = new_state
        except Exception:
            if old_exists and PREVIOUS_BINARY.is_file():
                atomic_copy(PREVIOUS_BINARY, CADDY_BINARY, 0o755)
                if shutil.which("restorecon"):
                    run_command(["restorecon", "-F", str(CADDY_BINARY)])
                if active_before:
                    run_command(["systemctl", "restart", "caddy"])
            elif not old_exists:
                CADDY_BINARY.unlink(missing_ok=True)
            raise

    def rebuild(self, modules: Sequence[Dict[str, Any]], *, pin_versions: bool) -> None:
        with tempfile.TemporaryDirectory(prefix="caddy-manager-") as temporary:
            build = self.download_build(modules, pin_versions=pin_versions, directory=Path(temporary))
            valid, details = self.validate_config(build.path)
            if not valid:
                raise ManagerError(f"Новая сборка не принимает текущий Caddyfile:\n{details}")
            self.activate_build(build)
        ui.success(f"Активирована Caddy {build.version}.")

    def install_or_repair(self) -> None:
        ui.clear()
        ui.header("Установка или восстановление")
        modules = self.state.get("modules", [])
        if CADDY_BINARY.exists() and not STATE_FILE.exists():
            ui.warning("Найден неуправляемый бинарник /usr/local/bin/caddy без состояния менеджера.")
            if not ui.confirm("Заменить его стандартной сборкой без известных аддонов?", False):
                return
            modules = []

        with tempfile.TemporaryDirectory(prefix="caddy-manager-") as temporary:
            build = self.download_build(modules, pin_versions=True, directory=Path(temporary))
            self.remove_rpm_conflict()
            self.ensure_user_and_directories()
            self.write_service()
            valid, details = self.validate_config(build.path)
            if not valid:
                raise ManagerError(f"Caddyfile не прошёл проверку новой сборкой:\n{details}")
            self.activate_build(build)

        ui.success(f"Caddy {build.version} установлена в {CADDY_BINARY}.")
        if self.service_active():
            ui.success("Служба перезапущена и работает.")
        elif ui.confirm("Включить автозапуск и запустить Caddy сейчас?", True):
            result = run_command(["systemctl", "enable", "--now", "caddy"])
            if result.returncode != 0 or not self.service_active():
                details = (result.stderr or result.stdout or "служба не запустилась").strip()
                ui.error(f"Caddy установлена, но служба не запустилась: {details}")
                ui.info("Откройте «Диагностика» или «Служба и конфигурация → Логи».")
            else:
                ui.success("Служба включена и запущена.")
        ui.pause()

    def update_menu(self) -> None:
        while True:
            ui.clear()
            ui.header("Обновления и откат")
            current = self.binary_version() or "—"
            print(f"\n  Текущая версия: {current}")
            choice = ui.menu(
                "Действия",
                [
                    ("1", "Проверить последнюю версию Caddy"),
                    ("2", "Обновить Caddy, сохранив версии аддонов"),
                    ("3", "Обновить Caddy и все аддоны"),
                    ("4", "Откатиться на предыдущую сборку"),
                    ("0", "Назад"),
                ],
            )
            if choice == "0":
                return
            try:
                if choice == "1":
                    self.check_latest_version()
                elif choice == "2":
                    self.rebuild(self.state.get("modules", []), pin_versions=True)
                elif choice == "3":
                    modules = [
                        {"package": item["package"], "version": None}
                        for item in self.state.get("modules", [])
                    ]
                    self.rebuild(modules, pin_versions=False)
                elif choice == "4":
                    self.rollback()
                else:
                    ui.warning("Такого пункта нет.")
            except (ManagerError, OSError) as exc:
                ui.error(str(exc))
            ui.pause()

    def check_latest_version(self) -> None:
        ui.info("Проверяю официальный релиз Caddy…")
        payload = fetch_json(LATEST_RELEASE_API)
        latest = str(payload.get("tag_name", "")).strip() if isinstance(payload, dict) else ""
        if not latest:
            raise ManagerError("GitHub не вернул номер последнего релиза Caddy.")
        current = self.binary_version() or "неизвестно"
        if current == latest:
            ui.success(f"Установлена актуальная версия {current}.")
        else:
            ui.warning(f"Установлена {current}, доступна {latest}.")
        if self.state.get("modules"):
            ui.info("Версии аддонов проверяются во время пункта «Обновить Caddy и все аддоны».")

    def rollback(self) -> None:
        if not PREVIOUS_BINARY.is_file() or not PREVIOUS_STATE_FILE.is_file():
            raise ManagerError("Предыдущая сборка для отката не найдена.")
        previous_state = load_state(PREVIOUS_STATE_FILE)
        previous_version = self.binary_version(PREVIOUS_BINARY) or "неизвестная версия"
        if not ui.confirm(f"Заменить текущую сборку на {previous_version}?", False):
            return
        valid, details = self.validate_config(PREVIOUS_BINARY)
        if not valid:
            raise ManagerError(f"Предыдущая сборка не принимает текущий Caddyfile:\n{details}")

        active = self.service_active()
        with tempfile.TemporaryDirectory(prefix="caddy-rollback-") as temporary:
            current_copy = Path(temporary) / "caddy.current"
            shutil.copy2(CADDY_BINARY, current_copy)
            current_state = load_state()
            try:
                atomic_copy(PREVIOUS_BINARY, CADDY_BINARY, 0o755)
                if shutil.which("restorecon"):
                    run_command(["restorecon", "-F", str(CADDY_BINARY)])
                if active:
                    result = run_command(["systemctl", "restart", "caddy"])
                    if result.returncode != 0 or not self.service_active():
                        raise ManagerError((result.stderr or result.stdout or "служба не запустилась").strip())
                shutil.copy2(current_copy, PREVIOUS_BINARY)
                os.chmod(PREVIOUS_BINARY, 0o700)
                save_state(current_state, PREVIOUS_STATE_FILE)
                save_state(previous_state)
                self.state = previous_state
            except Exception:
                atomic_copy(current_copy, CADDY_BINARY, 0o755)
                if shutil.which("restorecon"):
                    run_command(["restorecon", "-F", str(CADDY_BINARY)])
                if active:
                    run_command(["systemctl", "restart", "caddy"])
                raise
        ui.success(f"Восстановлена Caddy {previous_version}.")

    def addons_menu(self) -> None:
        while True:
            ui.clear()
            ui.header("Управление аддонами")
            self.print_modules()
            choice = ui.menu(
                "Действия",
                [
                    ("1", "Найти аддон в официальном каталоге"),
                    ("2", "Добавить по пути Go-пакета"),
                    ("3", "Удалить аддон"),
                    ("4", "Обновить все аддоны"),
                    ("5", "Обновить каталог"),
                    ("0", "Назад"),
                ],
            )
            if choice == "0":
                return
            try:
                if choice == "1":
                    self.search_addon()
                elif choice == "2":
                    self.add_by_path()
                elif choice == "3":
                    self.remove_addon()
                elif choice == "4":
                    modules = [
                        {"package": item["package"], "version": None}
                        for item in self.state.get("modules", [])
                    ]
                    if not modules:
                        ui.info("Аддоны ещё не установлены.")
                    elif ui.confirm("Пересобрать Caddy с последними версиями всех аддонов?", True):
                        self.rebuild(modules, pin_versions=False)
                elif choice == "5":
                    self.catalog(refresh=True)
                    ui.success("Каталог обновлён.")
                else:
                    ui.warning("Такого пункта нет.")
            except (ManagerError, OSError) as exc:
                ui.error(str(exc))
            ui.pause()

    def print_modules(self) -> None:
        modules = self.state.get("modules", [])
        print()
        if not modules:
            print(f"  {ui.c.dim('Аддоны не установлены.')}")
            return
        print(ui.c.bold("  Установленные аддоны:"))
        for number, item in enumerate(modules, 1):
            version = item.get("version") or "версия неизвестна"
            print(f"  {number:>2}. {item.get('package')} {ui.c.dim(str(version))}")

    def catalog(self, refresh: bool = False) -> List[Dict[str, Any]]:
        cached = load_json(CATALOG_CACHE, {})
        cache_fresh = (
            isinstance(cached, dict)
            and isinstance(cached.get("packages"), list)
            and time.time() - float(cached.get("saved_at", 0)) < 24 * 60 * 60
        )
        if cache_fresh and not refresh:
            return cached["packages"]
        ui.info("Загружаю официальный каталог модулей Caddy…")
        try:
            payload = fetch_json(PACKAGES_API, timeout=60)
            packages = payload.get("result", []) if isinstance(payload, dict) else []
            packages = [
                item
                for item in packages
                if isinstance(item, dict) and item.get("listed") and item.get("available") and item.get("path")
            ]
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            data = {"saved_at": time.time(), "packages": packages}
            atomic_write_text(CATALOG_CACHE, json.dumps(data, ensure_ascii=False), 0o600)
            return packages
        except ManagerError:
            if isinstance(cached, dict) and isinstance(cached.get("packages"), list):
                ui.warning("Сеть недоступна; используется сохранённый каталог.")
                return cached["packages"]
            raise

    def search_addon(self) -> None:
        query = ui.ask("Поиск по пакету или названию модуля").lower()
        packages = self.catalog()

        def haystack(item: Dict[str, Any]) -> str:
            modules = item.get("modules", [])
            parts = [str(item.get("path", "")), str(item.get("repo", ""))]
            for module in modules if isinstance(modules, list) else []:
                if isinstance(module, dict):
                    parts.extend([str(module.get("name", "")), str(module.get("docs", ""))])
            return " ".join(parts).lower()

        matches = [item for item in packages if not query or query in haystack(item)]
        matches.sort(key=lambda item: int(item.get("downloads") or 0), reverse=True)
        matches = matches[:20]
        if not matches:
            ui.warning("Ничего не найдено.")
            return
        print()
        for number, item in enumerate(matches, 1):
            downloads = int(item.get("downloads") or 0)
            print(f"  {number:>2}. {item['path']} {ui.c.dim(f'· {downloads} загрузок')}")
        selection = ui.ask("Номер аддона (0 — отмена)", "0")
        if not selection.isdigit() or int(selection) == 0:
            return
        index = int(selection) - 1
        if index < 0 or index >= len(matches):
            raise ManagerError("Выбран некорректный номер.")
        item = matches[index]
        print()
        print(ui.c.bold(str(item["path"])))
        print(f"Репозиторий: {item.get('repo') or 'не указан'}")
        module_names = [
            str(module.get("name"))
            for module in item.get("modules", [])
            if isinstance(module, dict) and module.get("name")
        ]
        if module_names:
            print("Модули: " + ", ".join(module_names[:8]))
        ui.warning("Аддоны — сторонний код. Устанавливайте только те, которым доверяете.")
        if ui.confirm("Добавить аддон и пересобрать Caddy?", False):
            self.add_module(str(item["path"]), None)

    def add_by_path(self) -> None:
        spec = ui.ask("Go-пакет (можно добавить @версию)")
        if not spec:
            return
        package, version = parse_package_spec(spec)
        ui.warning("Пакет будет скомпилирован официальным сервисом Caddy как сторонний код.")
        if ui.confirm(f"Добавить {package}{'@' + version if version else ''}?", False):
            self.add_module(package, version)

    def add_module(self, package: str, version: Optional[str]) -> None:
        modules = [dict(item) for item in self.state.get("modules", [])]
        if any(item.get("package") == package for item in modules):
            raise ManagerError("Этот аддон уже установлен.")
        modules.append({"package": package, "version": version})
        self.rebuild(modules, pin_versions=True)

    def remove_addon(self) -> None:
        modules = [dict(item) for item in self.state.get("modules", [])]
        if not modules:
            ui.info("Удалять нечего.")
            return
        self.print_modules()
        selection = ui.ask("Номер удаляемого аддона (0 — отмена)", "0")
        if not selection.isdigit() or int(selection) == 0:
            return
        index = int(selection) - 1
        if index < 0 or index >= len(modules):
            raise ManagerError("Выбран некорректный номер.")
        removed = modules.pop(index)
        if ui.confirm(f"Удалить {removed['package']} и пересобрать Caddy?", False):
            self.rebuild(modules, pin_versions=True)

    def service_menu(self) -> None:
        while True:
            ui.clear()
            ui.header("Служба и конфигурация")
            self.show_status()
            choice = ui.menu(
                "Действия",
                [
                    ("1", "Показать статус systemd"),
                    ("2", "Запустить и включить автозапуск"),
                    ("3", "Остановить"),
                    ("4", "Перезапустить"),
                    ("5", "Проверить конфигурацию и применить без простоя"),
                    ("6", "Редактировать Caddyfile"),
                    ("7", "Показать последние логи"),
                    ("8", "Показать пути"),
                    ("0", "Назад"),
                ],
            )
            if choice == "0":
                return
            try:
                if choice == "1":
                    run_command(["systemctl", "status", "caddy", "--no-pager", "--full"], capture=False)
                elif choice == "2":
                    run_command(["systemctl", "enable", "--now", "caddy"], check=True, capture=False)
                    ui.success("Caddy запущена; автозапуск включён.")
                elif choice == "3":
                    run_command(["systemctl", "stop", "caddy"], check=True)
                    ui.success("Caddy остановлена.")
                elif choice == "4":
                    run_command(["systemctl", "restart", "caddy"], check=True)
                    ui.success("Caddy перезапущена.")
                elif choice == "5":
                    valid, details = self.validate_config()
                    if not valid:
                        raise ManagerError(f"Caddyfile содержит ошибку:\n{details}")
                    run_command(["systemctl", "reload", "caddy"], check=True)
                    ui.success("Конфигурация проверена и применена.")
                elif choice == "6":
                    self.edit_config()
                elif choice == "7":
                    run_command(
                        ["journalctl", "-u", "caddy", "-n", "80", "--no-pager", "--output=short-iso"],
                        capture=False,
                    )
                elif choice == "8":
                    self.show_paths()
                else:
                    ui.warning("Такого пункта нет.")
            except (ManagerError, OSError) as exc:
                ui.error(str(exc))
            ui.pause()

    def edit_config(self) -> None:
        candidates = [os.environ.get("VISUAL"), os.environ.get("EDITOR"), "nano", "vi"]
        editor = next((candidate for candidate in candidates if candidate and shutil.which(candidate.split()[0])), None)
        if not editor:
            raise ManagerError(f"Редактор не найден. Откройте файл вручную: {CADDY_CONFIG}")
        command = editor.split() + [str(CADDY_CONFIG)]
        run_command(command, capture=False, timeout=None)
        valid, details = self.validate_config()
        if valid:
            ui.success("Caddyfile корректен.")
            if self.service_active() and ui.confirm("Применить изменения без остановки Caddy?", True):
                run_command(["systemctl", "reload", "caddy"], check=True)
                ui.success("Конфигурация применена.")
        else:
            ui.error(f"Caddyfile сохранён, но содержит ошибку:\n{details}")

    def show_paths(self) -> None:
        rows = [
            ("Бинарник", CADDY_BINARY),
            ("Caddyfile", CADDY_CONFIG),
            ("Доп. конфиги", CADDY_SNIPPETS),
            ("Данные и сертификаты", CADDY_DATA),
            ("Systemd unit", CADDY_UNIT),
            ("Состояние менеджера", STATE_FILE),
            ("Предыдущая сборка", PREVIOUS_BINARY),
        ]
        print()
        for label, path in rows:
            print(f"  {label:<22} {path}")

    def doctor(self) -> None:
        ui.clear()
        ui.header("Диагностика")
        checks: List[Tuple[str, bool, str]] = []
        checks.append(("Бинарник", CADDY_BINARY.is_file(), str(CADDY_BINARY)))
        checks.append(("Systemd unit", CADDY_UNIT.is_file(), str(CADDY_UNIT)))
        checks.append(("Caddyfile", CADDY_CONFIG.is_file(), str(CADDY_CONFIG)))
        try:
            user = pwd.getpwnam("caddy")
            checks.append(("Пользователь", True, f"uid={user.pw_uid}, home={user.pw_dir}"))
        except KeyError:
            checks.append(("Пользователь", False, "caddy не создан"))
        checks.append(("Служба", self.service_active(), "active" if self.service_active() else "inactive"))
        if CADDY_BINARY.is_file() and CADDY_CONFIG.is_file():
            valid, details = self.validate_config()
            checks.append(("Конфигурация", valid, "валидна" if valid else details.splitlines()[-1][:90]))
        if shutil.which("getenforce"):
            enforcing = run_command(["getenforce"]).stdout.strip()
            checks.append(("SELinux", enforcing in {"Enforcing", "Permissive"}, enforcing))
        if shutil.which("ls") and CADDY_BINARY.exists():
            context_result = run_command(["ls", "-Zd", str(CADDY_BINARY)])
            context = context_result.stdout.split()[0] if context_result.stdout else "неизвестно"
            checks.append(("SELinux label", "bin_t" in context, context))

        print()
        for label, ok, details in checks:
            mark = ui.c.green("✓") if ok else ui.c.yellow("!")
            print(f"  {mark} {label:<18} {details}")
        self.show_paths()
        if self.state.get("modules"):
            self.print_modules()
        print()
        ui.info("Для подробных ошибок откройте «Служба и конфигурация → Логи».")
        ui.pause()

    def uninstall_caddy(self) -> None:
        ui.clear()
        ui.header("Удаление Caddy")
        print(
            textwrap.dedent(
                f"""
                  Будут удалены:
                    • {CADDY_BINARY}
                    • {CADDY_UNIT}

                  По умолчанию сохраняются конфигурация, сертификаты, данные и список аддонов.
                """
            ).rstrip()
        )
        if not ui.confirm("Остановить службу и удалить ручную установку Caddy?", False):
            return
        run_command(["systemctl", "disable", "--now", "caddy"])
        CADDY_UNIT.unlink(missing_ok=True)
        CADDY_BINARY.unlink(missing_ok=True)
        run_command(["systemctl", "daemon-reload"])
        run_command(["systemctl", "reset-failed", "caddy"])
        ui.success("Caddy удалена. Конфигурация и данные сохранены.")

        if ui.confirm("Также безвозвратно удалить конфигурацию, сертификаты и состояние менеджера?", False):
            phrase = ui.ask("Для подтверждения введите PURGE")
            if phrase == "PURGE":
                for target in (CADDY_CONFIG_DIR, CADDY_DATA, STATE_DIR):
                    if target.is_dir():
                        shutil.rmtree(target)
                    elif target.exists():
                        target.unlink()
                ui.warning("Конфигурация, сертификаты, данные и состояние менеджера удалены.")
            else:
                ui.info("Полная очистка отменена.")
        ui.pause()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    return parser.parse_args()


def main() -> int:
    parse_args()
    try:
        require_fedora()
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ManagerError("Менеджеру нужен интерактивный терминал. Запустите: caddy-manager")
        require_root()
        with process_lock():
            CaddyManager().run()
        return 0
    except (ManagerError, OSError) as exc:
        ui.error(str(exc))
        return 1
    except KeyboardInterrupt:
        print()
        ui.warning("Выход без изменений.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
