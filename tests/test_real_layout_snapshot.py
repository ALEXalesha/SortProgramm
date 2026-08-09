"""Снимок раскладки по настоящим именам: правка правил не должна её ворошить.

Зачем это нужно, видно по журналам отмены из `<загрузки>/.sorter`. Файл
`Untitled.png` программа двигала так: 3 августа в «Дизайн», 4 августа в
«Скриншоты», 8 августа снова в «Дизайн», а следующие правила отправляли его уже
в «Others». То же самое с `7D14v1M1.zip`, `gcapi.dll`, `Тайный Санта 2025.xlsx`
и ещё десятком имён. Каждая сессия, поправившая `rules.json`, перекладывала
около девяноста уже разложенных файлов — и пользователь видел это как «после
каждой пересборки приложение опять всё перемешало».

Код при этом исправен: раскладка неподвижна, второй проход по тем же файлам
не двигает ничего. Ворочала папки нестабильность самих правил, а заметить её
было нечем: план на девяносто строк снаружи неотличим от честной уборки.

Памятка проекта требовала снимок категорий до и после правки правил вручную —
и его не делали ни разу. Здесь он лежит файлом и проверяется сам.

**Что делать, если тест упал.** Он не запрещает менять правила, он требует
объяснить изменение. Посмотри на список имён в отчёте: если категории у них
стали лучше — обнови снимок (`python tests/test_real_layout_snapshot.py`) и
скажи в коммите, какие имена переехали и почему. Если в списке есть имена, к
которым правка отношения не имела, — правка задела не то.

Снимок снят по одному имени, без содержимого файла: так он воспроизводится на
любой машине, где есть только репозиторий. Имена, которые правила решить не
могут (`Untitled.png`, `gcapi.dll`), держатся не словами, а ручными правилами в
`overrides.json` — для того файл и заведён.
"""
import json
from pathlib import Path

import pytest

from sorter.classifier import classify
from sorter.config import Config

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
SNAPSHOT_PATH = Path(__file__).resolve().parent / "fixtures" / "real_layout.json"


def place(config: Config, name: str) -> list[str]:
    """Куда правила кладут файл с таким именем: [категория, тип]."""
    category, file_type, _ = classify(name, "", config)
    return [category, file_type]


def rules_only_config() -> Config:
    """Конфиг без ручных правил: снимок должен чувствовать правку СЛОВ.

    Первая версия этого теста снимала раскладку как есть, с `overrides.json`, —
    и не падала даже на нарочно испорченных правилах. Причина простая: ручное
    правило бьёт раньше слов, а на большинстве имён из снимка оно есть, так
    что менять `rules.json` можно было как угодно. Получился тест, который не
    умеет падать: зелёный, с правильным названием и без единого шанса поймать
    то, ради чего написан. В этом проекте такой уже случался — на защите
    ответа ИИ, — и дожил до пятнадцатой сессии.

    Убирая правила, мы и получаем ту самую сверку «что сказали бы одни слова»,
    которую памятка проекта требует прогонять после каждой правки категорий.
    Файлы, чьё место держит `overrides.json`, от правки правил не двигаются
    вовсе — за них тут бояться нечего.
    """
    config = Config.load(CONFIG_PATH)
    config.overrides = {}
    return config


@pytest.fixture(scope="module")
def config() -> Config:
    return rules_only_config()


@pytest.fixture(scope="module")
def snapshot() -> dict[str, list[str]]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def test_snapshot_covers_the_real_downloads(snapshot):
    """Пустой снимок проходит любую сверку — значит размер надо стеречь.

    В снимке только файлы из своих папок программы: их пара сотен, тогда как
    всего в загрузках тысячи — остальные лежат в чужих папках, куда программа
    не заходит. Порог грубый и стоит здесь ради одного: генератор, сломавшийся
    молча (не та папка, пустой скан), не должен превратить проверку в
    формальность, которая всегда зелёная.
    """
    assert len(snapshot) > 100, "снимок подозрительно мал, перегенерируй"


def test_rules_do_not_reshuffle_the_real_layout(config, snapshot):
    drift = []
    for name, expected in snapshot.items():
        got = place(config, name)
        if got != expected:
            drift.append(f"  {name}\n     было: {'/'.join(expected)}"
                         f"\n     стало: {'/'.join(got)}")
    assert not drift, (
        f"правка правил перекладывает {len(drift)} уже разложенных файлов:\n"
        + "\n".join(drift[:25])
        + (f"\n  …и ещё {len(drift) - 25}" if len(drift) > 25 else "")
        + "\n\nЕсли так и задумано — обнови снимок:\n"
          "  python tests/test_real_layout_snapshot.py")


def _regenerate() -> None:
    """Пересобирает снимок по настоящей папке загрузок.

    Берём ровно те файлы, с которыми программа работает: корень загрузок и
    папки, которые она создала сама (`scan(deep=True)`). Чужие папки в снимок
    не попадают — программа их не трогает, и пинать их правилами незачем.
    """
    from sorter.scanner import scan

    full = Config.load(CONFIG_PATH)
    if full.problems:
        raise SystemExit("сначала почини настройки:\n  "
                         + "\n  ".join(full.problems))
    names = sorted({f.name for f in scan(full.downloads_path, full, deep=True)})
    config = rules_only_config()
    if not names:
        raise SystemExit(f"в {config.downloads_path} не найдено ни одного файла")
    data = {name: place(config, name) for name in names}
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    print(f"снимок обновлён: {len(data)} имён -> {SNAPSHOT_PATH}")


if __name__ == "__main__":
    _regenerate()
