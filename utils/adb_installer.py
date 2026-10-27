import logging
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import ClassVar

import requests
from tqdm import tqdm

# Создаём логгер для этого модуля
logger = logging.getLogger(__name__)


class ADBInstaller:
    DOWNLOAD_URLS: ClassVar[dict[str, str]] = {
        "Windows": "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
        "Linux": "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
        "Darwin": "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
    }

    def __init__(self, install_dir="tools/platform-tools"):
        self.install_dir = Path(install_dir)
        self.adb_path = self.install_dir / "adb"
        if sys.platform == "win32":
            self.adb_path = self.adb_path.with_name("adb.exe")
        logger.debug(
            f"ADBInstaller инициализирован. Установочная директория: {self.install_dir}"
        )

    def check_adb(self):
        """Проверяет, доступен ли adb в PATH."""
        logger.info("Проверка наличия ADB в системе")
        if self._is_adb_available("adb"):
            logger.info("ADB найден в системном PATH")
            return "system"
        if self.adb_path.exists():
            self._add_to_path()
            logger.info(f"Локальный ADB найден: {self.adb_path}")
            return "local"
        logger.warning("ADB не найден ни в системе, ни локально")
        return None

    def _add_to_path(self):
        """Добавляет папку ADB в PATH."""
        os.environ["PATH"] = str(self.install_dir) + os.pathsep + os.environ["PATH"]
        logger.debug(f"Путь {self.install_dir} добавлен в PATH")

    def _is_adb_available(self, cmd):
        try:
            subprocess.run(
                [cmd, "--version"],
                capture_output=True,
                check=True,
            )
            logger.debug(f"Команда {cmd} --version выполнена успешно")
            return True
        except (OSError, subprocess.SubprocessError) as e:
            logger.debug(f"ADB недоступен через {cmd}: {e}")
            return False

    def install(self):
        """Скачивает и устанавливает platform-tools."""
        # Определяем ОС
        if sys.platform == "win32":
            os_name = "Windows"
        elif sys.platform.startswith("linux"):
            os_name = "Linux"
        elif sys.platform == "darwin":
            os_name = "Darwin"
        else:
            error_msg = f"Не поддерживаемая ОС: {sys.platform}"
            logger.error(error_msg)
            raise OSError(error_msg)

        url = self.DOWNLOAD_URLS.get(os_name)
        logger.info(f"Определена ОС: {os_name}. Подготовка к загрузке ADB")

        zip_path = Path("platform-tools.zip")

        try:
            logger.info(f"Скачивание platform-tools с {url}")
            with requests.get(url, stream=True) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))

                with (
                    open(zip_path, "wb") as f,
                    tqdm(
                        desc="Скачивание",
                        total=total,
                        unit="B",
                        unit_scale=True,
                        unit_divisor=1024,
                        colour="#1f78b4",
                    ) as progress_bar,
                ):
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                        progress_bar.update(len(chunk))

            logger.info("Загрузка platform-tools завершена")
            logger.info(f"Распаковка архива в {self.install_dir.parent}")
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(self.install_dir.parent)

            if not self.adb_path.exists():
                error_msg = "ADB не найден после распаковки"
                logger.error(error_msg)
                raise FileNotFoundError(error_msg)

            if os.name != "nt":  # Не Windows
                st = os.stat(self.adb_path)
                os.chmod(self.adb_path, st.st_mode | 0o111)
                logger.debug(f"Выставлены права на выполнение для {self.adb_path}")

            self._add_to_path()
            logger.info(f"ADB успешно установлен и добавлен в PATH: {self.install_dir}")

            zip_path.unlink()
            logger.debug("Архив platform-tools.zip удалён")

        except (OSError, requests.RequestException, zipfile.BadZipFile):
            logger.exception("Ошибка установки ADB")
            raise

    def ensure_adb(self, bluestacks_port="127.0.0.1:5555"):
        """
        Проверяет ADB, устанавливает при необходимости и подключается к BlueStacks.
        Возвращает True при успехе.
        """
        logger.info(f"Проверка и настройка ADB для подключения к {bluestacks_port}")
        status = self.check_adb()
        if status == "system":
            logger.info("Используется системный ADB")
        elif status == "local":
            logger.info(f"Используется локальный ADB: {self.adb_path}")
        else:
            logger.warning("ADB не найден в системе")
            choice = (
                input("Хотите установить ADB автоматически? (y/n): ").strip().lower()
            )
            if choice in ["y", "yes", "д", "да"]:
                self.install()
            else:
                logger.warning("Установка ADB отменена пользователем")
                return False

        def is_device_connected():
            result = subprocess.run(
                "adb devices",
                shell=True,
                capture_output=True,
                text=True,
                check=False,
            )
            connected = (
                bluestacks_port in result.stdout and "offline" not in result.stdout
            )
            logger.debug(
                f"Проверка подключения устройства: {bluestacks_port} → {'подключено' if connected else 'не подключено'}"
            )
            return connected

        if is_device_connected():
            logger.info(f"Уже подключено к BlueStacks: {bluestacks_port}")
            return True

        logger.info(f"Подключение к BlueStacks: {bluestacks_port}")
        connect_result = subprocess.run(
            f"adb connect {bluestacks_port}",
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
        logger.debug(
            f"Результат выполнения 'adb connect': stdout={connect_result.stdout.strip()}, stderr={connect_result.stderr}"
        )

        if is_device_connected():
            logger.info(f"Успешное подключение к BlueStacks: {bluestacks_port}")
            return True
        else:
            logger.error(f"Не удалось подключиться к BlueStacks: {bluestacks_port}")
            if connect_result.stderr:
                logger.error(f"Ошибка подключения ADB: {connect_result.stderr}")
            return False
