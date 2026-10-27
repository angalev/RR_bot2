import unittest
import sys
import logging
import cv2
import numpy as np
from pathlib import Path

# Добавляем корень проекта в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Импортируем тестируемый класс
from core.detection import GameDetection


def remove_close_matches(matches, min_distance=10):
    """
    Удаляет близкие совпадения, оставляя только одно из группы.
    Использует простой алгоритм Non-Maximum Suppression.
    """
    if len(matches) == 0:
        return matches

    filtered = []
    matches = np.array(matches)

    for match in matches:
        if all(np.linalg.norm(match - f) >= min_distance for f in filtered):
            filtered.append(match)
    return np.array(filtered)


class TestGameDetection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.basicConfig(level=logging.INFO)

    def setUp(self):
        self.detection = GameDetection()
        self.template_path = "assets/templates/icons/ad_pve.png"
        self.output_image_path = "debug_ad_pve_detection.png"

    def test_find_ad_pve_with_visual_debug(self):
        """Тест: поиск шаблона без масштабирования — только прямое сравнение"""
        template_file = Path(self.template_path)
        if not template_file.exists():
            self.fail(f"❌ Шаблон не найден: {self.template_path}")

        # 1. Загружаем скриншот
        try:
            screenshot = self.detection.get_screenshot()
            screenshot_rgb = cv2.cvtColor(screenshot, cv2.COLOR_BGR2RGB)
            screenshot_with_matches = screenshot_rgb.copy()
        except Exception as e:
            self.fail(f"❌ Не удалось сделать скриншот: {e}")

        # Проверяем реальный размер скриншота
        screen_h, screen_w = screenshot.shape[:2]
        logging.info(f"📐 Размер скриншота: {screen_w}x{screen_h}")

        # Проверка: если разрешение не 900x1600 — падаем с ошибкой
        if (screen_w, screen_h) != (900, 1600):
            self.fail(
                f"❌ Скриншот имеет разрешение {screen_w}x{screen_h}, "
                "но ожидается 900x1600. Пожалуйста, настройте BlueStacks на фиксированное разрешение."
            )

        # 2. Загружаем шаблон
        template = cv2.imread(self.template_path, cv2.IMREAD_GRAYSCALE)
        if template is None:
            self.fail(f"❌ Не удалось загрузить шаблон: {self.template_path}")
        h, w = template.shape
        logging.info(f"📏 Размер шаблона: {w}x{h}")

        # ✅ Никакого resize — используем шаблон "как есть"
        logging.info(f"📏 Используется оригинальный шаблон без масштабирования: {w}x{h}")

        # 3. Поиск шаблона на скриншоте
        screenshot_gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        result = cv2.matchTemplate(screenshot_gray, template, cv2.TM_CCOEFF_NORMED)
        threshold = 0.8
        locations = np.where(result >= threshold)

        # Преобразуем в список координат центров
        matches = []
        for y, x in zip(*locations[::-1]):  # (x, y)
            center_x = int(x + h // 2)
            center_y = int(y + w // 2)
            matches.append([center_x, center_y])

        # 🔧 Убираем близкие совпадения (NMS)
        matches = remove_close_matches(matches, min_distance=15)
        logging.info(f"✅ Найдено {len(matches)} уникальных совпадений после фильтрации")

        # 4. Вывод координат
        if len(matches) > 0:
            logging.info(f"✅ Окончательные координаты:")
            for i, (x, y) in enumerate(matches):
                logging.info(f"📍 Совпадение {i+1}: ADB tap {x} {y}")

            # 5. Рисуем рамки и центры
            for i, (cx, cy) in enumerate(matches):
                top_left = (int(cx - w // 2), int(cy - h // 2))
                bottom_right = (int(cx + w // 2), int(cy + h // 2))
                cv2.rectangle(screenshot_with_matches, top_left, bottom_right, (0, 255, 0), 2)
                cv2.circle(screenshot_with_matches, (cx, cy), radius=5, color=(255, 0, 0), thickness=-1)
                cv2.putText(screenshot_with_matches, f"Match {i+1}",
                            (top_left[0], top_left[1] - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)

            # Сохраняем
            output_bgr = cv2.cvtColor(screenshot_with_matches, cv2.COLOR_RGB2BGR)
            cv2.imwrite(self.output_image_path, output_bgr)
            logging.info(f"🖼 Изображение с метками сохранено: {self.output_image_path}")
        else:
            logging.warning("❌ Совпадения не найдены — изображение с метками не создаётся")

        # 6. Проверка
        self.assertGreaterEqual(
            len(matches), 1,
            f"Шаблон '{self.template_path}' не был найден. "
            "Возможные причины:\n"
            "  1. На экране нет кнопки 'ad_pve'\n"
            "  2. Шаблон устарел или не соответствует текущему интерфейсу\n"
            "  3. Порог 0.8 слишком высокий (попробуйте 0.7)\n"
            "  4. Проблемы с масштабированием или сжатием скриншота"
        )

    def test_template_file_is_readable(self):
        """Проверяет, что шаблон можно загрузить как изображение"""
        template = cv2.imread(self.template_path, cv2.IMREAD_GRAYSCALE)
        self.assertIsNotNone(template, f"Не удалось загрузить шаблон как изображение: {self.template_path}")
        logging.info(f"✅ Шаблон загружен успешно: размер {template.shape}")


if __name__ == '__main__':
    unittest.main(verbosity=2)