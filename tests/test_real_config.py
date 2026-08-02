"""Проверки поставляемого config.json.

Поведение программы живёт не только в коде: порядок категорий и списки слов в
config.json решают не меньше. Все случаи ниже — настоящие файлы из загрузок,
на которых переразложение однажды сработало неверно. Тесты держат эти границы,
чтобы правка конфига не сломала их молча.
"""
from pathlib import Path

import pytest

from sorter.classifier import classify
from sorter.config import Config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


@pytest.fixture(scope="module")
def cfg() -> Config:
    config = Config.load(CONFIG_PATH)
    config.overrides = {}  # правила проверяем отдельно, тут интересны сами слова
    return config


def category(cfg: Config, name: str) -> str:
    return classify(name, "", cfg)[0]


# --- регрессии переразложения: файл лежал верно, а правила его забирали ---


@pytest.mark.parametrize(
    "name, expected",
    [
        # CAD-формат: слово «launcher» уводило деталь в Игры
        ("Puck_Launcher.step", "3D"),
        # LTX-Video — модель генерации видео, ролики демоted в Others
        ("LTX_2.3_ia2v_00001_.mp4", "Нейросети"),
        # исходники страниц уезжали из Кода в Others
        ("liquid-glass.html", "Код"),
        ("coffee-site.html", "Код"),
        ("workflow.json", "Код"),
        # BeamMP — мультиплеер для BeamNG, слово «installer» уводило в Программы
        ("BeamMP_Installer.exe", "Игры"),
        # безымянный .exe должен остаться программой, а не упасть в Others
        ("RadGen.exe", "Программы"),
    ],
)
def test_resort_does_not_demote_correctly_placed_file(cfg, name, expected):
    assert category(cfg, name) == expected


# --- порядок категорий ---


def test_electronics_wins_over_programs(cfg):
    """Иначе слово `driver` забирает драйвер платы CH340 себе."""
    assert category(cfg, "driver_ch340_341.zip") == "Электроника"


def test_programs_win_over_media(cfg):
    """Иначе слово `music` уводит установщик плеера в музыку."""
    assert category(cfg, "Yandex_Music_x64_5.92.1.exe") == "Программы"


def test_study_wins_over_documents(cfg):
    assert category(cfg, "Памятки_к_экзамену_9_класс.pdf") == "Учёба"


def test_contract_goes_to_documents(cfg):
    assert category(cfg, "Dop._soglashenie_k_dogovoru___U-1570-25.docx") == "Документы"


# --- шаблоны имён ---


def test_frame_range_is_a_render(cfg):
    assert category(cfg, "0001-0250.mp4") == "3D"


def test_bare_date_name_is_a_clip(cfg):
    assert category(cfg, "0606.mp4") == "Медиа"


def test_telegram_photo_is_a_screenshot(cfg):
    assert category(cfg, "photo_2026-06-19_20-03-13.jpg") == "Скриншоты"


# --- давние ловушки ---


def test_font_lora_is_not_an_ai_model(cfg):
    """Шрифт Lora не имеет отношения к LoRA."""
    assert category(cfg, "Lora-Regular.ttf") == "Дизайн"


def test_every_category_has_a_hint(cfg):
    """Подсказки уходят в промт ИИ — без них модель путает соседние категории."""
    named = set(cfg.categories) | {cfg.fallback_category}
    assert named <= set(cfg.category_hints)


def test_managed_folders_cover_all_categories(cfg):
    """Иначе переразложение не зайдёт в папку собственной категории."""
    missing = (set(cfg.categories) | {cfg.fallback_category}) - set(cfg.managed_folders)
    assert not missing


def test_managed_folders_cover_all_types(cfg):
    missing = (set(cfg.type_map) | {cfg.fallback_type}) - set(cfg.managed_folders)
    assert not missing
