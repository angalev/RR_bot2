"""
УСТАРЕЛО (2026-07-28) — не основной способ добавления нового этажа, см. ниже.

Изначально задумывался как dev-инструмент для подбора макроса навигации к
конкретному этажу через OCR (pytesseract). Живая калибровка показала две
проблемы с самой идеей "макрос фиксированных шагов":
1. Tesseract ненадёжно читает стилизованный шрифт этой игры — "Floor"/
   "Endless Floor" распознаётся как "Ficor"/"Feels." и подобный мусор.
2. Даже независимо от OCR: дистанция скролла на один и тот же жест на ленте
   глав экрана Dungeons непредсказуема между запусками (один и тот же свайп
   то не регистрируется вовсе, то прокручивает на 1 главу, то на 4 сразу) —
   любой макрос "сделай N свайпов на такое-то расстояние" не воспроизводится.

Из-за этого core/bot_controller.py:_select_floor устроен иначе: closed-loop
поиск маленькими шагами со скриншотом и сверкой по шаблону (find_template)
после каждого шага, с коррекцией направления при перелёте — а не по счётчику
шагов и не через OCR. Единственная всё ещё ручная часть при добавлении нового
этажа — вырезать *отличительный* фрагмент карточки (assets/templates/icons/
floor_{N}_marker.png) и добавить {"chapter": X} в button_coords.json →
floor_navigation.floors. Важно: обрезка должна выделять именно различающие
цифры номера этажа, а не всю надпись "Floor N" целиком и не общий фон
карточки — полная надпись "Floor 10" давала false-positive score 0.93 против
чужого этажа (Floor 11/12), обрезка на одни цифры развела true/false с 0.997
против 0.82 (см. floor_10_marker.png как пример).

Этот файл оставлен как справка о неудачном подходе и для возможного OCR
для ЧАСТИ задачи, где текст читается надёжно (в отличие от "Floor N",
"Chapter N" — короткие числа на однотонном баннере — распознавались с
хорошей уверенностью), но сам процесс поиска этажа теперь в bot_controller.py.
"""

import json
import shutil
import sys
import time
from pathlib import Path

import cv2
import pytesseract

if shutil.which("tesseract") is None:
    # PATH обновляется установщиком Tesseract только для новых процессов —
    # уже запущенная сессия терминала его не подхватывает без перезапуска.
    # Подстраховка: ищем бинарник по стандартному пути установки на Windows,
    # чтобы не требовать перезапуск терминала/перезагрузку от разработчика.
    for candidate in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if Path(candidate).exists():
            pytesseract.pytesseract.tesseract_cmd = candidate
            break

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from utils.bluestacks import BlueStacksController

COORDS_FILE = project_root / "assets/config/button_coords.json"

SCROLL_TO_EARLIER = (476, 456, 416, 1412)  # к более ранним главам (вверх по списку)
SCROLL_TO_LATER = (416, 1412, 476, 456)  # к более поздним главам (вниз по списку)
SCROLL_TO_TOP_STEPS = 12
MAX_SCROLL_STEPS = 30


def ocr_lines(screenshot_bgr):
    """[(text, center_x, center_y), ...] — строки текста на экране, слова внутри строки склеены."""
    rgb = cv2.cvtColor(screenshot_bgr, cv2.COLOR_BGR2RGB)
    data = pytesseract.image_to_data(rgb, output_type=pytesseract.Output.DICT)

    by_line = {}
    for i, word in enumerate(data["text"]):
        word = word.strip()
        if not word:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        entry = by_line.setdefault(key, {"words": [], "boxes": []})
        entry["words"].append(word)
        entry["boxes"].append(
            (data["left"][i], data["top"][i], data["width"][i], data["height"][i])
        )

    lines = []
    for entry in by_line.values():
        text = " ".join(entry["words"])
        xs1 = [b[0] for b in entry["boxes"]]
        ys1 = [b[1] for b in entry["boxes"]]
        xs2 = [b[0] + b[2] for b in entry["boxes"]]
        ys2 = [b[1] + b[3] for b in entry["boxes"]]
        lines.append((text, (min(xs1) + max(xs2)) // 2, (min(ys1) + max(ys2)) // 2))
    return lines


def find_line(lines, prefix):
    for text, x, y in lines:
        if text.strip().lower().startswith(prefix):
            return text, x, y
    return None


def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "Использование: python tests/calibrate_floor_selection.py <номер_этажа>"
        )
    target_floor = int(sys.argv[1])

    adb = BlueStacksController()
    macro_steps = []

    print(f"Прокручиваю к самому началу списка глав ({SCROLL_TO_TOP_STEPS} шагов)...")
    for _ in range(SCROLL_TO_TOP_STEPS):
        adb.swipe(*SCROLL_TO_EARLIER, 400)
        time.sleep(0.4)
    macro_steps.append(
        {
            "action": "swipe",
            "swipe": list(SCROLL_TO_EARLIER),
            "duration": 400,
            "wait": 0.4,
            "note": f"x{SCROLL_TO_TOP_STEPS} — прокрутка к самому началу списка глав",
        }
    )

    print(f"Ищу 'Floor {target_floor}'...")
    for step_num in range(MAX_SCROLL_STEPS):
        screenshot = cv2.imread(adb.screenshot("assets/screenshots/_floor_calib.png"))
        lines = ocr_lines(screenshot)

        floor_line = find_line(lines, f"floor {target_floor}")
        if (
            floor_line is not None
            and floor_line[0].strip().lower() == f"floor {target_floor}"
        ):
            text, x, y = floor_line
            print(f"\nНайден 'Floor {target_floor}' в точке ({x}, {y}).")
            print("Уточни на скриншоте assets/screenshots/_floor_calib.png координаты")
            print("кнопки Play под этой карточкой и добавь последним шагом:")
            print(
                json.dumps(
                    {
                        "action": "tap",
                        "tap": ["<PLAY_X>", "<PLAY_Y>"],
                        "wait": 2.0,
                        "note": f"кнопка Play на карточке Floor {target_floor}",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            print("\nПолный макрос для button_coords.json → floor_navigation.floors:")
            print(json.dumps({"steps": macro_steps}, ensure_ascii=False, indent=2))
            return

        chapter_line = find_line(lines, "chapter")
        # "floor" не обязательно префикс строки — например "Endless Floor"
        # (глава уже развёрнута и это её единственный этаж/режим).
        has_floor_line = any("floor" in text.strip().lower() for text, _, _ in lines)
        if chapter_line is not None and not has_floor_line:
            text, x, y = chapter_line
            print(f"Похоже, глава свёрнута ('{text}' @ {x},{y}) → разворачиваю")
            adb.tap(x, y)
            macro_steps.append(
                {
                    "action": "tap",
                    "tap": [x, y],
                    "wait": 1.5,
                    "note": f"развернуть баннер '{text}'",
                }
            )
            time.sleep(1.5)
            continue

        adb.swipe(*SCROLL_TO_LATER, 400)
        macro_steps.append(
            {
                "action": "swipe",
                "swipe": list(SCROLL_TO_LATER),
                "duration": 400,
                "wait": 0.6,
                "note": "докрутка вперёд по списку",
            }
        )
        time.sleep(0.6)

    print(f"\n'Floor {target_floor}' не найден за {MAX_SCROLL_STEPS} прокруток.")


if __name__ == "__main__":
    main()
