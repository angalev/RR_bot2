# Стиль Python-кода

- PEP 8, строка 100 символов (настройка ruff в pyproject.toml — источник истины).
- Аннотации типов на всех публичных функциях. `dict | None`, а не `Optional[dict]`.
- `logging`, никогда `print()` в рабочем коде.
- `pathlib.Path` вместо `os.path`.
- f-strings; в логах — ленивое форматирование: `log.info("x=%s", x)`.
- Конкретные исключения, никогда голый `except:`.
- Явные проверки: `if value is None`.
- Неизменяемость по умолчанию там, где это дёшево: `@dataclass(frozen=True)`, `NamedTuple`, кортежи.
- Форматирование и линт: `ruff check --fix` + `ruff format`. Типы: `mypy`.

Подробности — скилл `python-patterns`.
