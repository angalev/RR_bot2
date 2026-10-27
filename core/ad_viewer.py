import time
import cv2
from pathlib import Path
from core.detection import GameDetection
from utils.bluestacks import BlueStacksController


class AdViewer:
    """
    Модуль для просмотра рекламы.
    - Ищет кнопки из папки `assets/templates/icons/ad_viewer_marks`
    - При нахождении — кликает
    - Если не нашёл — цикл по 10 сек, 3 попытки
    - После 3 неудач — нажимает «Назад» 3 раза
    """

    def __init__(self):
        self.adb = BlueStacksController()
        self.detection = GameDetection()
        self.ad_templates_dir = Path("assets/templates/icons/ad_viewer_marks")
        self.templates = []
        self._load_templates()

    def _load_templates(self):
        """Загружает все шаблоны из папки assets/templates/icons/ad_viewer_marks"""
        if not self.ad_templates_dir.exists():
            print(f"⚠️ Папка с шаблонами рекламы не найдена: {self.ad_templates_dir}")
            return

        for file in self.ad_templates_dir.glob("*.png"):
            template = cv2.imread(str(file), cv2.IMREAD_GRAYSCALE)
            if template is not None:
                self.templates.append({
                    "path": str(file),
                    "name": file.stem,
                    "image": template
                })
        print(f"✅ Загружено {len(self.templates)} шаблонов кнопок рекламы из {self.ad_templates_dir}")

    def watch_ad(self, phase1_timeout=30, phase2_timeout=30):
        """
        Полный цикл просмотра рекламы:
        1. Первые 30 сек — активный поиск
        2. Вторые 30 сек — повторный активный поиск
        """
        print("▶️ Реклама запущена. Начинаем мониторинг...")

        # Фаза 1
        if self._search_loop(timeout=10, attempts=3, phase_name="Фаза 1"):
            print("✅ Кнопка найдена и нажата (фаза 1)")
            return True

        # Фаза 2
        if self._search_loop(timeout=10, attempts=3, phase_name="Фаза 2"):
            print("✅ Кнопка найдена и нажата (фаза 2)")
            return True

        print("❌ Кнопки пропуска не найдены за оба этапа")
        return False

    def _search_loop(self, timeout=10, attempts=3, phase_name="Поиск"):
        """Циклический поиск кнопки: 10 сек × 3 попытки, затем 3 нажатия 'Назад'"""
        for attempt in range(1, attempts + 1):
            print(f"{phase_name}: попытка {attempt}/{attempts} (ждём {timeout} сек)")
            if self._search_and_click(timeout):
                return True

        print("🔁 3 неудачные попытки. Нажимаем 'Назад' 3 раза...")
        for i in range(3):
            self.adb.press_back()
            print(f"🔙 Нажатие 'Назад' {i+1}/3")
            time.sleep(5)

        return False

    def _search_and_click(self, duration):
        """
        Ищет любую из кнопок в течение `duration` секунд.
        Если находит — кликает и возвращает True.
        """
        start_time = time.time()
        while time.time() - start_time < duration:
            screenshot = self.detection.get_screenshot()
            if screenshot is None:
                time.sleep(1)
                continue

            screenshot_gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)

            for btn in self.templates:
                result = cv2.matchTemplate(screenshot_gray, btn["image"], cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)

                if max_val > 0.9:
                    x, y = max_loc
                    w, h = btn["image"].shape[1], btn["image"].shape[0]
                    center_x = x + w // 2
                    center_y = y + h // 2

                    print(f"⏭ Нажимаем '{btn['name']}' по координатам ({center_x}, {center_y}) [схожесть: {max_val:.2f}]")
                    self.adb.tap(center_x, center_y)
                    time.sleep(1)
                    return True

            time.sleep(1)  # Анти-фриз

        return False