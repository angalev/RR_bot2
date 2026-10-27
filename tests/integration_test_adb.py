import sys
from pathlib import Path
import time

# Добавляем корневую директорию проекта в Python путь
project_root = Path(__file__).parent.parent  # Два уровня вверх: из tests/ в RR_Bot/
sys.path.insert(0, str(project_root))

# Теперь импорт из utils будет работать
from utils.adb import ADBController


def test_real_device():
    adb = ADBController()

    print("Делаем клик на (500, 1000)")
    adb.tap(500, 1000)
    time.sleep(1)

    print("Делаем свайп")
    adb.swipe(500, 1500, 500, 500, 800)
    time.sleep(1)

    print("Делаем скриншот")
    path = adb.screenshot()
    print(f"Скриншот сохранён: {path}")


if __name__ == "__main__":
    test_real_device()