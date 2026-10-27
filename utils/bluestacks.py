import logging

from .adb import ADBController

# Создаём логгер для этого модуля
logger = logging.getLogger(__name__)


class BlueStacksController(ADBController):
    def __init__(self, instance_name="BlueStacks", bluestacks_port="127.0.0.1:5555"):
        self.instance_name = instance_name
        self.bluestacks_port = bluestacks_port
        # ADBController.__init__ -> ADBInstaller.ensure_adb() уже добавляет
        # tools/platform-tools в PATH при необходимости и подключается к
        # bluestacks_port — отдельный adb connect здесь раньше запускался до
        # настройки PATH и падал, если adb не было в системном PATH.
        super().__init__()
        logger.info(
            f"BlueStacksController инициализирован для {instance_name} на порту {bluestacks_port}"
        )
