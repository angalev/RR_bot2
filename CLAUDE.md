# CLAUDE.md

Отвечай по-русски. Код, комментарии в коде и коммиты — на английском.

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows desktop bot (Tkinter GUI) that plays the mobile game **Rush Royale** running inside **BlueStacks**, driven entirely through **ADB** (`tools/platform-tools/adb.exe`) and OpenCV computer vision (template matching + contour matching). There is no accessibility API or memory reading involved — every action is a simulated tap/swipe based on screenshots pulled from the emulator.

- **Стек**: Python 3.9, `numpy`, `opencv-python`, `pyautogui`, `pytesseract`, `pillow` (see `requirements.txt`; note the file is UTF-16 encoded)
- **Эмулятор**: BlueStacks, fixed **900x1600** resolution — all coordinates in `assets/config/button_coords.json` and all detection logic assume this exact resolution
- **ОС**: Windows. `pathlib.Path` is the project convention (already used in `utils/adb.py`, `core/detection.py`, `main.py`, etc. — see `.claude/rules/python-style.md`); avoid `os.path` and bash-isms in new code
- **Запуск**: `python main.py`
- **Тесты**: `python -m unittest tests.test_detection` (see Tests section — these are live-device integration tests, not `pytest -q`)
- **Линт/формат**: `ruff check --fix . && ruff format .`

## Running the bot

```
python main.py
```

This starts the Tkinter UI (`ui/main_window.py`) wrapping `core/bot_controller.py`. The UI requires selecting exactly 5 distinct units before "Старт" will start the bot.

Requires BlueStacks running with **ADB debugging enabled** and the emulator set to a **fixed 900x1600 resolution** — all coordinates in `assets/config/button_coords.json` and all detection logic assume this exact resolution. If BlueStacks is on a different resolution, every tap coordinate and template match will be wrong.

`utils/adb_installer.py` will auto-download platform-tools if `adb` isn't found on PATH or in `tools/platform-tools/`, and will prompt on stdin to confirm — this only works in an interactive terminal.

## Tests

Tests are `unittest`-based but are **integration tests against a live, connected BlueStacks instance** — none of them mock ADB or the screen. There is no CI-safe/offline test suite.

```
python -m unittest tests.test_detection
python -m unittest tests.test_ad_click
python tests/integration_test_adb.py
python tests/check_template.py
```

`test_detection.py` will hard-fail if the live screenshot isn't exactly 900x1600 — that's a deliberate guard, not a bug.

`tests/calibrate_detection.py` (not a unittest) is a diagnostic tool, not a test: run it against a live BlueStacks instance to print per-cell unit/rank match scores and dump an annotated `debug_field_calibration.png`, for tuning the detection thresholds in `core/detection.py`. Takes unit names as CLI args, or falls back to `assets/config/settings.json`.

`tests/calibrate_floor_selection.py` is **superseded/kept only as a note-to-self** (see its module docstring) — it tried OCR-based floor navigation and live calibration proved that unreliable on this game's stylized font, and separately that swipe-distance-based scrolling doesn't reproduce between runs at all. The actual floor-navigation mechanism in `core/bot_controller.py` (`_hunt_for_chapter` / `_hunt_for_floor_play_button`) doesn't use OCR or fixed-distance macros — see Architecture below.

This project's actual test setup does **not** match the generic mocked-ADB/`pytest` pattern in `.claude/rules/python-testing.md` and the `python-testing` skill — those describe the default convention for other projects; here, `adb`/screen access is real by design, so `@pytest.mark.live`-style separation doesn't apply the same way. Follow the commands above, not the generic skill, for this repo.

## Architecture

The codebase separates into three layers, mirroring the perception/decision/transport split — don't blur them:

1. **Транспорт (`utils/`)** — raw ADB: screenshots, taps, swipes. Knows nothing about the game.
2. **Восприятие (`core/detection.py`)** — pure CV: screenshot → structured field state (unit grid, merge ranks). Knows nothing about strategy, never taps.
3. **Решения (`core/bot_controller.py`, `core/actions.py`, `core/strategy/*`)** — state → action. Calls into the transport/action layer; doesn't manipulate ADB directly from decision code.

**Layering:** `ui/` (Tkinter) → `core/` (bot control flow + CV detection) → `utils/` (ADB transport). `core/bot_controller.py` is the only place that should hold game-flow/state-machine logic; `core/detection.py` is pure perception (no taps); `utils/adb.py` / `utils/bluestacks.py` are pure transport (no game knowledge).

- **`utils/adb.py` (`ADBController`)** — raw `adb` shell wrapper: `tap`, `swipe`, `screenshot` (screencap on-device then `adb pull`), `run_adb_command`. Reads device id from `assets/config/adb_config.json`.
- **`utils/bluestacks.py` (`BlueStacksController(ADBController)`)** — adds `adb connect 127.0.0.1:5555` before delegating to `ADBController.__init__`. Nearly everything in `core/` instantiates this class directly rather than `ADBController`.
- **`core/detection.py` (`GameDetection`)** — all screen understanding:
  - `find_template()` — grayscale `matchTemplate` with a coordinate cache keyed by template path.
  - `detect_field_state()` — scans the 15 cells of `button_coords.json["game_field_matrix"]` and for each cell tries to identify (unit, merge rank). Unit ID (`_detect_unit_by_template` / `score_units_for_cell`) compares an HSV Hue+Saturation histogram (circular mask over the cell centre, `cv2.HISTCMP_BHATTACHARYYA`) between the cell and each selected unit's cached color icon — **not raw pixel correlation**. Live calibration (2026-07-28) found that plain `TM_CCOEFF_NORMED` template matching barely discriminated between units at all (correct unit was top-1 in only ~1/9 live cells checked) because attack-animation glow/cell-frame chrome dominated the correlation more than the character art; the histogram approach raised that to ~11/15 on the same live data. Known residual gap: `engineer` (grey/low-saturation) sometimes scores below threshold; `dryad`/`summoner` (both green) occasionally get confused with each other. Merge rank (1–7) (`_detect_merge_level_by_contour` / `score_ranks_for_cell`) is `cv2.matchShapes` contour comparison against rank templates under `assets/templates/units/unit_rank/`, whose on-disk naming is inconsistent (`rankN.png` / `rank_N.jpeg` / `rankN_bin.png`) — `_load_or_extract_template_contour` tries several candidate filenames per rank rather than assuming one convention. `_extract_contour_from_image` uses Otsu auto-thresholding, not a fixed value — a fixed `threshold=50` was found live to flood several rank templates (and some live cells) to a single solid color, making `cv2.findContours` return the whole-frame rectangle instead of the actual rank-badge shape.
  - **Occupancy comes from unit identification alone**; `merge_level == 0` means "rank unknown", not "empty", and such cells are still returned. This is load-bearing: rank detection currently fails on most cells (see below), and while `detect_field_state` required `unit AND merge_level > 0`, twelve of fifteen occupied cells were being dropped, read as free, and `_battle_loop` summoned into a full field forever (live-reported 2026-07-31). A cell that is genuinely empty and one whose *unit* failed to match are still indistinguishable — both are absent — so `threshold=0.3` in `_detect_unit_by_template` remains a rough empty-cell cutoff rather than a precise discriminator (the histogram argmax over the 5 selected units does the real work). Still worth tuning against genuinely-empty live cells — use `tests/calibrate_detection.py`.
  - **Rank (merge level) detection is broken and currently disabled** — `detect_field_state` always reports `merge_level = 0`. The rank is the **shape of the outline drawn under the unit** (user-confirmed): rank 1 is a circle, rank 4 a diamond, rank 7 an octagon — a polygon gaining vertices with rank, which is exactly what `assets/templates/units/unit_rank/*` holds. So `matchShapes` against those templates is the right method and the templates are good; what's wrong is the **contour extraction**. `_extract_contour_from_image` runs Otsu over the *whole* cell, handing `matchShapes` the unit's silhouette instead of that outline — measured on a live full field its scores were 0.009–23.1 against a `< 0.15` threshold, i.e. noise, and the same unit flipped between "L4" and nothing on consecutive ticks, which split one unit type across `(type, rank)` groups so it could never pair up. Fixing this means isolating the outline ring before matching, not touching the templates or the threshold. Until then consumers must treat rank as optional: `_find_merge_pair` groups unknown-rank units by type alone (a drag onto a mismatched rank is a no-op in game), except engineer↔engineer merges, which are skipped when the rank is unknown because the "never above rank 3" rule can't be verified.
  - `find_template()` had a real x/y coordinate swap bug (`for y, x in zip(*locations[::-1])` — backwards) that went unnoticed because every prior caller only checked the returned list's truthiness, never its coordinates; fixed 2026-07-28. Anything that taps a `find_template()` match's coordinates depends on this being correct.
  - Keeps `last_known_field` as a fallback so a bad frame doesn't blank out the UI table.
- **`core/actions.py` (`GameActions`)** — thin action layer with no decision logic: `summon()` taps the summon button, `merge(from_center, to_center)` drags one cell onto another via `adb.swipe` (merging in Rush Royale is drag-based, not tap-based), `activate_hero()` taps the hero slot. Takes a connected ADB controller in its constructor.
- **`core/bot_controller.py` (`BotController`)** — two phases:
  1. Menu-navigation state machine: waits for the home-screen anchor → taps Start PVE → `_select_floor(floor)` → applies green/golden card → launches the chosen PVE mode (`random`/`with_friend`/`with_clan`). If the anchor isn't found it first tries `_dismiss_post_battle()` (a battle-results screen does **not** respond to Back, so a leftover one used to hang the bot forever) and only then presses Back.
     - The home anchor is the **selected "Battle" bottom tab**, not anything in the PvP/Co-Op button area (`screen_phases.home_screen` → `battle_tab_selected.png`). The bottom bar renders the selected tab wide and captioned and the others as bare icons, so this template answers exactly the question the bot needs — "are we on the battle tab, where the Co-Op button lives". The previous anchor (the "Co-Op" caption) sat in a region the game moves: when an event is running a third button appears left of PvP, the row compresses and Co-Op slides right and clips, dropping the score to 0.815 against a 0.8 threshold. That 0.015 margin was the cause of the "bot sometimes just sits there" reports. Measured 2026-07-31: 1.000 on three different home-screen layouts, 0.31–0.35 on the cards tab, floor list, ally select and victory screen.
     - **All floors go through the same path** — `_hunt_for_chapter()` then `_hunt_for_floor_play_button()`, a **closed-loop** scroll: step → screenshot → `find_template()` → decide, repeat. There is no per-floor special case; `"endless"` (Chapter 6's Endless Floor) is just another entry in `floor_navigation.floors`, and `_normalize_floor_id()` maps the legacy `"14"` from old `settings.json` onto it. Live-verified 2026-07-30: all 14 options (1–13 + endless) navigate end-to-end, 19–41s each, the time scaling with how deep the card sits in its chapter.
     - Closed-loop rather than a fixed-distance macro because the list's scroll distance per gesture is **not reproducible**: measured live, the coarse gesture (350px/800ms) moves the list anywhere from 100 to 670px. The fine gesture (150px/1000ms) *is* reproducible (232–237px across four consecutive runs), so chapter-level search uses the coarse step and card-level search inside a chapter uses only the fine one — the coarse step used to jump clean over a chapter's third card, which is why floor 12 failed systematically. Gesture geometry lives in `button_coords.json → chapter_scroll_region`; all swipes must start inside its `viewport` (a swipe starting in the non-scrollable "Seasonal modifier" header moves nothing at all).
     - Two decisions in the hunt loop are subtler than they look, and both were live-debugging outcomes: **"did we overshoot"** is answered by the *next* chapter's banner being in the upper half of the viewport, not by it merely being visible (adjacent banners are on screen together almost always, which made the bot bounce forever); and **"is the chapter expanded"** is answered by a Play button lying between this chapter's banner and the next one's — a Play button merely *below* the banner can belong to a neighbouring chapter the accordion left open, which made the bot skip the expand step and scroll off into other chapters.
     - Adding a floor once the account unlocks it: crop `assets/templates/icons/floor_{N}_marker.png` as **only the number's digits with background padding on both sides** (no "Floor" word — it is identical on every card and dominates the match, pushing cross-floor scores to 0.93; the side padding is what separates `1` from `10` and `11`), then add `{"chapter": X}` to `button_coords.json → floor_navigation.floors`. See `tests/calibrate_floor_selection.py`'s docstring for the abandoned OCR approach and why.
     - The two phases form a **farm cycle**, not a one-shot: `start()` only does setup and then calls `_navigation_cycle()`, and `_battle_loop` calls `_start_next_battle()` once the game is back in a menu, which re-runs `_navigation_cycle` **on a background thread** (it blocks for tens of seconds and would freeze the window on the Tk thread). `_navigating` guards against two overlapping navigation threads — and note `_start_next_battle` returns a bool for exactly that case: if the guard blocks the spawn, `_handle_battle_over` must return `"waiting"` rather than `"done"`, or the battle loop stops rescheduling itself and dies silently (live-debugged 2026-07-31: nine minutes of no log output and zero battles).
  2. `_battle_loop()` — runs every `BATTLE_LOOP_INTERVAL_MS` via `root.after(...)`. Each tick takes **one** screenshot and first asks `_in_battle()` — a match on `battle_priority_icon.png`, the *only* accepted in-battle signal. A non-empty `detect_field_state()` is explicitly **not** one: the home screen's own team-portrait row falls inside `game_field_matrix`'s cells and reads as real units ("harlequin (L4) ×2"), so a bot started from the menu concluded a battle was already running, skipped all navigation and spent minutes summoning into the main menu (live-reproduced 2026-07-31). A false negative here is harmless (navigate from the top); a false positive is not. When not in battle the frame goes to `_handle_battle_over()`, which returns `"battle"` / `"waiting"` / `"done"` and handles the post-battle chain: ad chest → Continue on both results screens → loading → back in a menu → next battle. Chest phases are checked before `post_battle_results` because that anchor false-positives on both chest screens. When in battle it reads `detect_field_state()`, summons if any board cell isn't accounted for, otherwise merges a pair via `_find_merge_pair` (prefers a pair whose unit is flagged priority in `self.priorities`, aligned by index with `self.units` — the UI's "⬆️" checkboxes — falling back to the first pair found), otherwise idles a tick. Also calls `_maybe_activate_hero()` every tick, which taps the hero-ability button (`GameActions.activate_hero()`, previously never called anywhere) at most once per `HERO_ACTIVATION_INTERVAL_S` (45s, approximate — extra taps when the ability is still on cooldown are harmless no-ops). This is still a generic fill-then-merge policy with no true per-unit awareness (no totem/clone/placement control — `summon()` can't target a specific cell, the game auto-picks) — that's the scope boundary for `core/strategy/*` below, not something this phase implements.
- **`core/strategy/*.py`** (`dryad.py`, `summoner.py`, `engineer.py`, `harlequin.py`, `enchanted_sword.py`) — **all empty placeholder files**, intentionally left empty: the only game actions currently controllable (summon, merge, hero-ability tap) don't support per-unit placement/targeting, so there's no real per-unit decision logic to put here yet. Merge-priority and hero-ability timing (the two things that *are* controllable) live centrally in `bot_controller.py` instead (see above). Revisit these files only if/when cell-targeted summon control becomes possible.
- **`core/ad_viewer.py` (`AdViewer`)** — separate template-matching loop for clicking through rewarded-ad skip buttons (`assets/templates/icons/ad_viewer_marks/*.png`), with a back-off "press Back 3x" fallback if nothing is found after repeated attempts.
- **`ui/main_window.py` (`MainWindow`)** — builds the unit pickers (reading available unit names from `assets/templates/units/*.png`, auto-regenerating `assets/config/unit_list.json`), the battle log, and the per-rank unit-count table. Persists last-used settings to `assets/config/settings.json` on close and reloads them on startup. `MainWindow.root` is stashed onto the bot controller (`self.root_window = self.log_callback.root`) so the controller can schedule its own `after()` polling loop.

## Имитация человеческого ввода

Требование проекта, а не опция — **пока не реализовано** в `utils/adb.py`/`core/actions.py` (текущие `tap`/`swipe` используют точные координаты и без искусственных задержек); когда будет добавляться:
- Координаты тапа — случайная точка внутри целевой области, не центр.
- Задержки между действиями — случайные из заданного диапазона, не константа.
- Слой рандомизации должен остаться в транспорте (`utils/`); `core/` не должен об этом знать.

## Устойчивость

- Игровой цикл не должен падать от одного нераспознанного кадра — `GameDetection.last_known_field` уже частично это покрывает (см. выше); при расширении логики продолжать эту идею: неизвестное состояние → пауза, повторный скриншот, счётчик неудач, а не исключение наружу.
- Все координаты и пороги CV — в конфиге (`assets/config/button_coords.json`, `assets/config/settings.json`), привязанном к разрешению 900x1600, никаких магических чисел в коде.
- Logging goes to both `bot.log` (root) and `assets/config/bot.log` via `logging.basicConfig` in `main.py`; `core/bot_controller.py` additionally mirrors log lines into the UI battle log via `set_log_callback`.

## Config/asset conventions

- All screen regions/taps live in `assets/config/button_coords.json` as `[x1, y1, x2, y2]` rects (helper `_get_tap_center` converts to a tap point); this file is coordinate-only and is the first place to update if the in-game UI shifts.
- `assets/templates/units/*.png` filenames (minus extension) are the canonical unit ID strings used everywhere (UI dropdowns, `unit_list.json`, `selected_units`, `settings.json`). Adding a new playable unit is just dropping a correctly named PNG there.
- `assets/templates/units/variants/{unit}_{state}.png` holds **visual variants of an existing unit** (currently `engineer_shield.png`, `engineer_fire.png`) — the same unit rendered differently in-game (shield overlay, attack glow). `GameDetection._preload_unit_templates` loads them per selected unit and `score_units_for_cell` takes the best score across variants. They live in a subfolder specifically because a variant is **not** distinguishable from a separate unit by filename alone: `witch_doc` is its own unit, not a variant of `witch`, and `enchanted_sword` is not a variant of a nonexistent `enchanted`. Anything directly in `units/` is a real, selectable unit; anything in `variants/` is never shown in the UI.
- `assets/templates/icons/*.png` are menu/state markers (`ad_pve.png`, `chapter_6.png`, etc.) matched via `find_template`.
- `assets/templates/units/unit_rank/` holds merge-rank contour templates (`rankN.png` / `rank_N.jpeg` / `rankN_bin.png`, inconsistent naming — see `detection.py` notes above).
- `button_coords.json → floor_navigation.floors` holds per-floor metadata (`chapter` number; the marker path is implied by name, `assets/templates/icons/floor_{id}_marker.png`) for the closed-loop floor search in `bot_controller.py` — see Architecture above. It is also the **single source of the floor list shown in the UI dropdown** (`MainWindow.load_floor_values`), so the picker can only ever offer floors the bot can actually reach. Live-surveyed layout as of 2026-07-30: three floors per chapter (1–3 → ch1, 4–6 → ch2, 7–9 → ch3, 10–12 → ch4), chapter 5 currently holds only floor 13 on this account, chapter 6 is the Endless Floor (`"endless"`). `floor_navigation.play_buttons` lists both Play-button templates — normal floors' is blue, the Endless card's is yellow.

## Правила

@.claude/rules/python-style.md
@.claude/rules/python-testing.md
@.claude/rules/security.md
@.claude/rules/workflow.md

## Чего не делать

- Не тянуть веб-паттерны: здесь нет FastAPI, Django, SQLAlchemy, Redis.
- Не писать тесты в стиле Allure/BaseTest — это не проект API-автотестов.
- Не хардкодить координаты: только `assets/config/button_coords.json`, привязанный к разрешению 900x1600.
- Не создавать новые `.md` файлы без запроса — документация живёт в README и CLAUDE.md.
