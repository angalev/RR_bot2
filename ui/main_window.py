import json
import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

logger = logging.getLogger(__name__)

SETTINGS_FILE = Path("assets/config/settings.json")
COORDS_FILE = Path("assets/config/button_coords.json")


class MainWindow:
    # Размер превью юнита в полосе настроек. Уменьшен с 60px вместе с общей
    # компоновкой окна — полоса юнитов должна помещаться над кнопкой "Старт".
    UNIT_ICON_PX = 48

    def __init__(self, root, bot_controller):
        self.root = root
        self.bot = bot_controller

        root.title("Rush Royale Bot")
        # Окно намеренно компактное: раньше блоки паковались сверху вниз, и
        # при 900px высоты кнопка "Старт" уезжала за нижний край экрана.
        # Теперь кнопка и настройки прибиты к низу (side=BOTTOM в setup_ui),
        # а всё оставшееся место забирает лог — окно можно сжимать, и кнопка
        # остаётся на виду.
        root.geometry("780x640")
        root.minsize(700, 520)
        root.resizable(True, True)

        # Привязываем закрытие окна
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Инициализируем ДО load_unit_names()
        self.unit_images = {}  # Кэш изображений
        self.unit_image_labels = []  # Метки под изображения

        self.unit_names = self.load_unit_names()

        self.comboboxes = []
        self.unit_vars = []
        self.priority_vars = []

        # Очередь для потокобезопасной доставки обновлений UI из фонового
        # потока бота (см. toggle_bot/_run_bot_start) — виджеты Tkinter можно
        # трогать только из главного потока, поэтому BotController (который
        # исполняется в отдельном потоке, чтобы не подвешивать окно во время
        # навигации по меню) кладёт вызовы сюда, а не дёргает их напрямую.
        self._ui_queue = queue.Queue()

        self.setup_ui()
        self.load_last_settings()
        self.root.after(50, self._drain_ui_queue)
        logger.info("Интерфейс инициализирован")

    def post_to_ui(self, func, *args, **kwargs):
        """Ставит вызов на выполнение в главном потоке Tkinter (см. _drain_ui_queue) — единственный безопасный способ трогать виджеты из фонового потока бота."""
        self._ui_queue.put((func, args, kwargs))

    def _drain_ui_queue(self):
        """Выполняет накопленные из фонового потока вызовы; сама всегда работает в главном потоке, т.к. запланирована через root.after()."""
        try:
            while True:
                func, args, kwargs = self._ui_queue.get_nowait()
                func(*args, **kwargs)
        except queue.Empty:
            pass
        self.root.after(50, self._drain_ui_queue)

    def load_unit_names(self):
        """Загружает список юнитов и кэширует их изображения"""
        units_dir = Path("assets/templates/units")
        config_dir = Path("assets/config")
        json_path = config_dir / "unit_list.json"

        if not units_dir.exists():
            self.show_error("❌ Папка с шаблонами юнитов не найдена!")
            raise FileNotFoundError(f"Папка не найдена: {units_dir}")

        # glob не рекурсивный — визуальные варианты юнитов лежат в подпапке
        # units/variants/ (engineer_shield, engineer_fire, см.
        # GameDetection._preload_unit_templates) и в список UI не попадают:
        # пользователю нужна сама пешка, вариант — деталь распознавания.
        unit_names = sorted({f.stem for f in units_dir.glob("*.png")})
        if not unit_names:
            self.show_error(
                "❌ Нет шаблонов юнитов — проверьте папку assets/templates/units"
            )
            raise FileNotFoundError("Нет шаблонов юнитов")

        # Кэшируем изображения
        for name in unit_names:
            img_path = units_dir / f"{name}.png"
            if img_path.exists():
                try:
                    image = Image.open(img_path)
                    image = image.resize(
                        (self.UNIT_ICON_PX, self.UNIT_ICON_PX),
                        Image.Resampling.LANCZOS,
                    )
                    photo = ImageTk.PhotoImage(image)
                    self.unit_images[name] = photo
                except Exception as e:
                    logger.warning(f"Не удалось загрузить {img_path}: {e}")
                    self.unit_images[name] = None

        config_dir.mkdir(exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"units": list(unit_names)}, f, indent=4, ensure_ascii=False)

        logger.info(f"Юниты загружены: {len(unit_names)}")
        return unit_names

    def load_floor_values(self):
        """
        Список этажей для выпадашки — из button_coords.json →
        floor_navigation.floors, а не захардкоженный диапазон: бот умеет ровно
        те этажи, для которых есть шаблон-маркер и номер главы. Числовые идут
        по возрастанию, именованные ("endless") — в конец.
        """
        try:
            with open(COORDS_FILE, "r", encoding="utf-8") as f:
                floors = json.load(f)["floor_navigation"]["floors"]
        except (OSError, KeyError, json.JSONDecodeError) as e:
            logger.warning("Не удалось прочитать список этажей: %s", e)
            return ["1"]

        numeric = sorted((f for f in floors if f.isdigit()), key=int)
        named = sorted(f for f in floors if not f.isdigit())
        return numeric + named

    def setup_ui(self):
        # Основной фрейм
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Порядок паковки важен: кнопка и настройки прибиваются к низу ПЕРВЫМИ,
        # и только потом лог забирает всё оставшееся место. Иначе (как было
        # раньше, всё паковалось сверху вниз) кнопка "Старт" оказывалась за
        # нижним краем экрана, как только контента становилось чуть больше.
        self.start_btn = ttk.Button(main_frame, text="Старт", command=self.toggle_bot)
        self.start_btn.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))

        settings_frame = ttk.LabelFrame(main_frame, text="Настройки бота")
        settings_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))

        # Лог боя и таблица состояния поля — вкладками, а не двумя блоками
        # друг под другом: вместе они съедали ~330px фиксированной высоты.
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        battle_frame = ttk.Frame(notebook)
        notebook.add(battle_frame, text="Лог боя")

        self.battle_log = tk.Text(
            battle_frame, bg="black", fg="lime", font=("Courier", 10)
        )
        self.battle_log.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.battle_log.insert(tk.END, "Бот в режиме ожидания...\n")
        self.battle_log.config(state=tk.DISABLED)

        # === Таблица состояния поля ===
        state_frame = ttk.Frame(notebook)
        notebook.add(state_frame, text="Состояние поля")

        # Динамические столбцы: только выбранные юниты
        columns = ["rank"] + [f"unit_{i}" for i in range(5)]
        # 9 строк: "Всего" + ранги 1-7 + "Ранг ?"
        self.unit_table = ttk.Treeview(
            state_frame, columns=columns, show="headings", height=9
        )
        self.unit_table.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Заголовки. Ширины заданы явно: по умолчанию Treeview даёт каждой
        # колонке 200px, шесть колонок в окно не влезали и два юнита из пяти
        # просто не были видны.
        self.unit_table.heading("rank", text="Ранг")
        self.unit_table.column("rank", width=90, anchor=tk.W, stretch=False)
        for column in columns[1:]:
            self.unit_table.column(column, width=110, anchor=tk.CENTER)
        self.unit_column_ids = [f"unit_{i}" for i in range(5)]  # Сохраняем ID колонок

        # Устанавливаем начальные имена колонок
        self.table_units = [""] * 5  # Будет обновляться при выборе
        for i in self.unit_column_ids:
            self.unit_table.heading(i, text=f"Юнит {int(i[-1]) + 1}")

        # Строка "Всего" + строки рангов. Отдельная строка нужна для ранга 0
        # ("не распознан"): определение ранга сейчас отключено (см.
        # GameDetection.detect_field_state), и без неё юниты просто не
        # попадали в таблицу — она стояла в нулях, хотя в логе поле читалось.
        self.rank_items = {}
        self.total_item = self.unit_table.insert(
            "", tk.END, values=["Всего"] + ["0"] * 5
        )
        for level in [*range(1, 8), 0]:
            title = f"Ранг {level}" if level else "Ранг ?"
            item_id = self.unit_table.insert("", tk.END, values=[title] + ["0"] * 5)
            self.rank_items[level] = item_id

        # Инициализируем данные
        self.table_units = [""] * 5  # Будет обновляться при выборе

        # Полоса выбора юнитов — одна строка на всю ширину
        units_frame = ttk.Frame(settings_frame)
        units_frame.pack(fill=tk.X, padx=5, pady=(5, 2))

        for i in range(5):
            col_frame = ttk.Frame(units_frame)
            col_frame.grid(row=0, column=i, padx=4, sticky="n")
            units_frame.columnconfigure(i, weight=1)

            # Надпись: DD unit или Юнит 1-5
            label_text = "DD unit" if i == 0 else f"Юнит {i + 1}"
            label = ttk.Label(col_frame, text=label_text, font=("Arial", 8))
            label.pack()

            # Выбор юнита
            var = tk.StringVar()
            combo = ttk.Combobox(
                col_frame,
                textvariable=var,
                values=self.unit_names,
                state="readonly",
                width=14,
            )
            combo.pack(pady=2)
            combo.bind("<<ComboboxSelected>>", self.on_unit_selected)
            self.comboboxes.append(combo)
            self.unit_vars.append(var)

            # Контейнер для изображения
            image_frame = ttk.Frame(
                col_frame, width=self.UNIT_ICON_PX, height=self.UNIT_ICON_PX
            )
            image_frame.pack()
            image_frame.pack_propagate(False)

            image_label = ttk.Label(image_frame)
            image_label.pack(fill=tk.BOTH, expand=True)
            image_label.unit_index = i
            self.unit_image_labels.append(image_label)

            # Приоритет слияния ("Power up")
            priority_var = tk.BooleanVar(value=False)
            priority_cb = ttk.Checkbutton(
                col_frame, text="⬆️ Power up", variable=priority_var
            )
            priority_cb.pack()
            self.priority_vars.append(priority_var)

        # Этаж, тип PVE и флаги — одной строкой, а не тремя блоками подряд
        options_frame = ttk.Frame(settings_frame)
        options_frame.pack(fill=tk.X, padx=5, pady=(2, 5))

        ttk.Label(options_frame, text="Этаж:").pack(side=tk.LEFT)
        self.floor_values = self.load_floor_values()
        self.floor_combo = ttk.Combobox(
            options_frame,
            values=self.floor_values,
            state="readonly",
            width=8,
        )
        self.floor_combo.set(self.floor_values[0])
        self.floor_combo.pack(side=tk.LEFT, padx=(3, 12))

        ttk.Label(options_frame, text="Тип PVE:").pack(side=tk.LEFT)
        self.pve_type = tk.StringVar(value="random")
        self.pve_combo = ttk.Combobox(
            options_frame,
            textvariable=self.pve_type,
            values=["random", "with_friend", "with_clan"],
            state="readonly",
            width=12,
        )
        self.pve_combo.set("random")
        self.pve_combo.pack(side=tk.LEFT, padx=(3, 12))

        self.green_card_var = tk.BooleanVar(value=False)
        self.golden_card_var = tk.BooleanVar(value=False)
        self.watch_ads_var = tk.BooleanVar(value=False)

        ttk.Checkbutton(
            options_frame, text="Green Card", variable=self.green_card_var
        ).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(
            options_frame, text="Golden Card", variable=self.golden_card_var
        ).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(
            options_frame, text="Смотреть рекламу", variable=self.watch_ads_var
        ).pack(side=tk.LEFT, padx=4)

        logger.debug("UI элементы интерфейса успешно созданы")

    def update_unit_table(self, field_state):
        """Вызывается BotController (может быть из фонового потока) — ставит реальное обновление в очередь главного потока."""
        self.post_to_ui(self._update_unit_table_impl, field_state)

    def _update_unit_table_impl(self, field_state):
        """
        Обновляет таблицу: количество юнитов по рангам плюс строка "Всего".

        Ранг 0 — "не распознан" (см. GameDetection.detect_field_state) и имеет
        свою строку. Пока определение ранга отключено, вся раскладка ложится
        именно туда, а реальную картину поля показывает строка "Всего" — без
        неё таблица стояла в нулях при полностью прочитанном поле.
        """
        levels = [*range(1, 8), 0]
        data = {level: {unit: 0 for unit in self.table_units} for level in levels}
        totals = {unit: 0 for unit in self.table_units}

        for unit_data in field_state:
            unit = unit_data["unit"]
            level = unit_data["merge_level"]
            if level in data and unit in data[level]:
                data[level][unit] += 1
                totals[unit] += 1

        self.unit_table.item(
            self.total_item,
            values=["Всего"]
            + [str(totals[unit]) if unit else "0" for unit in self.table_units],
        )
        for level in levels:
            title = f"Ранг {level}" if level else "Ранг ?"
            values = [title] + [
                str(data[level][unit]) if unit else "0" for unit in self.table_units
            ]
            self.unit_table.item(self.rank_items[level], values=values)

    def on_unit_selected(self, event):
        """Обновляет доступные юниты, имена колонок и отображает изображения"""
        self._refresh_unit_selection_ui()

    def _refresh_unit_selection_ui(self):
        """
        Синхронизирует доступные значения комбобоксов, заголовки/table_units
        таблицы "Состояние поля" и изображения с текущим выбором юнитов.
        Вызывается из on_unit_selected (взаимодействие пользователя с
        комбобоксом) и из load_last_settings (программная установка через
        combo.set(), которая НЕ генерирует <<ComboboxSelected>> — без этого
        вызова таблица боя оставалась бы с пустыми заголовками и нулями
        после восстановления настроек до первого ручного клика по комбобоксу).
        """
        # Получаем выбранные юниты
        selected_units = [cb.get() for cb in self.comboboxes]

        # Обновляем доступные юниты в каждом комбобоксе
        for i, combo in enumerate(self.comboboxes):
            current_value = combo.get()
            available = [
                u
                for u in self.unit_names
                if u not in selected_units or u == current_value
            ]
            available.sort()
            combo["values"] = available

            # Если текущее значение выбрано, но занято — снимаем выбор
            if current_value and selected_units.count(current_value) > 1:
                combo.set("")

        # Обновляем имена колонок
        for i, unit in enumerate(selected_units):
            if unit:
                self.unit_table.heading(f"unit_{i}", text=unit.capitalize())
                self.table_units[i] = unit
            else:
                self.unit_table.heading(f"unit_{i}", text=f"Юнит {i + 1}")
                self.table_units[i] = ""

        # Обновляем изображения
        for i, combo in enumerate(self.comboboxes):
            unit_name = combo.get()
            image_label = self.unit_image_labels[i]
            if unit_name and unit_name in self.unit_images:
                image_label.configure(image=self.unit_images[unit_name])
                image_label.image = self.unit_images[unit_name]
            else:
                image_label.configure(image="")
                image_label.image = None

    def toggle_bot(self):
        """Запуск/остановка бота"""
        if self.start_btn["text"] == "Старт":
            selected_units = [cb.get() for cb in self.comboboxes if cb.get()]
            if len(selected_units) != 5:
                self.show_warning("Выберите все 5 юнитов!")
                return
            if len(set(selected_units)) < 5:
                self.show_warning("Юниты не должны повторяться!")
                return

            floor = self.floor_combo.get()
            green_card = self.green_card_var.get()
            golden_card = self.golden_card_var.get()
            pve_type = self.pve_type.get()
            priorities = [var.get() for var in self.priority_vars]
            watch_ads = self.watch_ads_var.get()

            if not self.check_adb_connection():
                return

            self.bot.set_log_callback(self)
            # Кнопка сразу переключается на "Стоп" и остаётся кликабельной —
            # bot.stop() просто выставляет running=False, который читают все
            # блокирующие циклы навигации (_wait_for_template/_hunt_for_*),
            # так что бота можно прервать даже во время поиска меню, не
            # дожидаясь первого боя.
            self.start_btn["text"] = "Стоп"
            threading.Thread(
                target=self._run_bot_start,
                args=(
                    selected_units,
                    floor,
                    green_card,
                    golden_card,
                    pve_type,
                    priorities,
                    watch_ads,
                ),
                daemon=True,
            ).start()
        else:
            self.bot.stop()
            self.start_btn["text"] = "Старт"
            self.update_battle_log("🛑 Бот остановлен.")

    def _run_bot_start(
        self, units, floor, green_card, golden_card, pve_type, priorities, watch_ads
    ):
        """
        Выполняется в фоновом потоке, НЕ в главном потоке Tkinter — здесь
        живёт вся блокирующая логика BotController.start() (навигация по
        меню: ожидание шаблонов, скролл этажей, sleep между тапами), чтобы
        окно не подвисало ("Не отвечает") на время этой навигации. Виджеты
        Tkinter отсюда трогать нельзя — только через self.post_to_ui (сам
        BotController.log()/update_unit_table уже потокобезопасны, см.
        update_battle_log/update_unit_table выше; здесь маршалим только
        результат/ошибку самого start()).
        """
        try:
            self.bot.start(
                units=units,
                floor=floor,
                green_card=green_card,
                golden_card=golden_card,
                pve_type=pve_type,
                priorities=priorities,
                watch_ads=watch_ads,
            )
            # start() мог вернуться и без исключения, но так и не дойдя до
            # боя — например, пользователь нажал "Стоп" во время навигации,
            # или неверный номер этажа/тип PVE (см. правки в bot_controller.py,
            # которые теперь сбрасывают running=False на этих путях выхода).
            # running всё ещё True — только если реально дошли до боя.
            if self.bot.running:
                self.post_to_ui(self._on_bot_started_ok)
            else:
                self.post_to_ui(self._on_bot_start_aborted)
        except Exception as e:
            self.bot.running = False
            self.post_to_ui(self._on_bot_start_failed, str(e))

    def _on_bot_started_ok(self):
        self.update_battle_log("🎮 Бот запущен...")

    def _on_bot_start_aborted(self):
        self.start_btn["text"] = "Старт"
        self.update_battle_log("⏹ Запуск прерван.")

    def _on_bot_start_failed(self, error_text):
        self.start_btn["text"] = "Старт"
        self.show_error(f"❌ Ошибка запуска бота: {error_text}")

    def check_adb_connection(self):
        """Проверяет подключение к BlueStacks"""
        try:
            from utils.bluestacks import BlueStacksController

            BlueStacksController()
            self.update_battle_log("✅ Подключение к BlueStacks установлено")
            return True
        except Exception as e:
            error_msg = f"❌ Не удалось подключиться к BlueStacks: {e}"
            self.update_battle_log(error_msg)
            messagebox.showerror("Ошибка подключения", error_msg)
            return False

    def update_battle_log(self, message):
        """Вызывается BotController (может быть из фонового потока) — ставит реальное обновление в очередь главного потока."""
        self.post_to_ui(self._update_battle_log_impl, message)

    def _update_battle_log_impl(self, message):
        """Выводит сообщение в лог с подсветкой"""
        self.battle_log.config(state=tk.NORMAL)
        self.battle_log.insert(tk.END, message + "\n")
        self.battle_log.see(tk.END)

        if message.startswith("❌"):
            self.battle_log.tag_add("error", "end-2l", "end-1c")
            self.battle_log.tag_config("error", foreground="red")
        if message.startswith("✅"):
            self.battle_log.tag_add("success", "end-2l", "end-1c")
            self.battle_log.tag_config("success", foreground="lime")

        self.battle_log.config(state=tk.DISABLED)

    def show_error(self, message):
        self.update_battle_log(f"❌ {message}")
        messagebox.showerror("Ошибка", message)

    def show_warning(self, message):
        self.update_battle_log(f"⚠️ {message}")
        messagebox.showwarning("Внимание", message)

    def save_settings(self):
        """Сохраняет настройки"""
        try:
            settings = {
                "units": [cb.get() for cb in self.comboboxes],
                "floor": self.floor_combo.get(),
                "green_card": self.green_card_var.get(),
                "golden_card": self.golden_card_var.get(),
                "pve_type": self.pve_type.get(),
                "priorities": [var.get() for var in self.priority_vars],
                "watch_ads": self.watch_ads_var.get(),
            }
            SETTINGS_FILE.parent.mkdir(exist_ok=True)
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=4, ensure_ascii=False)
            logger.info("Настройки сохранены")
        except Exception as e:
            self.show_error(f"Ошибка сохранения: {e}")

    def load_last_settings(self):
        """Загружает настройки"""
        try:
            if not SETTINGS_FILE.exists():
                return
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
            for i, unit in enumerate(settings.get("units", [])):
                if i < 5 and unit in self.unit_names:
                    self.comboboxes[i].set(unit)
            # "14" в старых настройках означало Endless Floor — теперь у него
            # свой идентификатор (см. BotController._LEGACY_FLOOR_ALIASES),
            # и без подмены сохранённый выбор молча сбросился бы на первый этаж.
            floor = settings.get("floor", self.floor_values[0])
            floor = {"14": "endless"}.get(floor, floor)
            if floor in self.floor_values:
                self.floor_combo.set(floor)
            self.green_card_var.set(settings.get("green_card", False))
            self.golden_card_var.set(settings.get("golden_card", False))
            self.watch_ads_var.set(settings.get("watch_ads", False))
            pve_type = settings.get("pve_type", "random")
            if pve_type in ("random", "with_friend", "with_clan"):
                self.pve_type.set(pve_type)
                self.pve_combo.set(pve_type)
            priorities = settings.get("priorities", [False] * 5)
            for i, p in enumerate(priorities):
                if i < 5:
                    self.priority_vars[i].set(p)
            logger.info("Настройки загружены")
            self.root.after(100, self._refresh_unit_selection_ui)
        except Exception as e:
            self.show_error(f"Ошибка загрузки: {e}")

    def on_closing(self):
        self.save_settings()
        self.bot.stop()
        logger.info("Окно закрыто")
        self.root.destroy()

    def get_selected_units(self):
        return [cb.get() for cb in self.comboboxes]

    def get_selected_floor(self):
        return self.floor_combo.get()

    def has_green_card(self):
        return self.green_card_var.get()

    def has_golden_card(self):
        return self.golden_card_var.get()

    def should_watch_ads(self):
        return self.watch_ads_var.get()

    def get_pve_type(self):
        return self.pve_type.get()

    def get_unit_priorities(self):
        return [var.get() for var in self.priority_vars]
