#!/usr/bin/env python3
"""Fill the `adress` column by extracting location hints from listing URLs.

This is useful mostly for OLX and OBYAVA listings where the list card does not
provide a separate address field, but the URL slug often contains street,
residential area, or landmark words.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from urllib.parse import unquote, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"

DROP_TOKENS = {
    "ua",
    "uk",
    "obyavlenie",
    "prodazh",
    "prodazha",
    "prodam",
    "prodaetsya",
    "prodatsya",
    "kupite",
    "kvartira",
    "kvartiru",
    "kvartiry",
    "kv",
    "kvar",
    "kom",
    "komn",
    "komnatnaya",
    "komnatnuyu",
    "komnatnu",
    "kimnatna",
    "kimnatnu",
    "kmnatna",
    "kmnatnu",
    "k",
    "h",
    "oh",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
    "16",
    "17",
    "18",
    "19",
    "20",
    "m2",
    "kvm",
    "m",
    "tsna",
    "tsena",
    "vigdna",
    "srochnaya",
    "termnoviy",
    "remont",
    "program",
    "programi",
    "programy",
    "programme",
    "programmy",
    "vaucher",
    "sertifkat",
    "eoselya",
    "vdnovlennya",
    "gos",
    "derzhyu",
    "dogovrna",
    "torg",
    "vlasnika",
    "koms",
    "bez",
    "mebelyu",
    "tehnka",
    "tehnikoyu",
    "gaz",
    "kirpich",
    "tsegla",
    "tsentr",
    "centre",
    "rayon",
    "rn",
    "r",
    "po",
    "na",
    "v",
    "u",
    "z",
    "s",
    "o",
    "so",
    "ot",
    "vid",
    "dlya",
    "blya",
    "vozle",
    "bilya",
    "novyy",
    "novomu",
    "novostroy",
    "zhk",
    "id",
    "da",
    "nyz",
    "niz",
    "nizkiy",
    "nizkiy",
    "sredniy",
    "sredina",
    "etazh",
    "poverh",
    "visotka",
    "sotovyy",
    "proekt",
    "spetsproekt",
    "autonomka",
    "avtonomnoe",
    "avtonomnim",
    "opalennyam",
    "otorlenie",
    "eksklyuziv",
    "zhiliy",
    "stan",
    "beznal",
    "svoyu",
    "zatishnu",
    "otlichnuyu",
    "idealnaya",
    "idealnuyu",
    "vasha",
    "vashe",
    "morya",
    "more",
    "sovremennom",
    "sovremennyy",
    "novom",
    "prostranstvo",
    "predlagaetsya",
    "prodazhe",
    "doma",
    "yaht",
    "mira",
    "raschet",
    "segodnya",
    "sogodni",
    "sogodn",
    "sohodni",
    "dniprovskyy",
    "dneprovskiy",
    "dniprovskiy",
    "provskiy",
    "provskiy",
    "primorskiy",
    "klub",
    "super",
    "podhodit",
    "idem",
    "mozhno",
    "krascha",
    "krasiviy",
    "balcona",
    "balkona",
    "lodzhiya",
    "mebl",
    "vlasna",
    "kotelnya",
    "dom",
    "dome",
    "budinku",
    "dvokmnatna",
    "trikmnatna",
    "odnokmnatna",
    "studiyu",
    "studiya",
    "tipa",
}

WEAK_ADDRESS_VALUES = {
    "дорога",
    "район",
    "парк",
    "ринок",
    "правий берег",
    "лівий берег",
}

LOCATION_MARKERS = {
    "ul",
    "vul",
    "ulitsa",
    "prospekt",
    "prosp",
    "pr",
    "prov",
    "pereulok",
    "doroga",
    "bulvar",
    "bulv",
    "ploscha",
    "ploschad",
    "naberezhnaya",
    "uzviz",
    "spusk",
    "zhm",
    "zh",
}

TOKEN_MAP = {
    "ul": "вул.",
    "vul": "вул.",
    "ulitsa": "вул.",
    "prospekt": "просп.",
    "prosp": "просп.",
    "pr": "просп.",
    "prov": "пров.",
    "pereulok": "пров.",
    "doroga": "дорога",
    "bulvar": "бульв.",
    "bulv": "бульв.",
    "ploscha": "площа",
    "ploschad": "площа",
    "naberezhnaya": "набережна",
    "uzviz": "узвіз",
    "spusk": "узвіз",
    "zhm": "ж/м",
    "zh": "ж/м",
    "rnzhk": "р-н ЖК",
    "raen": "район",
    "rayon": "район",
    "tairova": "Таїрова",
    "tairov": "Таїрова",
    "tarova": "Таїрова",
    "cheremushki": "Черемушки",
    "cheromushki": "Черемушки",
    "arkadii": "Аркадія",
    "arkadiya": "Аркадія",
    "fontan": "Фонтан",
    "kotovskogo": "Котовського",
    "slobodka": "Слобідка",
    "moldavanka": "Молдаванка",
    "pobeda": "Перемога",
    "peremoga": "Перемога",
    "topol": "Тополя",
    "topolya": "Тополя",
    "levoberezhnyy": "Лівобережний",
    "slobozhanskiy": "Слобожанський",
    "slobozhanskiy": "Слобожанський",
    "kalinovaya": "Калинова",
    "rabochaya": "Робоча",
    "karavan": "Караван",
    "parus": "Парус",
    "kommunar": "Комунар",
    "krasnyy": "Червоний",
    "kamen": "Камінь",
    "tsentre": "Центр",
    "tsentr": "Центр",
    "pridnprovsk": "Придніпровськ",
    "pridneprovsk": "Придніпровськ",
    "manhattan": "Manhattan",
    "lagom": "Lagom",
    "akvarel": "Акварель",
    "akropol": "Акрополь",
    "kontinent": "Континент",
    "tiras": "Тирас",
    "avinion": "Avinion",
    "gavayi": "Гаваї",
    "zhemchuzhina": "Жемчужина",
    "odesskiy": "Одеський",
    "dvor": "Двір",
    "solnechnom": "Сонячний",
    "levyy": "Лівий",
    "lviy": "Лівий",
    "bereg": "берег",
    "praviy": "Правий",
    "rinok": "ринок",
    "rynok": "ринок",
    "obraztsova": "Образцова",
    "obraztsoviy": "Образцовий",
    "park": "парк",
    "parka": "парк",
    "marka": "Марка",
    "tvena": "Твена",
    "kosta": "Коста",
    "kalipso": "Каліпсо",
    "baku": "Баку",
    "logos": "Логос",
    "terra": "Терра",
    "sich": "Січ",
}

KNOWN_NAMES = {
    "bocharova": "Бочарова",
    "buvalkina": "Бувалкіна",
    "dobrovolskogo": "Добровольського",
    "mahachkalinskaya": "Махачкалінська",
    "zholio": "Жоліо",
    "kyuri": "Кюрі",
    "lyustdorfskaya": "Люстдорфська",
    "nebesnoy": "Небесної",
    "sotne": "Сотні",
    "saharchova": "Сахарова",
    "saharova": "Сахарова",
    "zabolotnogo": "Заболотного",
    "filatova": "Філатова",
    "kosmonavtov": "Космонавтів",
    "bolgarskaya": "Болгарська",
    "balkovskoy": "Балківська",
    "derevyanko": "Дерев'янка",
    "paliya": "Палія",
    "dacha": "Дача",
    "kovalevskogo": "Ковалевського",
    "vilyamsa": "Вільямса",
    "generala": "Генерала",
    "vishnevskogo": "Вишневського",
    "marselska": "Марсельська",
    "lesi": "Лесі",
    "ukrainki": "Українки",
    "kalinova": "Калинова",
    "kalinovaya": "Калинова",
    "rabochey": "Робоча",
    "rabochaya": "Робоча",
    "ivana": "Івана",
    "mazepy": "Мазепи",
    "korobova": "Коробова",
    "osmiska": "Осьмака",
    "osmaka": "Осьмака",
    "gromova": "Громова",
    "starochumatskaya": "Старочумацька",
    "baykalskaya": "Байкальська",
    "yantarna": "Янтарна",
    "geroev": "Героїв",
    "polya": "Поля",
    "shevchenka": "Шевченка",
    "shmidta": "Шмідта",
    "benderi": "Бендери",
    "bendery": "Бендери",
    "kozhemyaki": "Кожем'яки",
    "bocharov": "Бочарова",
    "bocharovadobrovolskogo": "Бочарова / Добровольського",
    "arkadiizhk": "Аркадія",
    "cheremushkahprogrammy": "Черемушки",
    "cheremushkah": "Черемушки",
    "cheromushkah": "Черемушки",
    "krimskomu": "Кримський",
    "olgerda": "Ольгерда",
    "bochkovskogo": "Бочковського",
    "starokozatska": "Старокозацька",
    "nebesno": "Небесної",
    "sotn": "Сотні",
    "levtana": "Левітана",
    "vgenya": "Євгенія",
    "tantsyuri": "Танцюри",
    "kosmonavtv": "Космонавтів",
    "malinovskogo": "Малиновського",
    "rabina": "Рабина",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fill CSV adress column from listing URL slugs.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=f"Input CSV. Default: {DEFAULT_INPUT}")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output CSV. Default: {DEFAULT_OUTPUT}")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite non-empty adress values too. By default only empty adress cells are filled.",
    )
    parser.add_argument(
        "--replace-sources",
        default="",
        help="Comma-separated sources whose adress values should be recalculated even if already filled.",
    )
    return parser.parse_args()


def slug_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    slug = path.rstrip("/").split("/")[-1]
    slug = re.sub(r"\.html?$", "", slug, flags=re.IGNORECASE)
    slug = re.sub(r"-?ID[a-zA-Z0-9]+$", "", slug)
    slug = re.sub(r"-?db\d+$", "", slug)
    slug = re.sub(r"-?\d{6,}$", "", slug)
    return slug.lower()


def normalized_tokens(url: str) -> list[str]:
    slug = slug_from_url(url)
    raw_tokens = re.split(r"[-_/]+", slug)
    return [token for token in raw_tokens if token and token not in DROP_TOKENS and not token.isdigit()]


def looks_like_location_token(token: str) -> bool:
    return token in LOCATION_MARKERS or token in TOKEN_MAP or token in KNOWN_NAMES


def choose_location_tokens(tokens: list[str]) -> list[str]:
    if not tokens:
        return []

    marker_indexes = [idx for idx, token in enumerate(tokens) if token in LOCATION_MARKERS]
    if marker_indexes:
        start = marker_indexes[0]
        chosen = []
        for token in tokens[start : start + 7]:
            if token in DROP_TOKENS:
                continue
            chosen.append(token)
            if len(chosen) >= 4:
                break
        return chosen

    known_indexes = [idx for idx, token in enumerate(tokens) if looks_like_location_token(token)]
    if known_indexes:
        chosen = []
        for token in tokens:
            if looks_like_location_token(token):
                chosen.append(token)
            if len(chosen) >= 4:
                break
        return chosen

    return []


def humanize_tokens(tokens: list[str]) -> str:
    words: list[str] = []
    for token in tokens:
        if token in DROP_TOKENS:
            continue
        word = TOKEN_MAP.get(token) or KNOWN_NAMES.get(token)
        if not word:
            word = token.replace("_", " ").capitalize()
        words.append(word)

    # Remove weak leftovers from the edges after mapping.
    while words and words[0].lower() in {"ul", "vul", "na", "po"}:
        words.pop(0)
    address = " ".join(words).strip(" ,.-")
    if address.lower() in WEAK_ADDRESS_VALUES:
        return ""
    if any(word in address.lower() for word in ["ідеальная", "идеальная", "сьогодні", "сегодня"]):
        return ""
    if address.lower().startswith("жк ") and len(address.split()) > 4:
        address = " ".join(address.split()[:4])
    while address.split() and address.split()[-1].lower() in {"на", "po", "pod", "iz", "n"}:
        address = " ".join(address.split()[:-1])
    if not any(marker in address.lower() for marker in ["вул.", "просп.", "пров.", "дорога", "бульв.", "площа", "ж/м"]):
        mapped_count = sum(1 for token in tokens if token in TOKEN_MAP or token in KNOWN_NAMES)
        if mapped_count == 0:
            return ""
    return address


def address_from_url(url: str) -> str:
    tokens = normalized_tokens(url)
    chosen = choose_location_tokens(tokens)
    address = humanize_tokens(chosen)
    return address if len(address) >= 3 else ""


def should_update(row: dict[str, str], overwrite: bool, replace_sources: set[str]) -> bool:
    if overwrite:
        return True
    if row.get("source", "") in replace_sources:
        return True
    return not row.get("adress", "").strip()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    replace_sources = {source.strip() for source in args.replace_sources.split(",") if source.strip()}

    with input_path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    updated = 0
    cleared = 0
    for row in rows:
        if not should_update(row, args.overwrite, replace_sources):
            continue
        address = address_from_url(row.get("url", ""))
        if address:
            if row.get("adress", "") != address:
                updated += 1
            row["adress"] = address
        elif args.overwrite or row.get("source", "") in replace_sources:
            if row.get("adress", ""):
                cleared += 1
            row["adress"] = ""

    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Updated adress for {updated} rows, cleared {cleared} noisy values in {output_path}")


if __name__ == "__main__":
    main()
