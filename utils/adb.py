import json
import logging
import random
import subprocess
import time
from pathlib import Path

from .adb_installer import ADBInstaller

# Создаём логгер для этого модуля
logger = logging.getLogger(__name__)


class ADBController:
    # Имитация человеческого ввода (см. CLAUDE.md "Имитация человеческого
    # ввода") — рандомизация координат/задержек живёт здесь, в транспорте, а
    # не в core/, чтобы core/ вызывал tap()/swipe()/tap_region() как раньше,
    # ничего не зная об этих деталях.
    _TAP_DELAY_RANGE_S = (0.05, 0.25)
    _SWIPE_DELAY_RANGE_S = (0.05, 0.25)
    # Небольшой джиттер для tap(x, y), когда вызывающий код передаёт уже
    # готовую точку (например, координаты найденного шаблона или центр
    # клетки поля), а не область — для swipe() джиттер намеренно НЕ
    # добавляется: жесты скролла ленты глав откалиброваны так, чтобы целиком
    # попадать в прокручиваемую область, и сдвиг точки старта уводит их в
    # непрокручиваемую шапку экрана (см. button_coords.json →
    # chapter_scroll_region).
    _TAP_POINT_JITTER_PX = 4
    # Отступ от краёв области в tap_region(), чтобы случайная точка не
    # задела границу/соседний элемент UI.
    _REGION_EDGE_MARGIN_RATIO = 0.15

    def __init__(self, device=None):
        installer = ADBInstaller()
        if not installer.ensure_adb():
            raise RuntimeError("Не удалось подключиться к ADB или BlueStacks")

        self.config = self._load_adb_config()
        self.device = device or self.config.get("device_id")
        self.screenshot_remote = self.config.get(
            "screenshot_path", "/sdcard/screen.png"
        )

        logger.info(f"Инициализация ADBController с устройством: {self.device}")
        self._check_device()

    def _load_adb_config(self):
        config_path = Path("assets/config/adb_config.json")
        logger.debug(f"Загрузка конфигурации ADB из: {config_path}")
        if not config_path.exists():
            error_msg = f"Config not found: {config_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)
        with open(config_path, "r") as f:
            config = json.load(f)
            logger.info(f"Конфигурация ADB успешно загружена: {list(config.keys())}")
            return config

    def _check_device(self):
        logger.info(f"Проверка состояния устройства: {self.device}")
        result = subprocess.run(
            f"adb -s {self.device} get-state",
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
        state = result.stdout.strip()
        if state == "device":
            logger.info(f"Устройство {self.device} доступно и готово к работе")
        else:
            error_msg = f"Устройство недоступно: {state}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    def run_adb_command(self, command):
        cmd = f"adb -s {self.device} {command}"
        logger.debug(f"Выполнение ADB команды: {cmd}")
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            error_msg = f"ADB ошибка: {result.stderr}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        logger.debug(f"Команда выполнена успешно: {result.stdout.strip()}")
        return result.stdout.strip()

    def _human_delay(self, delay_range):
        """Случайная пауза перед действием — см. класс-level комментарий про имитацию ввода."""
        time.sleep(random.uniform(*delay_range))

    def tap(self, x, y):
        """
        Тап с небольшим случайным джиттером вокруг (x, y) и случайной паузой
        перед выполнением. Если у вызывающего кода есть целая область (а не
        уже готовая точка) — используйте tap_region(), он даёт точку в любом
        месте области, а не только рядом с центром.
        """
        self._human_delay(self._TAP_DELAY_RANGE_S)
        jx = x + random.randint(-self._TAP_POINT_JITTER_PX, self._TAP_POINT_JITTER_PX)
        jy = y + random.randint(-self._TAP_POINT_JITTER_PX, self._TAP_POINT_JITTER_PX)
        logger.info(f"Выполнен клик: tap {jx} {jy}")
        self.run_adb_command(f"shell input tap {jx} {jy}")

    def tap_region(self, region):
        """
        Тап по случайной точке внутри region=(x1, y1, x2, y2), а не в центр
        (см. CLAUDE.md "Имитация человеческого ввода"). С отступом от краёв
        (_REGION_EDGE_MARGIN_RATIO), чтобы не задеть соседний элемент UI.
        """
        x1, y1, x2, y2 = region
        margin_x = int((x2 - x1) * self._REGION_EDGE_MARGIN_RATIO)
        margin_y = int((y2 - y1) * self._REGION_EDGE_MARGIN_RATIO)
        lo_x, hi_x = x1 + margin_x, x2 - margin_x
        lo_y, hi_y = y1 + margin_y, y2 - margin_y
        if lo_x >= hi_x:
            lo_x, hi_x = x1, x2
        if lo_y >= hi_y:
            lo_y, hi_y = y1, y2
        self.tap(random.randint(lo_x, hi_x), random.randint(lo_y, hi_y))

    def swipe(self, x1, y1, x2, y2, duration=300):
        """
        Свайп со случайной паузой перед выполнением, но БЕЗ джиттера
        координат — в отличие от tap(). Жесты скролла откалиброваны по
        границам прокручиваемых областей (см. button_coords.json →
        chapter_scroll_region), и сдвиг точки старта способен увести жест
        в непрокручиваемую шапку экрана, где он не сработает вовсе.
        """
        self._human_delay(self._SWIPE_DELAY_RANGE_S)
        logger.info(f"Выполнен свайп: ({x1}, {y1}) → ({x2}, {y2}) за {duration}мс")
        self.run_adb_command(f"shell input swipe {x1} {y1} {x2} {y2} {duration}")

    def screenshot(self, local_path="assets/screenshots/current.png"):
        logger.info(
            f"Делаем скриншот: сохранение на устройстве → {self.screenshot_remote}"
        )
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self.run_adb_command(f"shell screencap -p {self.screenshot_remote}")
        logger.info(
            f"Копируем скриншот с устройства: {self.screenshot_remote} → {local_path}"
        )
        self.run_adb_command(f"pull {self.screenshot_remote} {local_path}")
        logger.info(f"Скриншот сохранён: {local_path}")
        return local_path
