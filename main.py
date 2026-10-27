import tkinter as tk
from ui.main_window import MainWindow
import logging
from pathlib import Path

# Настройка логирования
LOG_DIR = Path("assets/config")
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / "bot.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s:%(funcName)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8")
    ]
)

logger = logging.getLogger(__name__)


def main():
    """Точка входа: создаёт контроллер и запускает GUI"""
    from core.bot_controller import BotController

    # Создаём контроллер бота (вся логика там)
    bot = BotController()

    # Создаём окно
    root = tk.Tk()
    app = MainWindow(root, bot)

    # Запускаем GUI
    root.mainloop()


if __name__ == "__main__":
    main()