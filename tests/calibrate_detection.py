"""
Калибровочный скрипт для core.detection.GameDetection.

Требует запущенный BlueStacks с ADB debugging и разрешением 900x1600
(см. CLAUDE.md). Ничего не тапает — только читает экран и печатает/рисует
диагностику, чтобы можно было подобрать пороги (threshold в
_detect_unit_by_template, 0.15 в _detect_merge_level_by_contour).

Запуск:
    python tests/calibrate_detection.py
    python tests/calibrate_detection.py dryad summoner harlequin witch engineer
"""
import json
import sys
from pathlib import Path

import cv2

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.detection import GameDetection

SETTINGS_FILE = project_root / "assets/config/settings.json"
COORDS_FILE = project_root / "assets/config/button_coords.json"
OUTPUT_IMAGE = project_root / "debug_field_calibration.png"


def load_units_from_args_or_settings():
    if len(sys.argv) > 1:
        return sys.argv[1:]
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            settings = json.load(f)
        units = [u for u in settings.get("units", []) if u]
        if units:
            return units
    raise SystemExit(
        "Не заданы юниты: передай их аргументами командной строки "
        "или сохрани настройки через UI (assets/config/settings.json)."
    )


def main():
    units = load_units_from_args_or_settings()
    print(f"Юниты для калибровки: {units}")

    detection = GameDetection()
    detection.set_selected_units(units)

    with open(COORDS_FILE, "r", encoding="utf-8") as f:
        game_field_matrix = json.load(f)["game_field_matrix"]

    screenshot = detection.get_screenshot()
    overlay = screenshot.copy()

    for row in game_field_matrix["matrix"]:
        for cell in row:
            x1, y1, x2, y2 = cell["region"]
            cell_roi = screenshot[y1:y2, x1:x2].copy()

            unit_scores = detection.score_units_for_cell(cell_roi)
            cell_gray = cv2.cvtColor(cell_roi, cv2.COLOR_BGR2GRAY)
            rank_scores = detection.score_ranks_for_cell(cell_gray)

            label = "empty"
            if unit_scores:
                best_unit, best_score = unit_scores[0]
                label = f"{best_unit}:{best_score:.2f}"
            if rank_scores:
                best_rank, best_rank_score = rank_scores[0]
                label += f" R{best_rank}:{best_rank_score:.2f}"

            print(
                f"[{cell['row']},{cell['col']}] "
                f"units={[(u, round(s, 2)) for u, s in unit_scores]} "
                f"ranks={[(r, round(s, 2)) for r, s in rank_scores]}"
            )

            cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 255, 0), 1)
            cv2.putText(
                overlay, label, (x1 + 2, y1 + 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1, cv2.LINE_AA
            )

    cv2.imwrite(str(OUTPUT_IMAGE), overlay)
    print(f"\nАннотированный скриншот сохранён: {OUTPUT_IMAGE}")
    print("Сверь подписи с реальным полем и подбери пороги в core/detection.py при необходимости.")


if __name__ == "__main__":
    main()
