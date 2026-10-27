import sys
from pathlib import Path
import cv2
import time
import random
import json

# Добавляем корень проекта в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Импорты
from core.detection import GameDetection
from utils.bluestacks import BlueStacksController


def load_button_coords(button_name="ad_view_button"):
    """Загружает координаты кнопки из JSON-файла"""
    coords_file = Path("assets/config/button_coords.json")
    try:
        with open(coords_file, "r", encoding="utf-8") as f:
            coords = json.load(f)
        if button_name not in coords:
            raise KeyError(f"Кнопка '{button_name}' не найдена в {coords_file}")
        return coords[button_name]["region"]
    except Exception as e:
        print(f"❌ Ошибка загрузки координат кнопки: {e}")
        return None


def test_ad_click_from_json():
    """Тест: кликнуть в случайную точку внутри зоны кнопки, указанной в JSON"""
    print("🎬 Запущен тест клика по рекламной кнопке (по координатам из JSON)")
    detection = GameDetection()
    adb = BlueStacksController()

    output_image = "debug_ad_click_visual.png"

    # Шаг 1: Скриншот
    print("📸 Делаем скриншот экрана...")
    screenshot = detection.get_screenshot()
    if screenshot is None:
        print("❌ Не удалось получить скриншот")
        return False

    debug_img = screenshot.copy()
    height, width = debug_img.shape[:2]
    print(f"📐 Размер скриншота: {width}x{height}")

    # Шаг 2: Загружаем координаты кнопки из JSON
    region = load_button_coords("ad_view_button")
    if not region:
        print("❌ Не удалось загрузить координаты кнопки из JSON")
        return False

    x1, y1, x2, y2 = region
    print(f"📍 Область кнопки из JSON: ({x1}, {y1}) → ({x2}, {y2})")

    # Визуализация области кнопки
    cv2.rectangle(debug_img, (x1, y1), (x2, y2), (255, 0, 0), 2)
    cv2.putText(debug_img, "AD Button Zone", (x1, y1 - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 1)

    # Шаг 3: Случайный клик внутри зоны
    click_x = random.randint(x1, x2)
    click_y = random.randint(y1, y2)
    print(f"🖱 Случайная точка внутри зоны: ({click_x}, {click_y})")

    # Визуализация точки клика
    cv2.drawMarker(debug_img, (click_x, click_y), (0, 255, 0), cv2.MARKER_TILTED_CROSS, 15, 2)

    # Сохраняем скриншот с визуализацией
    cv2.imwrite(output_image, debug_img)
    print(f"🖼 Скриншот с меткой клика сохранён: {output_image}")

    # Шаг 4: Клик
    print("🖱 Выполняем клик через ADB...")
    adb.tap(click_x, click_y)
    print(f"✅ Клик отправлен: tap {click_x} {click_y}")
    time.sleep(0.7)

    # Повторный клик (на случай промаха)
    adb.tap(click_x, click_y)
    print(f"✅ Повторный клик отправлен")

    # Шаг 5: Начинаем бесконечную запись скриншотов каждые 10 секунд
    print("\n🎥 Начинаем запись скриншотов каждые 10 секунд. Остановите вручную (Ctrl+C)...")
    counter = 1
    save_dir = Path("assets/templates/icons/ad_viewer_marks")
    save_dir.mkdir(exist_ok=True)

    try:
        while True:
            timestamp = time.strftime("%H_%M_%S")
            filename = f"ad_frame_{counter:03d}_{timestamp}.png"
            filepath = save_dir / filename

            # Делаем скриншот
            screenshot_path = adb.screenshot(str(filepath))
            print(f"📸 [{counter}] Скриншот сохранён: {screenshot_path}")

            # Ждём 10 секунд
            time.sleep(10)
            counter += 1

    except KeyboardInterrupt:
        print("\n🛑 Запись скриншотов остановлена пользователем.")
        return True

    return True


if __name__ == "__main__":
    success = test_ad_click_from_json()
    if success:
        print("🎉 Тест пройден: клик по кнопке из JSON выполнен, запись скриншотов завершена")
    else:
        print("❌ Тест не пройден: не удалось найти или нажать кнопку")