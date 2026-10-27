import json
import logging
import threading
import time
from pathlib import Path
from typing import ClassVar

from core.actions import GameActions
from core.detection import GameDetection
from utils.bluestacks import BlueStacksController

logger = logging.getLogger(__name__)


class BotController:
    """Контроллер бота — управляет логикой работы"""

    def __init__(self):
        self.running = False
        self.log_callback = None  # ← для вывода в GUI
        self.watch_ads = False  # ← галочка "Смотреть рекламу", ставится в start()
        # Настройки боя для повторного запуска навигации после каждого боя
        # (цикл фарма, см. _start_next_battle) — заполняются в start().
        self._battle_params = None
        self._battles_played = 0
        self._navigating = False
        self._last_loop_state = None

    def set_log_callback(self, callback):
        """Устанавливает объект GUI для лога и обновления состояния поля"""
        self.log_callback = callback

    def log(self, message):
        """Записывает сообщение в лог и отправляет в GUI"""
        logger.info(message)
        if self.log_callback:
            try:
                self.log_callback.update_battle_log(message)
            except AttributeError:
                # На случай, если callback — просто функция
                self.log_callback(message)

    # Порядок проверки экранов в classify_screen — см. assets/config/
    # button_coords.json → screen_phases.phases за сами шаблоны/пороги.
    # in_battle сюда не входит — определяется отдельно в _detect_current_phase.
    # Порядок значим: ad_chest_offer/support_chest_dialog обязаны идти ДО
    # post_battle_results — шаблон post_battle_continue.png ложно срабатывает
    # на обоих экранах сундука (0.972 и 0.916 при пороге 0.8, замерено
    # 2026-07-30), так что при обратном порядке сундук никогда не опознается.
    _SCREEN_PHASE_ORDER = (
        "home_screen",
        "floor_select",
        "card_select",
        "ad_chest_offer",
        "support_chest_dialog",
        "post_battle_results",
        "loading",
    )
    # UI-хром экрана боя (иконка приоритета слияния в углу) — ЕДИНСТВЕННЫЙ
    # признак того, что идёт бой.
    #
    # Раньше признаком считался ещё и непустой detect_field_state(), причём
    # достаточным. Это оказалось грубо неверно: на главном экране ряд
    # портретов своей команды попадает в клетки game_field_matrix, детектор
    # честно опознаёт там "harlequin (L4) x2" — и бот, запущенный из меню,
    # решал, что бой уже идёт, пропускал всю навигацию и восемь минут
    # "призывал юнитов" в главное меню (воспроизведено вживую 2026-07-31).
    # Ложный отрицательный у хрома безвреден (просто пройдём навигацию с
    # начала), ложный положительный у поля — нет, поэтому признак один.
    _IN_BATTLE_CHROME_TEMPLATE = "assets/templates/icons/battle_priority_icon.png"
    _IN_BATTLE_CHROME_THRESHOLD = 0.8

    def _in_battle(self, screenshot=None):
        """Идёт ли бой прямо сейчас — по UI-хрому боя в углу экрана."""
        return bool(
            self.detection.find_template(
                self._IN_BATTLE_CHROME_TEMPLATE,
                threshold=self._IN_BATTLE_CHROME_THRESHOLD,
                screenshot=screenshot,
            )
        )

    def _detect_current_phase(self, coords):
        """
        Определяет, на каком экране сейчас игра — чтобы start() мог
        пропустить уже пройденные шаги навигации, если бота запустили не с
        главного меню (например, поверх уже идущего боя). "in_battle" —
        строго по UI-хрому боя, см. _IN_BATTLE_CHROME_TEMPLATE. Остальные
        фазы — через classify_screen по конфигу screen_phases (см.
        _SCREEN_PHASE_ORDER).
        Возвращает имя фазы или "unknown", если ничего не подошло.
        """
        if self._in_battle():
            return "in_battle"

        phases_config = coords.get("screen_phases", {}).get("phases", {})
        candidates = [
            (name, phases_config[name]["template"], phases_config[name]["threshold"])
            for name in self._SCREEN_PHASE_ORDER
            if name in phases_config
        ]
        return self.detection.classify_screen(candidates) or "unknown"

    def start(
        self,
        units,
        floor=None,
        green_card=False,
        golden_card=False,
        pve_type=None,
        priorities=None,
        watch_ads=False,
    ):
        """Запускает бота с основной логикой запуска PVE и мониторингом поля"""
        self.running = True
        # Галочка "Смотреть рекламу" в UI: определяет, какую кнопку жать в
        # модалке пост-боевого сундука — Watch Ads или Continue.
        self.watch_ads = watch_ads
        self.log(
            f"🎮 Бот запущен: этаж={floor}, green={green_card}, golden={golden_card}, "
            f"PVE={pve_type}, реклама={'смотрим' if watch_ads else 'пропускаем'}"
        )

        # Устанавливаем выбранные юниты в detection
        self.detection = GameDetection()
        self.detection.set_selected_units(units)

        # Юниты и приоритеты слияния (чекбоксы "⬆️" в UI, MainWindow.get_unit_priorities) —
        # индексы соответствуют друг другу по позиции в units.
        self.units = units
        self.priorities = priorities if priorities is not None else [False] * len(units)
        self._last_hero_activation = 0.0
        self._last_harlequin_copy = 0.0

        # Инициализируем ADB и получаем доступ к Tk-окну
        self.adb = BlueStacksController()
        self.actions = GameActions(self.adb)
        self.root_window = self.log_callback.root  # Получаем root из MainWindow

        # Загружаем координаты
        coords_file = Path("assets/config/button_coords.json")
        try:
            with open(coords_file, "r", encoding="utf-8") as f:
                coords = json.load(f)
        except Exception as e:
            self.log(f"❌ Ошибка загрузки координат: {e}")
            self.running = False
            return

        # Параметры боя сохраняем ДО определения фазы: они нужны и когда бота
        # запустили поверх уже идущего боя — после его окончания цикл фарма
        # пойдёт по ним так же, как после боя, который бот начал сам.
        self._battle_params = {
            "floor": floor,
            "green_card": green_card,
            "golden_card": golden_card,
            "pve_type": pve_type,
        }
        self._battles_played = 0

        # Определяем текущую фазу игры — если бой уже идёт (бота запустили
        # не с главного меню, а поверх уже начатого матча), не проходим всю
        # навигацию заново, а сразу подключаемся к бою. Остальные фазы
        # (floor_select/card_select/post_battle_results/loading) пока только
        # логируются — навигация с главного меню ниже всё ещё считается
        # безопасным дефолтом для них (v1 этой фичи, см. CLAUDE.md).
        phase = self._detect_current_phase(coords)
        self.log(f"🔎 Определена текущая фаза игры: {phase}")
        if phase == "in_battle":
            self.log("⚔️ Бой уже идёт — пропускаю навигацию по меню")
            self.log_callback.post_to_ui(self._battle_loop)
            return

        self._navigation_cycle(coords)

    def _navigation_cycle(self, coords):
        """
        Проводит игру от главного меню до начала боя: главный экран → Start PVE
        → выбор этажа → карты → запуск PVE. Блокирующий и долгий (десятки
        секунд ожиданий и скроллов), поэтому вызывается только из фонового
        потока — из start() (его уже запускает поток UI) либо из
        _start_next_battle() после окончания предыдущего боя.
        """
        self._navigating = True
        try:
            self._navigate_to_battle(coords)
        finally:
            self._navigating = False

    def _navigate_to_battle(self, coords):
        """Тело цикла навигации, см. _navigation_cycle."""
        params = self._battle_params
        floor = params["floor"]
        green_card = params["green_card"]
        golden_card = params["golden_card"]
        pve_type = params["pve_type"]

        start_pve_region = coords["start_pve_button"]["region"]
        green_card_region = coords["green_card_button"]["region"]
        golden_card_region = coords["golden_card_button"]["region"]
        pve_buttons = {
            "random": coords["start_pve_random"]["region"],
            "with_friend": coords["start_pve_friend"]["region"],
            "with_clan": coords["start_pve_clan"]["region"],
        }

        # Якорь главного экрана — тот же, что и у фазы home_screen: путь и
        # порог берём из конфига, а не дублируем строкой здесь, иначе при
        # перекалибровке шаблона легко поправить одно место и забыть второе.
        home_anchor = coords["screen_phases"]["phases"]["home_screen"]

        # Основной цикл: ищем главный экран
        while self.running:
            # Шаг 1: Ищем главный экран
            self.log("🔍 Ищем главный экран...")
            if not self._wait_for_template(
                home_anchor["template"],
                threshold=home_anchor["threshold"],
                timeout=10,
            ):
                # Сначала пробуем закрыть известные пост-боевые экраны: они
                # не реагируют на "Назад", и без этого бот залипал на экране
                # итогов боя навсегда (см. _dismiss_post_battle).
                if self._dismiss_post_battle(coords, self.detection.get_screenshot()):
                    time.sleep(2)
                    continue
                self.log("🔙 Не найден → нажимаем 'Назад'")
                self.adb.run_adb_command("shell input keyevent 4")
                time.sleep(2)
                continue

            self.log("✅ Главный экран найден")

            # Шаг 2: Нажимаем Start PVE
            self.log(f"🖱 Нажимаю 'Start PVE' → {start_pve_region}")
            self.adb.tap_region(start_pve_region)
            time.sleep(2)

            # Шаг 3-4: находим и открываем нужный этаж (любая глава).
            # Номер не приводится к int: "endless" — такой же полноправный
            # идентификатор этажа, как "1".."13" (см. _normalize_floor_id).
            target_floor = self._normalize_floor_id(floor)
            if not self._select_floor(target_floor):
                self.log(f"❌ Этаж {target_floor} не найден → перезапуск цикла")
                continue

            # Шаг 5: Обработка карт
            if golden_card and green_card:
                self.log(
                    "🟨🟩 Выбраны обе карты → активируем: сначала Golden, потом Green"
                )
                self.adb.tap_region(golden_card_region)
                self.log("🟨 Нажата 'Golden Card'")
                time.sleep(2)
                self.adb.tap_region(green_card_region)
                self.log("🟢 Нажата 'Green Card'")
                time.sleep(2)
            elif golden_card:
                self.log(f"🟨 Нажимаю 'Golden Card' → {golden_card_region}")
                self.adb.tap_region(golden_card_region)
                time.sleep(2)
            elif green_card:
                self.log(f"🟢 Нажимаю 'Green Card' → {green_card_region}")
                self.adb.tap_region(green_card_region)
                time.sleep(2)
            else:
                # Раньше здесь стоял двойной тап по 'Green Card'. Это ломало
                # запуск: когда карт нет (или они на кулдауне), тап открывает
                # информационный диалог "Treasure Maps" поверх экрана выбора
                # союзников, второй тап жмёт уже внутрь диалога, а следующий
                # тап по кнопке PVE попадает в него же — бой так и не
                # начинается (воспроизведено вживую 2026-07-31). Играть без
                # карты игра разрешает, так что не жмём ничего.
                self.log("⚪ Ни одной карты — играем без карты")

            # Диалог поверх экрана выбора союзников (те же "Treasure Maps")
            # съел бы тап по кнопке PVE, поэтому убеждаемся, что экран чист.
            if not self._ensure_card_select_ready(coords):
                self.log("❌ Экран выбора союзников перекрыт → перезапуск цикла")
                continue

            # Шаг 6: Запуск PVE по типу
            if pve_type not in pve_buttons:
                self.log(f"❌ Неизвестный тип PVE: {pve_type}")
                self.running = False
                return

            pve_region = pve_buttons[pve_type]
            self.log("🎉 Логика завершена: бой начат")
            time.sleep(5)  # Ждём 5 сек — чтобы игра загрузилась

            # Запускаем игровой цикл на главном потоке Tkinter — start()
            # теперь выполняется в фоновом потоке (см. MainWindow._run_bot_start),
            # а _battle_loop() трогает виджеты и планирует сам себя через
            # root_window.after(), что небезопасно делать не из главного
            # потока. post_to_ui кладёт вызов в очередь, которую опустошает
            # цикл событий Tkinter — после этого _battle_loop уже выполняется
            # на главном потоке и все дальнейшие self-reschedule через
            # after() тоже безопасны.
            self.log_callback.post_to_ui(self._battle_loop)

            # Запускаем бой
            btn_names = {
                "random": "рандом",
                "with_friend": "с другом",
                "with_clan": "с сокланом",
            }
            self.log(f"🚀 Запуск PVE: {btn_names[pve_type]} → {pve_region}")
            self.adb.tap_region(pve_region)
            time.sleep(2)
            break

    BATTLE_LOOP_INTERVAL_MS = 3000  # Требует подбора на реальном устройстве
    # Вне боя тик чаще: между тапом по кнопке запуска PVE и появлением поля
    # проходит 15-20 секунд, и на трёхсекундном шаге начало боя ловится с
    # запозданием до тика — а это потерянные первые секунды волны. Тем же
    # интервалом быстрее проходится и пост-боевая цепочка (два Continue,
    # сундук), где каждый экран — отдельный тик.
    OUT_OF_BATTLE_INTERVAL_MS = 1000
    # Способность героя не привязана к конкретному юниту — общий кулдаун
    # (в живом бою наблюдался ~42-46 сек, зависит от уровня героя). Лишний тап,
    # когда способность ещё не готова, безвреден — игра его просто игнорирует,
    # поэтому не проверяем состояние кнопки, а просто тапаем не чаще интервала.
    HERO_ACTIVATION_INTERVAL_S = 45

    def _maybe_activate_hero(self):
        """Активирует способность героя, если прошло достаточно времени с прошлого раза."""
        now = time.monotonic()
        if now - self._last_hero_activation >= self.HERO_ACTIVATION_INTERVAL_S:
            self.log("🦸 Активирую способность героя")
            self.actions.activate_hero()
            self._last_hero_activation = now

    def _activate_goal_unit_abilities(self, field_state):
        """
        Мана-способность инженера (_GOAL_UNIT) — тап по его клетке на поле.
        Обязательна для стратегии "13 инженеров" (в отличие от способностей
        меча/дриады — их пока не трогаем, см. _find_dryad_boost_pair и
        docstring _find_merge_pair). Тапаем каждый инженер каждый тик — как и
        с героем, лишний тап при неготовой мане предположительно безвреден,
        но для юнитов на поле (в отличие от кнопки героя) это ещё не
        проверено вживую.
        """
        for unit_data in field_state:
            if unit_data["unit"] == self._GOAL_UNIT:
                self.actions.tap_unit(unit_data["region"])

    def _battle_loop(self):
        """
        Основной игровой цикл во время боя: каждые BATTLE_LOOP_INTERVAL_MS
        читает состояние поля и решает, что делать:
        1. Если есть свободные клетки — призывает юнита.
        2. Иначе, если есть пара одинаковых юнитов одного ранга — сливает их.
        3. Иначе — ждёт следующего тика.
        Также обновляет лог и таблицу состояния поля в UI.

        Всё это выполняется, только если бой действительно идёт (UI-хром боя в
        кадре). Иначе кадр разбирает _handle_battle_over: пост-боевые экраны,
        рекламный сундук и переход к следующему бою. Проверять "поле пустое"
        вместо хрома нельзя — детектор поля даёт ложные срабатывания на меню
        (см. _IN_BATTLE_CHROME_TEMPLATE).
        """
        if not self.running:
            return

        try:
            coords_path = "assets/config/button_coords.json"
            with open(coords_path, "r", encoding="utf-8") as f:
                coords_data = json.load(f)
            game_field_matrix = coords_data["game_field_matrix"]

            # Один кадр на тик — по нему и проверка боя, и разбор поля.
            frame = self.detection.get_screenshot()
            if not self._in_battle(frame):
                state = self._handle_battle_over(coords_data, frame)
                # Лог только на смене состояния: тикаем раз в 3 секунды, и
                # писать каждый тик — это шум. Но совсем без записи цикл,
                # застрявший вне боя, выглядит как полная тишина, и первый
                # живой прогон именно так и пришлось отлаживать.
                if state != self._last_loop_state:
                    self.log(f"⏳ Бой не идёт (состояние: {state})")
                    self._last_loop_state = state
                if state == "done":
                    # Навигация к следующему бою уже идёт в фоне и сама
                    # перезапустит _battle_loop, когда бой начнётся.
                    return
                if self.running:
                    self.root_window.after(
                        self.OUT_OF_BATTLE_INTERVAL_MS, self._battle_loop
                    )
                return

            # Сбрасываем, чтобы следующий выход из боя снова попал в лог.
            self._last_loop_state = None

            field_state = self.detection.detect_field_state(
                game_field_matrix, screenshot=frame
            )

            # Собираем статистику для лога/UI
            unit_count = {}
            for unit_data in field_state:
                level = unit_data["merge_level"]
                key = f"{unit_data['unit']} (L{level if level else '?'})"
                unit_count[key] = unit_count.get(key, 0) + 1

            if unit_count:
                units_str = ", ".join([f"{k}: {v}" for k, v in unit_count.items()])
                self.log(f"📊 Поле: {units_str}")
            else:
                self.log("📊 Поле: нет распознанных юнитов")

            if self.log_callback and hasattr(self.log_callback, "update_unit_table"):
                self.log_callback.update_unit_table(field_state)

            self._maybe_activate_hero()
            self._activate_goal_unit_abilities(field_state)
            self._activate_dryad_mana_ability(field_state)
            self._maybe_copy_harlequin(field_state)

            # Принятие решения
            all_cells = {
                (cell["row"], cell["col"])
                for row in game_field_matrix["matrix"]
                for cell in row
            }
            occupied_cells = {(u["row"], u["col"]) for u in field_state}
            free_cells = all_cells - occupied_cells

            # Сливаем и призываем в ОДНОМ тике, именно в этом порядке.
            # Раньше слияние срабатывало только при полностью занятом поле —
            # бот сперва добивал поле призывами и лишь потом начинал мержить,
            # теряя на этом волны. Слияние первым ещё и освобождает клетку,
            # которую тут же занимает призыв ниже.
            pair = self._find_merge_pair(field_state)
            if pair:
                unit_a, unit_b = pair
                level = unit_a["merge_level"]
                self.log(
                    f"🔀 Мержим {unit_a['unit']} (L{level if level else '?'}): "
                    f"({unit_a['row']},{unit_a['col']}) → ({unit_b['row']},{unit_b['col']})"
                )
                self.actions.merge(unit_a["center"], unit_b["center"])
            elif not free_cells:
                self.log("⏸ Поле заполнено, пар для слияния нет")

            if free_cells:
                self.log(f"➕ Свободных клеток: {len(free_cells)} → призываем юнита")
                self.actions.summon()

        except FileNotFoundError:
            self.log("❌ Файл button_coords.json не найден")
        except KeyError:
            self.log("❌ В button_coords.json отсутствует ключ 'game_field_matrix'")
        except json.JSONDecodeError:
            self.log("❌ Ошибка чтения JSON: файл повреждён")
        except Exception as e:
            self.log(f"❌ Ошибка в игровом цикле: {e}")

        if self.running:
            self.root_window.after(self.BATTLE_LOOP_INTERVAL_MS, self._battle_loop)

    # Юнит, чьё количество на поле стратегия "13 инженеров" стремится
    # максимизировать (а не просто поднимать в ранге) — слияние двух
    # экземпляров этого юнита друг с другом снижает их число на поле,
    # поэтому это крайний случай, а не обычный приоритет.
    _GOAL_UNIT = "engineer"
    # Юнит, чья специальная способность поднимает ранг ДРУГОГО юнита того же
    # ранга при перетаскивании на него (а не сливается сама с собой) —
    # см. _find_dryad_boost_pair.
    _BOOSTER_UNIT = "dryad"
    # Юнит, чья способность (при перетаскивании на цель) копирует юнита-цель —
    # см. _find_harlequin_copy_target.
    _HARLEQUIN_UNIT = "harlequin"
    # Предел ранга для слияния двух инженеров друг с другом (крайний случай
    # в _find_merge_pair): разрешаем только слияние ранга 3+3 → 4. Выше —
    # запрещено намеренно (домен-знание пользователя, не техническое
    # ограничение): инженер — не боевой юнит, поднимать его ранг слиянием до
    # 5+ рискованно, дальше ранг поднимаем только boost-слиянием с дриадой
    # (см. _find_dryad_boost_pair), которое не трогает счётчик инженеров.
    _MAX_ENGINEER_MERGE_LEVEL = 3

    def _find_merge_pair(self, field_state):
        """
        Стратегия "13 инженеров": приоритет действий на тик слияния —
        1. Drag дриады на инженера того же ранга — поднимает ранг инженера
           и расходует дриаду одним действием (см. _find_dryad_boost_pair).
        2. Слияние любой другой пары фодера (summoner/harlequin/sword/dryad
           друг с другом) — освобождает клетку под новый саммон, юниты,
           отмеченные приоритетными (чекбокс "⬆️" в UI), в первую очередь.
        3. Слияние самих инженеров — крайний случай: снижает их количество
           на поле, но иначе клетку не освободить, если больше нечего сливать.
           Разрешено только до ранга 3+3 → 4 включительно (см.
           _MAX_ENGINEER_MERGE_LEVEL) — пара инженеров рангом выше в это
           слияние не попадает вообще, их ранг растится только дриадами.

        Про неизвестный ранг (merge_level == 0, см. detect_field_state):
        такие юниты группируются между собой, то есть пара берётся по одному
        лишь совпадению типа. Это осознанная ставка: в игре перетаскивание
        юнита на юнита другого ранга просто не срабатывает, лишний drag
        безвреден, а на поле одного типа обычно и правда лежат юниты одного
        ранга. Исключение — сами инженеры (см. ниже), где цена ошибки выше.
        """
        boost_pair = self._find_dryad_boost_pair(field_state)
        if boost_pair:
            return boost_pair

        priority_units = {
            unit
            for unit, is_priority in zip(self.units, self.priorities)
            if is_priority
        }

        groups = {}
        for unit_data in field_state:
            key = (unit_data["unit"], unit_data["merge_level"])
            groups.setdefault(key, []).append(unit_data)

        fallback_pair = None
        goal_unit_pair = None
        for (unit, level), members in groups.items():
            if len(members) < 2:
                continue
            if unit == self._GOAL_UNIT:
                # level == 0 — ранг не распознан. Слияние инженеров и так
                # крайний случай, а ограничение "только до ранга 3+3 → 4" при
                # неизвестном ранге не проверить: лучше не слить вовсе, чем
                # вслепую слить пару высокого ранга вопреки правилу.
                if level == 0 or level > self._MAX_ENGINEER_MERGE_LEVEL:
                    continue
                if goal_unit_pair is None:
                    goal_unit_pair = (members[0], members[1])
                continue
            if unit in priority_units:
                return members[0], members[1]
            if fallback_pair is None:
                fallback_pair = (members[0], members[1])
        return fallback_pair or goal_unit_pair

    def _find_dryad_boost_pair(self, field_state):
        """
        Ищет дриаду и целевого юнита (_GOAL_UNIT, т.е. инженера) одного
        ранга. Возвращает (дриада, инженер) — именно в этом порядке,
        т.к. actions.merge(from, to) должен тащить дриаду НА инженера,
        а не наоборот (см. docstring GameActions.merge).
        """
        goal_units_by_rank = {}
        for unit_data in field_state:
            if unit_data["unit"] == self._GOAL_UNIT:
                goal_units_by_rank.setdefault(unit_data["merge_level"], []).append(
                    unit_data
                )

        for unit_data in field_state:
            if unit_data["unit"] != self._BOOSTER_UNIT:
                continue
            candidates = goal_units_by_rank.get(unit_data["merge_level"])
            if candidates:
                return unit_data, candidates[0]
        return None

    # Порог мана-способности дриады (перманентный баф на следующее слияние,
    # см. CLAUDE.md "Дриада ... мана-способность ... explicitly skipped for
    # now"): активируем только когда дриад на поле больше этого числа — по
    # словам пользователя (домен-эксперта по игре), чтобы не расходовать баф
    # впустую на дриаду, которая тут же уйдёт в boost-слияние с инженером.
    _DRYAD_MANA_THRESHOLD = 3

    def _activate_dryad_mana_ability(self, field_state):
        """Тап по клетке каждой дриады — но только если дриад на поле больше _DRYAD_MANA_THRESHOLD."""
        dryads = [u for u in field_state if u["unit"] == self._BOOSTER_UNIT]
        if len(dryads) <= self._DRYAD_MANA_THRESHOLD:
            return
        for unit_data in dryads:
            self.actions.tap_unit(unit_data["region"])

    def _find_harlequin_copy_target(self, field_state):
        """
        Выбирает, кого арлекин должен скопировать: приоритет — дриада (даёт
        стратегии ещё один источник boost-слияния для инженеров), при её
        отсутствии — инженер (наращивает число целевого юнита стратегии
        "13 инженеров"). Возвращает (арлекин, цель) для actions.merge(from, to)
        — перетаскиваем клетку арлекина на клетку цели; цель при этом не
        расходуется (в отличие от _find_dryad_boost_pair, где расходуется
        booster-юнит).
        """
        harlequins = [u for u in field_state if u["unit"] == self._HARLEQUIN_UNIT]
        if not harlequins:
            return None

        dryads = [u for u in field_state if u["unit"] == self._BOOSTER_UNIT]
        target = (
            dryads[0]
            if dryads
            else next((u for u in field_state if u["unit"] == self._GOAL_UNIT), None)
        )
        if target is None:
            return None
        return harlequins[0], target

    # Как и с HERO_ACTIVATION_INTERVAL_S — нет прямой видимости готовности
    # способности арлекина, поэтому просто не чаще раза в N секунд. Значение
    # не откалибровано вживую, взято равным интервалу героя как отправная
    # точка — требует подбора на реальном устройстве.
    HARLEQUIN_COPY_INTERVAL_S = 45

    def _maybe_copy_harlequin(self, field_state):
        """Перетаскивает арлекина на дриаду (приоритет) или инженера — не чаще раза в HARLEQUIN_COPY_INTERVAL_S."""
        now = time.monotonic()
        if now - self._last_harlequin_copy < self.HARLEQUIN_COPY_INTERVAL_S:
            return
        pair = self._find_harlequin_copy_target(field_state)
        if pair is None:
            return
        harlequin, target = pair
        self.log(
            f"🎭 Арлекин копирует {target['unit']}: "
            f"({harlequin['row']},{harlequin['col']}) → ({target['row']},{target['col']})"
        )
        self.actions.merge(harlequin["center"], target["center"])
        self._last_harlequin_copy = now

    _CARD_SELECT_RECOVERY_ATTEMPTS = 3

    def _ensure_card_select_ready(self, coords):
        """
        Убеждается, что экран выбора союзников виден и ничем не перекрыт.

        Тап по кнопке карты может открыть поверх него диалог (например,
        "Treasure Maps", когда карт нет) — тогда следующий тап уйдёт в диалог,
        а не в кнопку запуска PVE. Заголовок экрана в таком случае перекрыт
        диалогом и не находится, и мы закрываем диалог кнопкой "Назад".
        """
        phase = coords["screen_phases"]["phases"]["card_select"]
        for _ in range(self._CARD_SELECT_RECOVERY_ATTEMPTS):
            if not self.running:
                return False
            if self.detection.find_template(
                phase["template"], threshold=phase["threshold"]
            ):
                return True
            self.log("🔙 Экран выбора союзников перекрыт → закрываю диалог")
            self.adb.run_adb_command("shell input keyevent 4")
            time.sleep(2)
        return False

    def _start_next_battle(self, coords):
        """
        Запускает навигацию к следующему бою в фоновом потоке.

        Именно в фоновом: вызывается из _battle_loop, который живёт на главном
        потоке Tkinter, а _navigation_cycle блокирует на десятки секунд
        (ожидания шаблонов, скроллы) — на главном потоке это подвесило бы окно
        и сделало кнопку "Стоп" некликабельной, ровно как до вынесения start()
        в поток (см. MainWindow._run_bot_start).
        """
        if self._navigating:
            # Навигация уже идёт: цикл боя мог успеть тикнуть ещё раз на том же
            # экране меню, а второй поток тапал бы одновременно с первым.
            # Возвращаем False, а не None: вызывающий по этому признаку
            # продолжит тикать. Если считать это успешным стартом, цикл боя
            # перестанет перепланировать себя и умрёт молча — так и вышло на
            # первом живом прогоне (девять минут тишины, ноль боёв).
            return False
        self._battles_played += 1
        self.log(f"🔁 Бой №{self._battles_played} завершён — иду за следующим")
        threading.Thread(
            target=self._navigation_cycle, args=(coords,), daemon=True
        ).start()
        return True

    def _dismiss_post_battle(self, coords, frame):
        """
        Закрывает пост-боевые экраны: рекламный сундук и итоги боя. Один тап за
        вызов, возвращает True, если что-то нажали.

        Нужен и циклу боя, и навигации: экран итогов ("VICTORY" с кнопкой
        Continue) кнопкой "Назад" НЕ закрывается, поэтому старт бота поверх
        такого экрана уходил в бесконечное "не найден главный экран → Назад"
        (проверено вживую 2026-07-31 — три минуты подряд). Сюда же попадает
        любой остаток от прерванного прогона.

        Порядок значим: экраны сундука проверяются первыми, потому что шаблон
        кнопки Continue ложно срабатывает на обоих (0.972 и 0.916 при пороге
        0.8).
        """
        if self._maybe_handle_ad_chest(coords, frame):
            return True

        phase = coords["screen_phases"]["phases"]["post_battle_results"]
        matches = self.detection.find_template(
            phase["template"], threshold=phase["threshold"], screenshot=frame
        )
        if matches:
            # Таких экранов после боя два подряд — итоги и статистика урона, —
            # и закрываются они одной и той же кнопкой. Жмём по одному за раз.
            self.log("🏁 Экран итогов боя — жму Continue")
            self.adb.tap(*matches[0])
            return True

        return False

    def _handle_battle_over(self, coords, frame):
        """
        Разбирает, что на экране, когда поле прочиталось пустым.

        Возвращает:
        "battle"  — признаков конца боя нет (поле законно бывает пустым в
                    начале волны), работаем дальше как в бою;
        "waiting" — идёт пост-боевая последовательность, в этот тик не
                    призываем и не мержим, но продолжаем тикать;
        "done"    — вернулись в меню, навигация к следующему бою запущена,
                    цикл боя больше не перепланируется.

        Порядок проверок значим: экраны сундука идут до post_battle_results,
        потому что шаблон его кнопки Continue ложно срабатывает на обоих
        экранах сундука (0.972 и 0.916 при пороге 0.8).
        """
        if self._dismiss_post_battle(coords, frame):
            return "waiting"

        phases = coords["screen_phases"]["phases"]

        def visible(name):
            phase = phases[name]
            return self.detection.find_template(
                phase["template"], threshold=phase["threshold"], screenshot=frame
            )

        # card_select здесь не случайно: _navigation_cycle планирует
        # _battle_loop ДО тапа по кнопке запуска PVE, поэтому первые тики цикла
        # приходятся на экран выбора союзников и загрузку боя. Без этой ветки
        # бот в этот момент считал бы поле пустым и "призывал юнита", тапая по
        # чужому UI.
        if visible("loading") or visible("card_select"):
            return "waiting"

        # После боя игра возвращается не на главный экран, а на список этажей
        # (проверено вживую) — но главный экран тоже считаем концом цикла.
        if visible("floor_select") or visible("home_screen"):
            return "done" if self._start_next_battle(coords) else "waiting"

        return "battle"

    def _maybe_handle_ad_chest(self, coords, frame=None):
        """
        Один шаг обработки пост-боевого рекламного сундука: максимум один тап
        за вызов, без внутренних циклов и sleep. Экран двухэтапный (карусель
        "Ad Chest" с кнопкой Collect → модалка "Support Chest" с Continue /
        Watch Ads), но каждый этап обрабатывается на своём тике игрового цикла:
        _battle_loop крутится на главном потоке Tkinter, и блокирующая
        последовательность тапов с ожиданиями подвесила бы окно.

        Какую кнопку жать в модалке, решает галочка "Смотреть рекламу" в UI
        (self.watch_ads). Возвращает True, если что-то было нажато.

        frame — уже снятый кадр; передаётся вызывающим, когда по тому же кадру
        разбираются и другие пост-боевые экраны (см. _handle_battle_over),
        чтобы не тянуть отдельный ADB-скриншот на каждую проверку.
        """
        rewards = coords["ad_rewards"]
        threshold = rewards["threshold"]
        if frame is None:
            frame = self.detection.get_screenshot()

        def find(key):
            return self.detection.find_template(
                rewards[key], threshold=threshold, screenshot=frame
            )

        # Модалка проверяется первой: она перекрывает карусель, и пока она
        # открыта, тапать по Collect под ней бессмысленно.
        if find("support_chest_title"):
            button = "watch_ads_button" if self.watch_ads else "continue_button"
            matches = find(button)
            if not matches:
                self.log("⚠️ Модалка сундука открыта, но кнопка не найдена")
                return False
            self.log(
                "📺 Сундук: смотрю рекламу"
                if self.watch_ads
                else "⏭️ Сундук: забираю без рекламы"
            )
            self.adb.tap(*matches[0])
            return True

        if find("ad_chest_title"):
            matches = find("collect_button")
            if not matches:
                self.log("⚠️ Экран 'Ad Chest' найден, но кнопки Collect нет")
                return False
            self.log("🎁 Забираю рекламный сундук")
            self.adb.tap(*matches[0])
            return True

        return False

    def _wait_for_template(self, template_path, threshold=0.8, timeout=5):
        """Ждёт появление шаблона до таймаута"""
        for _ in range(timeout):
            if not self.running:
                return False
            if self.detection.find_template(template_path, threshold=threshold):
                return True
            time.sleep(1)
        return False

    # Все жесты ленты глав описаны в assets/config/button_coords.json →
    # chapter_scroll_region (там же — история живой калибровки). Здесь только
    # исполнение: свайп + пауза на затухание инерции.
    def _scroll_chapters(self, gesture):
        """Один свайп ленты глав по описанию жеста из button_coords.json."""
        x1, y1 = gesture["start"]
        x2, y2 = gesture["end"]
        self.adb.swipe(x1, y1, x2, y2, gesture["duration_ms"])
        time.sleep(gesture["settle_s"])

    # Сколько ждать появления экрана Dungeons после тапа по Start PVE.
    # Достаточно, чтобы пережить анимацию перехода, и достаточно мало, чтобы
    # чужой экран отсеялся быстро.
    _FLOOR_SELECT_WAIT_S = 6

    _SCROLL_TO_TOP_REPEATS = (
        3  # с запасом: Chapter 1 — поглощающая граница, лишнее безвредно
    )
    _MAX_HUNT_STEPS = 15
    _TEMPLATE_THRESHOLD = 0.9
    _LAST_CHAPTER = 6
    # Высота карточки этажа под баннером главы. Если баннер найден ниже, чем
    # (низ viewport - это значение), карточка развёрнутой главы физически не
    # поместится в кадр — сначала подкручиваем ленту, а не тапаем по баннеру.
    _FLOOR_CARD_HEIGHT_PX = 470
    _ACCORDION_ANIMATION_S = 2.0

    # Раньше "этаж 14" в UI означал Endless Floor и запускался отдельным
    # калиброванным тапом. Теперь Endless — обычная запись floor_navigation
    # с маркером по слову "Endless", но в сохранённых settings.json у
    # пользователя всё ещё может лежать "14".
    _LEGACY_FLOOR_ALIASES: ClassVar[dict[str, str]] = {"14": "endless"}

    def _normalize_floor_id(self, target_floor):
        """Приводит выбор этажа из UI/настроек к ключу floor_navigation.floors."""
        floor_id = str(target_floor).strip().lower()
        return self._LEGACY_FLOOR_ALIASES.get(floor_id, floor_id)

    def _later_chapter_visible(self, chapter, screenshot):
        """Виден ли в кадре баннер какой-нибудь из глав ПОСЛЕ chapter."""
        return any(
            self.detection.find_template(
                f"assets/templates/icons/chapter_{later}.png",
                threshold=self._TEMPLATE_THRESHOLD,
                screenshot=screenshot,
            )
            for later in range(chapter + 1, self._LAST_CHAPTER + 1)
        )

    def _select_floor(self, target_floor):
        """
        Открывает нужный этаж на экране "Dungeons".

        target_floor — идентификатор этажа как в button_coords.json →
        floor_navigation.floors: номер ("1".."13") или "endless". Все этажи
        ищутся одинаково, спецслучаев нет.

        Поиск closed-loop: скролл всегда до упора наверх (Chapter 1 —
        поглощающая граница, докрутка с запасом безопасна), а дальше —
        маленькими шагами со скриншотом и сверкой по шаблону после каждого
        шага, а не расчётом дистанции.

        Дистанция одного жеста на этой ленте непредсказуема из-за инерции
        (fling): один и тот же свайп прокручивает то на главу, то сразу на
        пять — любой расчёт "на сколько пикселей скроллить" не воспроизводится
        между запусками. Единственное, что воспроизводится — сам факт "чего-то
        видно на экране после шага", поэтому решение о следующем шаге
        принимается по find_template (chapter_N.png, play_button.png,
        floor_{N}_marker.png), а не по счётчику. Сама геометрия жестов — в
        button_coords.json → chapter_scroll_region.
        """
        coords_path = "assets/config/button_coords.json"
        with open(coords_path, "r", encoding="utf-8") as f:
            coords = json.load(f)

        floor_id = self._normalize_floor_id(target_floor)
        floors = coords.get("floor_navigation", {}).get("floors", {})
        floor_meta = floors.get(floor_id)
        marker_path = f"assets/templates/icons/floor_{floor_id}_marker.png"
        if floor_meta is None or not Path(marker_path).exists():
            self.log(
                f"❌ Для этажа {floor_id} нет калиброванного маркера навигации "
                f"({marker_path}). Нужно один раз снять карточку этажа и вырезать "
                f"только цифры номера с полями по бокам (см. floor_navigation "
                f"в button_coords.json)."
            )
            return False
        chapter = floor_meta["chapter"]
        scroll = coords["chapter_scroll_region"]

        # Дешёвая проверка, что мы вообще на экране Dungeons: без неё бот
        # минуту вслепую свайпает по чужому экрану (проверено 2026-07-30 —
        # так он "искал главу" прямо на главном меню), а потом рапортует
        # "глава не найдена". Быстрый выход вернёт цикл к навигации с начала.
        # Ждём, а не судим по одному кадру: экран Dungeons открывается не
        # мгновенно, и сразу после анимации перехода первый кадр ещё пустой —
        # на этом цикл терял целый заход (проверено вживую 2026-07-31).
        floor_select = coords["screen_phases"]["phases"]["floor_select"]
        if not self._wait_for_template(
            floor_select["template"],
            threshold=floor_select["threshold"],
            timeout=self._FLOOR_SELECT_WAIT_S,
        ):
            self.log("❌ Это не экран Dungeons — навигация к этажу отменена")
            return False

        self.log("📜 Прокручиваю к самому началу списка глав...")
        for _ in range(self._SCROLL_TO_TOP_REPEATS):
            if not self.running:
                return False
            self._scroll_chapters(scroll["fling_to_top"])

        self.log(f"🔍 Ищу главу {chapter}...")
        if self._hunt_for_chapter(chapter, scroll) is None:
            self.log(f"❌ Глава {chapter} не найдена за {self._MAX_HUNT_STEPS} шагов")
            return False

        self.log(f"🔍 Ищу карточку этажа {target_floor}...")
        play_pos = self._hunt_for_floor_play_button(
            marker_path, chapter, scroll, coords["floor_navigation"]["play_buttons"]
        )
        if play_pos is None:
            self.log(f"❌ Карточка этажа {target_floor} не найдена")
            return False

        self.log(f"✅ Нажимаю Play на карточке этажа {target_floor} → {play_pos}")
        self.adb.tap(*play_pos)
        time.sleep(3)
        return True

    def _hunt_for_chapter(self, chapter, scroll):
        """
        Ищет баннер главы `chapter`, докручивая маленькими шагами и поправляя
        направление, если баннер более поздней главы уже виден, а искомого нет
        (значит, проскочили). Возвращает координаты найденного баннера или None.
        """
        chapter_template = f"assets/templates/icons/chapter_{chapter}.png"
        for _ in range(self._MAX_HUNT_STEPS):
            if not self.running:
                return None

            # Один скриншот на итерацию: каждый find_template без screenshot=
            # тянет отдельный ADB-скриншот (~0.7с), а их тут до шести на шаг.
            frame = self.detection.get_screenshot()
            matches = self.detection.find_template(
                chapter_template, threshold=self._TEMPLATE_THRESHOLD, screenshot=frame
            )
            if matches:
                return matches[0]

            overshot = self._later_chapter_visible(chapter, frame)
            self._scroll_chapters(
                scroll["step_to_earlier"] if overshot else scroll["step_to_later"]
            )

        return None

    # В главе до трёх карточек, и нужная может оказаться третьей. Внутри главы
    # шаг мелкий (~235px за жест), так что на две карточки уходит около
    # четырёх шагов — плюс запас на разворот главы и коррекцию перелёта.
    _MAX_FLOOR_HUNT_STEPS = 14

    def _find_play_buttons(self, frame, play_templates):
        """Все кнопки Play в кадре: у обычных этажей синяя, у Endless — жёлтая."""
        matches = []
        for template in play_templates:
            matches.extend(
                self.detection.find_template(
                    template, threshold=self._TEMPLATE_THRESHOLD, screenshot=frame
                )
            )
        return matches

    def _hunt_for_floor_play_button(self, marker_path, chapter, scroll, play_templates):
        """
        После того как баннер главы найден (см. _hunt_for_chapter), разворачивает
        его при необходимости и ищет карточку конкретного этажа (по маркеру
        marker_path) вместе с её кнопкой Play — обе должны быть видны в одном
        кадре, чтобы не перепутать с кнопкой Play соседнего этажа.
        Возвращает координаты кнопки Play или None.

        Решение на каждом шаге принимается по тому, виден ли в кадре баннер
        САМОЙ искомой главы. Раньше здесь стояла проверка "виден баннер любой
        более поздней главы ⇒ проскочили" — она ломала всю навигацию: в
        свёрнутом списке баннеры соседних глав почти всегда видны одновременно
        (замерено вживую 2026-07-30: chapter_4 0.98 и chapter_5 0.99 в одном
        кадре), поэтому бот бесконечно "возвращался" с правильной позиции и
        уходил в качели вперёд-назад, так и не открыв этаж.
        """
        chapter_template = f"assets/templates/icons/chapter_{chapter}.png"
        viewport_bottom = scroll["viewport"][3]

        for _ in range(self._MAX_FLOOR_HUNT_STEPS):
            if not self.running:
                return None

            frame = self.detection.get_screenshot()
            marker_matches = self.detection.find_template(
                marker_path, threshold=self._TEMPLATE_THRESHOLD, screenshot=frame
            )
            play_matches = self._find_play_buttons(frame, play_templates)
            if marker_matches and play_matches:
                marker_y = marker_matches[0][1]
                # В главе несколько этажей подряд (у главы 4 это 10, 11, 12 —
                # проверено вживую), поэтому кнопка Play нужной карточки —
                # именно ближайшая ПОД маркером этажа. Если под маркером Play
                # нет, карточка обрезана низом кадра: тапать что попало нельзя,
                # это запустит соседний этаж — доводим ленту и смотрим снова.
                below = [p for p in play_matches if p[1] > marker_y]
                if below:
                    return min(below, key=lambda p: p[1])
                self._scroll_chapters(scroll["fine_to_later"])
                continue

            later_banner_ys = self._later_chapter_banner_ys(chapter, frame)

            # Перелёт — только когда баннер СЛЕДУЮЩЕЙ главы уже уехал в
            # верхнюю половину кадра: значит, вся искомая глава осталась выше.
            # Просто "виден баннер следующей главы" перелётом не считается —
            # он штатно вплывает снизу, пока мы листаем карточки текущей.
            middle = (scroll["viewport"][1] + viewport_bottom) // 2
            if any(y < middle for y in later_banner_ys):
                self.log("↩️ Проскочили главу — возвращаюсь")
                self._scroll_chapters(scroll["fine_to_earlier"])
                continue

            banner_matches = self.detection.find_template(
                chapter_template, threshold=self._TEMPLATE_THRESHOLD, screenshot=frame
            )
            if banner_matches:
                banner_y = banner_matches[0][1]
                fits = banner_y <= viewport_bottom - self._FLOOR_CARD_HEIGHT_PX
                if (
                    not self._chapter_is_expanded(
                        banner_y, play_matches, later_banner_ys
                    )
                    and fits
                ):
                    self.log("📂 Разворачиваю главу")
                    self.adb.tap(*banner_matches[0])
                    time.sleep(self._ACCORDION_ANIMATION_S)
                    continue

            # Глава открыта, но нужной карточки в кадре нет (или баннер уже
            # уехал вверх) — карточка ниже, листаем дальше. Внутри главы
            # только мелкий шаг: крупный (step_*) сдвигает ленту то на 100, то
            # на 670px (измерено вживую) и перепрыгивал третью карточку главы
            # целиком — на этом систематически падал этаж 12.
            self._scroll_chapters(scroll["fine_to_later"])

        return None

    def _later_chapter_banner_ys(self, chapter, frame):
        """Вертикальные позиции баннеров всех глав ПОСЛЕ chapter, видимых в кадре."""
        ys = []
        for later in range(chapter + 1, self._LAST_CHAPTER + 1):
            ys.extend(
                y
                for _, y in self.detection.find_template(
                    f"assets/templates/icons/chapter_{later}.png",
                    threshold=self._TEMPLATE_THRESHOLD,
                    screenshot=frame,
                )
            )
        return ys

    @staticmethod
    def _chapter_is_expanded(banner_y, play_matches, later_banner_ys):
        """
        Развёрнута ли глава: есть ли кнопка Play МЕЖДУ её баннером и баннером
        следующей главы.

        Ограничение "до следующего баннера" критично, а не косметично: без него
        бот принимал за свою карточку Play уже развёрнутой СОСЕДНЕЙ главы
        (аккордеон помнит, какая глава была открыта в прошлый раз) — проверено
        вживую на этаже 12, когда после теста этажа 13 осталась открыта глава 5:
        бот считал главу 4 развёрнутой, не разворачивал её и уезжал искать
        карточку вниз, в чужие главы.
        """
        next_banner_y = min((y for y in later_banner_ys if y > banner_y), default=None)
        return any(
            p[1] > banner_y and (next_banner_y is None or p[1] < next_banner_y)
            for p in play_matches
        )

    def stop(self):
        """Останавливает бота"""
        if self.running:
            self.log("🛑 Бот остановлен")
            self.running = False
