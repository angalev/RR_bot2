import json
import logging

logger = logging.getLogger(__name__)


class GameActions:
    """
    Выполняет игровые действия внутри боя: призыв юнита, слияние (drag), способность героя.
    Работает поверх уже подключённого ADB-контроллера (BlueStacksController) и
    координат из button_coords.json. Никакой логики принятия решений здесь нет —
    только исполнение конкретного действия.
    """

    def __init__(self, adb, coords_path="assets/config/button_coords.json"):
        self.adb = adb
        with open(coords_path, "r", encoding="utf-8") as f:
            self.coords = json.load(f)

    def summon(self):
        """Призывает нового юнита (в случайную свободную клетку выбирает сама игра)."""
        logger.info("Призыв юнита")
        self.adb.tap_region(self.coords["summon"]["region"])

    def merge(self, from_center, to_center, duration=350):
        """
        Перетаскивает юнита из одной клетки в другую. Если юниты совпадают
        по типу и рангу — игра сольёт их в юнита рангом выше. Особый случай
        (стратегия "13 инженеров"): drag дриады на юнита того же ранга (не
        обязательно того же типа) поднимает ранг юнита-получателя, а не
        дриады — дриада расходуется. from_center/to_center определяют, кто
        "получатель" (результат оказывается там, куда тащили).
        duration подобран как безопасный минимум, чтобы игра распознала drag,
        а не быстрый swipe/тап — при необходимости скорректировать по результатам
        калибровки на реальном устройстве.
        """
        fx, fy = from_center
        tx, ty = to_center
        logger.info(f"Merge: ({fx}, {fy}) → ({tx}, {ty})")
        self.adb.swipe(fx, fy, tx, ty, duration)

    def activate_hero(self):
        """Активирует способность героя."""
        logger.info("Активация способности героя")
        self.adb.tap_region(self.coords["hero"]["region"])

    def tap_unit(self, region):
        """
        Тап по клетке юнита на поле — активирует его мана-способность,
        если она накоплена (например, обязательная мана-способность
        инженера в стратегии "13 инженеров"). Лишний тап, пока способность
        не готова, предположительно безвреден — как с activate_hero(), но
        это ещё не проверено вживую для юнитов на поле, только для героя.
        Принимает всю область клетки (не центр) — tap_region() сам выбирает
        случайную точку внутри неё.
        """
        logger.info(f"Тап по юниту (мана-способность) → {region}")
        self.adb.tap_region(region)
