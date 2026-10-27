import logging
from pathlib import Path

import cv2
import numpy as np
from utils.bluestacks import BlueStacksController

logger = logging.getLogger(__name__)


class GameDetection:
    """
    Класс для визуального анализа экрана игры Rush Royale.
    Поддерживает:
    - Получение скриншотов через ADB
    - Поиск шаблонов (иконок, кнопок)
    - Определение юнитов на поле по шаблону иконки (растянутому под размер клетки)
    - Определение ранга слияния через сравнение контуров (устойчиво к анимации)
    - Кэширование шаблонов
    - Хранение последнего состояния поля при ошибках распознавания
    """

    def __init__(self):
        self.adb = BlueStacksController()
        self.template_cache = {}  # Кэш шаблонов (ранги, юниты)
        self.last_known_field = []  # Последнее успешное состояние поля
        self.selected_units = []  # Юниты, выбранные пользователем в UI
        # unit -> список ключей template_cache (базовый шаблон + визуальные варианты)
        self.unit_template_keys = {}
        logger.info("GameDetection инициализирован")

    def set_selected_units(self, units):
        """Устанавливает выбранные юниты и загружает их шаблоны."""
        self.selected_units = units
        logger.info(f"Выбранные юниты: {units}")
        self._preload_unit_templates()

    def _preload_unit_templates(self):
        """
        Предзагружает цветные шаблоны иконок выбранных юнитов, включая
        визуальные варианты одного и того же юнита
        (assets/templates/units/variants/{unit}_*.png, например
        engineer_shield.png / engineer_fire.png).

        Варианты лежат в подпапке, а не рядом с базовыми шаблонами, потому
        что имя файла в units/ — это канонический ID юнита (см. CLAUDE.md), и
        отличить вариант от самостоятельного юнита по одному лишь имени
        нельзя: witch_doc — отдельный юнит, а не вариант witch, точно так же
        как enchanted_sword — не вариант несуществующего "enchanted".
        Подпапка убирает эту двусмысленность и заодно чинит список юнитов в
        UI, который просто читает units/*.png.

        Живая калибровка 2026-07-29 показала, что engineer в бою реально
        выглядит по-разному в зависимости от состояния (щит — холодный
        жёлто-зелёный тон без свечения, атака — яркое оранжевое пламя),
        и оба этих состояния сильно отличаются от чистого статичного
        engineer.png без подсветки. Один шаблон физически не может хорошо
        совпадать со всеми состояниями сразу, поэтому здесь грузится набор
        вариантов на юнит, а score_units_for_cell берёт лучший (максимальный)
        score среди них.
        """
        units_dir = Path("assets/templates/units")
        for unit in self.selected_units:
            template_path = units_dir / f"{unit}.png"
            if not template_path.exists():
                logger.warning(f"Шаблон не найден: {template_path}")
                continue

            img = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
            if img is None:
                logger.warning(f"Не удалось загрузить изображение: {template_path}")
                continue

            self.template_cache[unit] = img
            template_keys = [unit]
            logger.debug(f"Шаблон загружен: {unit}")

            for variant_path in sorted((units_dir / "variants").glob(f"{unit}_*.png")):
                variant_img = cv2.imread(str(variant_path), cv2.IMREAD_COLOR)
                if variant_img is None:
                    logger.warning(f"Не удалось загрузить вариант: {variant_path}")
                    continue
                variant_key = f"{unit}::{variant_path.stem}"
                self.template_cache[variant_key] = variant_img
                template_keys.append(variant_key)
                logger.debug(f"Вариант шаблона загружен: {variant_key}")

            self.unit_template_keys[unit] = template_keys

    def get_screenshot(self):
        """Делает скриншот через ADB и возвращает изображение как numpy array."""
        logger.debug("Получение скриншота через ADB")
        screenshot_path = self.adb.screenshot("current_screen.png")
        screenshot = cv2.imread(screenshot_path)
        if screenshot is None:
            error_msg = f"Не удалось загрузить скриншот: {screenshot_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)
        return screenshot

    def find_template(self, template_path, threshold=0.9, screenshot=None):
        """
        Ищет шаблон на экране с заданным порогом. Возвращает список координат
        центров совпадений. screenshot — опционально: уже готовый кадр (BGR
        numpy array), чтобы не делать отдельный ADB-скриншот на каждый вызов
        при последовательной проверке нескольких шаблонов подряд (см.
        classify_screen). По умолчанию (None) берёт свежий скриншот сам, как раньше.
        """
        template_path = Path(template_path)
        if not template_path.exists():
            raise FileNotFoundError(f"Шаблон не найден: {template_path}")

        if str(template_path) not in self.template_cache:
            template = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
            if template is None:
                raise FileNotFoundError(f"Не удалось загрузить шаблон: {template_path}")
            self.template_cache[str(template_path)] = template
        else:
            template = self.template_cache[str(template_path)]

        if screenshot is None:
            screenshot = self.get_screenshot()
        screenshot_gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        result = cv2.matchTemplate(screenshot_gray, template, cv2.TM_CCOEFF_NORMED)
        locations = np.where(result >= threshold)
        h, w = template.shape
        matches = []
        # np.where даёт (row_indices, col_indices) = (ys, xs); locations[::-1] = (xs, ys) —
        # порядок распаковки переменных ниже должен совпадать с этим порядком.
        for x, y in zip(*locations[::-1]):
            center_x = x + w // 2
            center_y = y + h // 2
            matches.append((center_x, center_y))
        return matches

    def classify_screen(self, candidates):
        """
        Определяет, какой из заданных экранов сейчас показан — берёт ОДИН
        скриншот и проверяет по нему все шаблоны-кандидаты по очереди (а не
        отдельный ADB-скриншот на каждый, см. find_template(screenshot=...)).
        candidates — список (name, template_path, threshold) в порядке
        проверки. Возвращает name первого совпадения выше порога, либо None,
        если ни один не подошёл. Чисто CV, без тапов — что делать с
        результатом решает вызывающий код (bot_controller.py).
        """
        screenshot = self.get_screenshot()
        for name, template_path, threshold in candidates:
            if self.find_template(
                template_path, threshold=threshold, screenshot=screenshot
            ):
                return name
        return None

    def _extract_contour_from_image(self, image_gray):
        """
        Извлекает наибольший внешний контур из grayscale-изображения.
        Порог подбирается автоматически (Otsu), а не фиксированным числом:
        калибровка на живом устройстве показала, что фиксированный threshold=50
        на части шаблонов рангов и часть реальных клеток поля заливает весь кадр
        одним цветом выше порога — cv2.findContours тогда возвращает контур
        размером с всё изображение (ложный "самый большой" контур) вместо
        реальной формы значка ранга.
        """
        _, thresh = cv2.threshold(
            image_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if contours:
            return max(contours, key=cv2.contourArea)
        return None

    def _load_or_extract_template_contour(self, rank):
        """Загружает шаблон ранга и извлекает его контур. Кэширует результат."""
        cache_key = f"contour_rank_{rank}"

        if cache_key in self.template_cache:
            return self.template_cache[cache_key]

        template_dir = Path("assets/templates/units/unit_rank")
        # Наименование шаблонов на диске не унифицировано (rank2.png, rank1_bin.png,
        # rank_1.png/.jpeg) — перебираем известные варианты и берём первый существующий.
        candidates = [
            template_dir / f"rank{rank}.png",
            template_dir / f"rank{rank}_bin.png",
            template_dir / f"rank_{rank}.png",
            template_dir / f"rank_{rank}.jpeg",
        ]
        template_path = next((p for p in candidates if p.exists()), None)

        if template_path is None:
            logger.warning(
                f"Шаблон ранга {rank} не найден среди: {[str(p) for p in candidates]}"
            )
            return None

        template_img = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
        if template_img is None:
            logger.warning(f"Не удалось загрузить шаблон: {template_path}")
            return None

        contour = self._extract_contour_from_image(template_img)
        if contour is not None:
            self.template_cache[cache_key] = contour
            logger.debug(f"Контур ранга {rank} загружен и закэширован")
        return contour

    def score_ranks_for_cell(self, cell_roi_gray):
        """
        Считает score (cv2.matchShapes, чем меньше — тем лучше) контура клетки против
        контура каждого шаблона ранга 1..7. Возвращает список (rank, score) по возрастанию.
        Используется и распознаванием, и калибровочным скриптом.
        """
        cell_contour = self._extract_contour_from_image(cell_roi_gray)
        if cell_contour is None or cv2.contourArea(cell_contour) < 30:
            return []

        scores = []
        for level in range(1, 8):
            template_contour = self._load_or_extract_template_contour(level)
            if template_contour is None:
                continue
            score = cv2.matchShapes(
                cell_contour, template_contour, method=1, parameter=0.0
            )
            scores.append((level, float(score)))

        scores.sort(key=lambda item: item[1])
        return scores

    def _detect_merge_level_by_contour(self, cell_roi_gray):
        """
        Определяет ранг слияния по форме контура.
        Использует cv2.matchShapes — устойчив к частичному перекрытию анимацией.
        """
        scores = self.score_ranks_for_cell(cell_roi_gray)
        if not scores:
            return 0

        best_rank, best_score = scores[0]
        return best_rank if best_score < 0.15 else 0

    def _unit_icon_mask(self, shape, radius_fraction=0.35):
        """
        Круглая маска на центр клетки (сам значок юнита), исключающая углы
        квадратной ROI — там рамка клетки, счётчик слияния, подсветка атаки
        и прочие элементы, не относящиеся к юниту.
        """
        h, w = shape[:2]
        mask = np.zeros((h, w), np.uint8)
        cv2.circle(mask, (w // 2, h // 2), int(min(h, w) * radius_fraction), 255, -1)
        return mask

    def _unit_hue_sat_histogram(self, image_bgr):
        """Нормализованная 2D-гистограмма (Hue, Saturation) по круглой маске значка."""
        mask = self._unit_icon_mask(image_bgr.shape)
        hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], mask, [30, 32], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        return hist

    def _cached_template_histogram(self, template_key, cell_w, cell_h):
        """
        Гистограмма шаблона (юнита или одного из его визуальных вариантов),
        растянутого под размер клетки — кэшируется по (ключ, ширина, высота),
        т.к. размер клетки не меняется между тиками и клетками (все 15 клеток
        game_field_matrix одного размера), а без кэша она пересчитывалась бы
        заново на каждую из 15 клеток каждый тик.
        """
        cache_key = ("unit_hist", template_key, cell_w, cell_h)
        if cache_key in self.template_cache:
            return self.template_cache[cache_key]

        template = self.template_cache.get(template_key)
        if template is None:
            return None

        resized = cv2.resize(template, (cell_w, cell_h), interpolation=cv2.INTER_AREA)
        histogram = self._unit_hue_sat_histogram(resized)
        self.template_cache[cache_key] = histogram
        return histogram

    def score_units_for_cell(self, cell_roi):
        """
        Считает score между клеткой и шаблоном каждого выбранного юнита.
        Сравнение — по цветовой гистограмме (Hue+Saturation, cv2.HISTCMP_BHATTACHARYYA)
        на круглой маске значка, а не по прямому пиксельному сопоставлению.

        Живая калибровка (tests/calibrate_detection.py, реальный бой на этажах 10 и 14)
        показала, что прежний подход — TM_CCOEFF_NORMED серого шаблона, растянутого
        под размер клетки, — почти не различал юнитов между собой: правильный юнит
        был лучшим совпадением только в ~1 из 9 проверенных клеток. Свечение анимации
        атаки, рамка клетки и счётчик слияния меняют яркость/контур сильнее, чем
        отличаются друг от друга сами персонажи, поэтому пиксельная корреляция
        сравнивала в основном фон, а не юнита. Гистограмма по кругу в центре клетки
        устойчивее к этому и на тех же живых данных даёт верный топ-1 юнит намного
        чаще (~11/15 против ~2/15 у старого подхода). Юниты с очень похожей палитрой
        (dryad/summoner — оба зелёные) всё ещё иногда путаются между собой — это
        известный остаточный пробел, а не то, что маскирует эта замена. Живой бой
        на этаже 10 (2026-07-28) также показал, что engineer (серый, низкая
        насыщенность) иногда вообще пропадает из detect_field_state — гистограмма
        Hue+Saturation слабо различает низконасыщенные цвета.

        Живая калибровка 2026-07-29 добавила второй слой: engineer в реальном
        бою выглядит по-разному в зависимости от состояния (щит — холодный
        жёлто-зелёный тон, атака — оранжевое пламя), и оба отличаются от
        статичного engineer.png. Поэтому каждый юнит сравнивается со ВСЕМИ
        загруженными вариантами своего шаблона (см. _preload_unit_templates),
        и берётся лучший (максимальный) score среди них — а не только базовый.

        Возвращает список (unit, score) по убыванию, где выше — увереннее совпадение.
        Используется и распознаванием, и калибровочным скриптом (tests/calibrate_detection.py).
        """
        cell_h, cell_w = cell_roi.shape[:2]
        cell_hist = self._unit_hue_sat_histogram(cell_roi)

        scores = []
        for unit in self.selected_units:
            template_keys = self.unit_template_keys.get(unit, [unit])
            best_score = None
            for template_key in template_keys:
                template_hist = self._cached_template_histogram(
                    template_key, cell_w, cell_h
                )
                if template_hist is None:
                    continue
                distance = cv2.compareHist(
                    cell_hist, template_hist, cv2.HISTCMP_BHATTACHARYYA
                )
                score = 1.0 - float(distance)
                if best_score is None or score > best_score:
                    best_score = score

            if best_score is not None:
                scores.append((unit, best_score))

        scores.sort(key=lambda item: item[1], reverse=True)
        return scores

    def _detect_unit_by_template(self, cell_roi, threshold=0.3):
        """
        Определяет юнита по цветовой гистограмме значка (см. score_units_for_cell).
        Кандидаты — только выбранные пользователем юниты (их шаблоны закэшированы).
        threshold=0.3 — грубый отсев пустых/нечитаемых клеток, а не точный
        разделитель между юнитами (это делает argmax по score_units_for_cell);
        нужно ещё уточнить по живым пустым клеткам.
        """
        scores = self.score_units_for_cell(cell_roi)
        if not scores:
            return None

        best_unit, best_score = scores[0]
        return best_unit if best_score > threshold else None

    def detect_field_state(self, game_field_matrix, screenshot=None):
        """
        Основной метод: сканирует все 15 клеток поля и определяет:
        - Какой юнит
        - Какой ранг слияния
        - Где находится (row, col)

        Если распознавание не удалось — возвращает last_known_field.

        screenshot — опционально готовый кадр, чтобы не тянуть отдельный
        ADB-скриншот, когда вызывающий уже снял его для других проверок
        (см. find_template с тем же параметром).
        """
        current_field = []
        if screenshot is None:
            screenshot = self.get_screenshot()

        for row_idx, row in enumerate(game_field_matrix["matrix"]):
            for col_idx, cell in enumerate(row):
                region = cell["region"]
                x1, y1, x2, y2 = region
                cell_roi = screenshot[y1:y2, x1:x2].copy()

                # 1. Определение юнита
                unit = self._detect_unit_by_template(cell_roi)

                # 2. Ранг слияния: пока всегда "неизвестен".
                #
                # Контурное определение (_detect_merge_level_by_contour)
                # отключено намеренно, а не забыто. Ранг — это ФОРМА контура
                # под пешкой (ранг 1 — круг, 4 — ромб, 7 — восьмиугольник,
                # см. assets/templates/units/unit_rank/), то есть сам метод
                # matchShapes и шаблоны верные. Сломана добыча контура:
                # _extract_contour_from_image берёт Otsu по ВСЕЙ клетке и
                # отдаёт на сравнение силуэт юнита вместо этого контура.
                # На живом поле это давало
                # matchShapes 0.009-23.1 при пороге 0.15, то есть случайный
                # шум: один и тот же меч в соседних тиках получал то "L4",
                # то ничего, из-за чего юниты одного типа разъезжались по
                # разным группам (тип, ранг) и НЕ СЛИВАЛИСЬ вовсе. Пока контур
                # не научились выделять правильно, честнее не знать ранг, чем
                # знать его неверно. Сама функция и score_ranks_for_cell
                # оставлены — ими пользуется tests/calibrate_detection.py и с
                # них же начнётся починка.
                merge_level = 0

                # 3. Сохранение результата.
                # Клетка считается занятой по одному лишь опознанному юниту:
                # ранг — дополнительная информация, а не условие. Раньше
                # неудачное чтение ранга выбрасывало клетку целиком, и она
                # становилась неотличима от пустой — на живом поле, забитом
                # под завязку, распознавание ранга проходило только в 3 клетках
                # из 15 (contour matchShapes давал 0.7-23 при пороге 0.15), бот
                # видел 12 "свободных" клеток и бесконечно призывал юнитов
                # вместо слияний (2026-07-31). merge_level == 0 означает
                # "ранг неизвестен".
                if unit:
                    current_field.append(
                        {
                            "row": row_idx,
                            "col": col_idx,
                            "unit": unit,
                            "merge_level": merge_level,
                            "center": ((x1 + x2) // 2, (y1 + y2) // 2),
                            "region": region,
                        }
                    )

        # 4. Защита от сбоев
        if not current_field and self.last_known_field:
            logger.warning(
                "Не удалось определить состояние поля — используется предыдущее"
            )
            return self.last_known_field

        self.last_known_field = current_field
        logger.debug(f"Текущее состояние поля: {len(current_field)} юнитов распознано")
        return current_field
