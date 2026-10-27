import sys
from pathlib import Path
import time
import cv2

# Добавляем корень проекта в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Импорты
from core.detection import GameDetection
from utils.bluestacks import BlueStacksController

# Пути
AD_BUTTON_TEMPLATE = "assets/templates/icons/ad_pve.png"
SCREENSHOT_DIR = Path("assets/templates/icons/ad_viewer_marks")
COUNTER_FILE = "ad_screenshot_counter.txt"

def read_counter():
    if Path(COUNTER_FILE).exists():
        with open(COUNTER_FILE, "r") as f:
            return int(f.read().strip())
    return 1

def save_counter(value):
    with open(COUNTER_FILE, "w") as f:
        f.write(str(value))

def ensure_dir(path):
    path.mkdir(parents=True, exist_ok=True)

def run_ad_click_debug():
    print("🎬 [DEBUG] Режим отладки кликов: запись + лог ADB")
    detection = GameDetection()
    adb = BlueStacksController()
    counter = read_counter()
    ensure_dir(SCREENSHOT_DIR)

    # Проверка шаблона
    if not Path(AD_BUTTON_TEMPLATE).exists():
        print(f"❌ Файл шаблона не найден: {AD_BUTTON_TEMPLATE}")
        return False

    # Шаг 1: Поиск кнопки
    print("🔍 Ищем кнопку 'Просмотреть рекламу'...")
    # Теперь find_template возвращает координаты, уже сконвертированные под ADB
    matches = detection.find_template(AD_BUTTON_TEMPLATE, threshold=0.95)
    if not matches:
        print("❌ Кнопка не найдена на экране.")
        return False

    # Берем первый результат — он уже в формате ADB (landscape)
    adb_x, adb_y = matches[0]
    print(f"🎯 Найдена кнопка. Координаты для ADB: ({adb_x}, {adb_y})")

    # Делаем клик
    print(f"\n🔄 Выполняем двойной клик через ADB:")
    print(f"  → adb tap {adb_x} {adb_y}")

    try:
        adb.tap(adb_x, adb_y)
        print(f"✅ [ADB] Первый клик отправлен: tap {adb_x} {adb_y}")
        time.sleep(1)

        adb.tap(adb_x, adb_y)
        print(f"✅ [ADB] Второй клик отправлен: tap {adb_x} {adb_y}")
        time.sleep(2)
    except Exception as e:
        print(f"❌ Ошибка при клике: {e}")
        return False

    # Шаг 2: Запись скриншотов
    print(f"\n📸 Начинаем запись скриншотов (6 кадров, 10 сек между)")
    for i in range(6):
        print(f"📷 [{i+1}/6] Делаем скриншот...")
        if i > 0:
            time.sleep(10)

        screenshot_path = adb.screenshot(f"ad_frame_{i+1}.png")
        img = cv2.imread(screenshot_path)
        if img is not None:
            final_path = SCREENSHOT_DIR / f"ad_{counter:03d}_{time.strftime('%H_%M_%S')}.png"
            cv2.imwrite(str(final_path), img)
            print(f"✅ Сохранено: {final_path}")
            counter += 1
        else:
            print(f"❌ Не удалось загрузить скриншот: {screenshot_path}")

    save_counter(counter)
    print("🎉 ✅ Запись завершена. Клик был отправлен.")
    return True


if __name__ == "__main__":
    success = run_ad_click_debug()
    if not success:
        print("❌ Тест завершился с ошибкой.")