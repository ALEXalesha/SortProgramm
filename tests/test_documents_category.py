"""Категория «Документы» и её граница с «Учёбой».

Договоры и справки раньше уезжали в Учёбу (слово `soglashenie` стояло там) и
затыкались правилами `-> Others`. Тесты держат обе стороны границы.
"""
from sorter.classifier import classify
from sorter.config import Config

# Порядок как в config.json: Учёба раньше Документов, поэтому школьные слова
# выигрывают первыми, а бумаги забирает то, что осталось.
CATEGORIES = {
    "Учёба": ["класс", "экзамен", "демоверсия", "олимпиад", "решени", "задач"],
    "Документы": ["договор", "соглашение", "soglashenie", "справка", "анкета", "полис"],
    "Программы": ["setup", "install", "yandex"],
    "Медиа": ["music", "клип"],
}


def make_config():
    return Config(
        downloads_path="X:/dummy",
        categories=CATEGORIES,
        patterns={
            "3D": [r"^\d{4}-\d{4}(\s*\(\d+\))?\.(mp4|mkv)$"],
            "Медиа": [r"^audio-\d{4}-\d{2}-\d{2}", r"^\d{4}(\s*\(\d+\))?\.(mp4|mkv|mp3)$"],
        },
        type_map={"Documents": ["pdf", "docx", "xlsx"], "Videos": ["mp4"], "Audio": ["mp3"]},
        fallback_category="Others",
        fallback_type="Misc",
    )


def category(name: str) -> str:
    return classify(name, "", make_config())[0]


# --- бумаги ---


def test_contract_addendum_is_a_document_not_homework():
    assert category("Dop._soglashenie_k_dogovoru___U-1570-25.docx") == "Документы"


def test_form_is_a_document():
    assert category("Анкета поступающего.docx") == "Документы"


def test_certificate_is_a_document():
    assert category("Справка об обучении.pdf") == "Документы"


# --- школа не должна утечь в бумаги ---


def test_exam_paper_stays_in_study():
    assert category("Памятки_к_экзамену_9_класс.pdf") == "Учёба"


def test_demo_variant_stays_in_study():
    assert category("Математика демоверсия 9МАТ 2026.pdf") == "Учёба"


def test_admission_decision_stays_in_study():
    """«Решение приемной комиссии» — про поступление, слово «решени» из Учёбы."""
    assert category("Решение приемной комиссии №4 2026.pdf") == "Учёба"


# --- рендер против скачанного ролика ---


def test_frame_range_is_a_render():
    """`0001-0250.mp4` — 886 КБ и 4 секунды, вывод Blender."""
    assert category("0001-0250.mp4") == "3D"


def test_bare_date_name_is_a_clip():
    """`0606.mp4` — 37 МБ и 30 секунд, это скачанный ролик, а не рендер."""
    assert category("0606.mp4") == "Медиа"


def test_clip_with_dedup_suffix():
    assert category("0606(1).mp4") == "Медиа"


def test_voice_message_is_media():
    assert category("AUDIO-2026-05-19-19-06-45.mp3") == "Медиа"


# --- проверки на регресс ---


def test_music_player_installer_stays_a_program():
    """Слово `music` не должно уводить установщик в Медиа."""
    assert category("Yandex_Music_x64_5.92.1.exe") == "Программы"
