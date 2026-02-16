#!/usr/bin/env python3
"""
Generate expanded negative-media queries per client using clients_full.txt
which contains company name variants and owner names.

Outputs per-client CSVs ready for Yandex Search API collection.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import re
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


@dataclasses.dataclass(frozen=True)
class ClientRecord:
    name: str
    variants: List[str]
    owners: List[str]
    jurisdiction: str  # "by" | "ru"


@dataclasses.dataclass(frozen=True)
class QuerySpec:
    client: str
    category: str
    lang: str  # "ru" | "en" | "any"
    query: str
    notes: str = ""


def _today() -> dt.date:
    return dt.date.today()


def normalize_for_slug(s: str) -> str:
    s = s.strip().lower()
    s = s.replace("ё", "е")
    s = re.sub(r"[^0-9a-zа-я]+", "_", s, flags=re.IGNORECASE)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "client"


def _clean_stamp(stamp: str | None) -> str | None:
    if not stamp:
        return None
    cleaned = stamp.strip()
    return cleaned or None


def _queries_filename(*, stamp: str | None, slug: str | None = None) -> str:
    stamp = _clean_stamp(stamp)
    if slug:
        return f"queries_{stamp}__{slug}.csv" if stamp else f"queries__{slug}.csv"
    return f"queries_{stamp}.csv" if stamp else "queries.csv"


def _run_commands_filename(*, stamp: str | None) -> str:
    stamp = _clean_stamp(stamp)
    return f"run_yandex_commands_{stamp}.txt" if stamp else "run_yandex_commands.txt"


def _normalize_key(s: str) -> str:
    s = (s or "").strip().lower().replace("ё", "е")
    s = re.sub(r"\s+", " ", s)
    return s


def dedupe_terms(terms: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for t in terms:
        t = t.strip()
        if not t:
            continue
        key = _normalize_key(t)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def clean_term(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    s = s.replace("«", "").replace("»", "").replace('"', "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_e_yo(s: str) -> str:
    return s.replace("ё", "е").replace("Ё", "Е")


def phrase(s: str) -> str:
    s = clean_term(s)
    return f'"{s}"' if s else ""


def cook_term(t: str) -> str:
    t = (t or "").strip()
    if not t:
        return ""
    if t.startswith('"') and t.endswith('"'):
        return t
    if any(ch in t for ch in [":", ".", '"', "(", ")"]):
        return t
    if " " in t:
        return f'"{t}"'
    return t


def or_block_terms(terms: Sequence[str]) -> str:
    cooked = [t for t in terms if t]
    if not cooked:
        return ""
    if len(cooked) == 1:
        return cooked[0]
    return "(" + " OR ".join(cooked) + ")"


def or_block_phrases(terms: Sequence[str]) -> str:
    cooked = [phrase(t) for t in terms if t.strip()]
    cooked = [t for t in cooked if t]
    return or_block_terms(cooked)


_LEGAL_FORMS_RE = re.compile(
    r"^\s*(?:"
    r"ООО|ОАО|ЗАО|ПАО|АО|СООО|ИП|УП|ЧУП|ГУП|МУП|ТАА|"
    r"Унитарное предприятие|"
    r"Общество с ограниченной ответственностью|"
    r"Открытое акционерное общество|"
    r"Закрытое акционерное общество|"
    r"Публичное акционерное общество|"
    r"Акционерное общество|"
    r"Совместное общество с ограниченной ответственностью"
    r")\s+",
    flags=re.IGNORECASE,
)

_FULL_TO_ABBR = {
    "Общество с ограниченной ответственностью": "ООО",
    "Открытое акционерное общество": "ОАО",
    "Закрытое акционерное общество": "ЗАО",
    "Публичное акционерное общество": "ПАО",
    "Акционерное общество": "АО",
    "Совместное общество с ограниченной ответственностью": "СООО",
    "Унитарное предприятие": "УП",
}


def strip_legal_form(s: str) -> str:
    s = s.strip()
    m = _LEGAL_FORMS_RE.match(s)
    if not m:
        return s
    tail = s[m.end() :].strip()
    return tail or s


def abbreviate_legal_form(s: str) -> str:
    s = s.strip()
    for full, abbr in _FULL_TO_ABBR.items():
        if s.lower().startswith(full.lower()):
            tail = s[len(full) :].strip()
            if tail:
                return f"{abbr} {tail}".strip()
    return ""


_CYR_RE = re.compile(r"[А-Яа-яЁёІіЎў]")


def contains_cyrillic(s: str) -> bool:
    return bool(_CYR_RE.search(s or ""))


_TRANS = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
    "і": "i",
    "ў": "u",
    "ґ": "g",
    "є": "e",
}


def transliterate(s: str) -> str:
    out: List[str] = []
    for ch in s:
        low = ch.lower()
        repl = _TRANS.get(low)
        if repl is None:
            out.append(ch)
            continue
        if ch.isupper():
            repl = repl[:1].upper() + repl[1:]
        out.append(repl)
    return "".join(out)


def expand_company_terms(raw_terms: Sequence[str]) -> List[str]:
    out: List[str] = []
    for raw in raw_terms:
        cleaned = clean_term(raw)
        if not cleaned:
            continue
        out.append(cleaned)
        out.append(normalize_e_yo(cleaned))

        abbr = abbreviate_legal_form(cleaned)
        if abbr:
            out.append(abbr)
            out.append(normalize_e_yo(abbr))

        stripped = strip_legal_form(cleaned)
        if stripped and stripped != cleaned:
            out.append(stripped)
            out.append(normalize_e_yo(stripped))

        if "-" in cleaned:
            out.append(cleaned.replace("-", " "))
            out.append(cleaned.replace("-", ""))

        if " " in cleaned and len(cleaned) <= 40:
            out.append(cleaned.replace(" ", ""))

    return dedupe_terms(out)


_COMPANY_WORDS = {
    "общество",
    "акционерное",
    "унитарное",
    "компания",
    "кампанiя",
    "предприятие",
    "трест",
    "торговая",
    "сеть",
}


def looks_like_person(s: str) -> bool:
    s = clean_term(s)
    if not s:
        return False
    if any(ch.isdigit() for ch in s):
        return False
    if any(ch in s for ch in ['"', "«", "»"]):
        return False
    if _LEGAL_FORMS_RE.match(s):
        return False
    low = s.lower()
    if any(w in low for w in _COMPANY_WORDS):
        return False
    parts = s.split()
    if len(parts) < 2 or len(parts) > 4:
        return False
    if not all(re.fullmatch(r"[A-Za-zА-Яа-яЁёІіЎў'-]+", p or "") for p in parts):
        return False
    if not all(p[0].isupper() for p in parts if p):
        return False
    return True


def _looks_patronymic(s: str) -> bool:
    s = s.lower()
    return s.endswith("ич") or s.endswith("на")


def expand_owner_terms(raw_terms: Sequence[str]) -> List[str]:
    out: List[str] = []
    for raw in raw_terms:
        cleaned = clean_term(raw)
        if not cleaned:
            continue
        out.append(cleaned)
        out.append(normalize_e_yo(cleaned))
        parts = cleaned.split()
        if len(parts) == 2:
            out.append(f"{parts[1]} {parts[0]}")
        elif len(parts) == 3:
            if _looks_patronymic(parts[2]):
                surname, first, patronymic = parts
                out.append(f"{first} {surname}")
                out.append(f"{first} {surname} {patronymic}")
            else:
                out.append(f"{parts[1]} {parts[0]} {parts[2]}")
    return dedupe_terms(out)


def split_terms_by_lang(terms: Sequence[str]) -> Tuple[List[str], List[str]]:
    ru_terms: List[str] = []
    en_terms: List[str] = []
    for t in terms:
        if not t:
            continue
        ru_terms.append(t)
        if contains_cyrillic(t):
            tr = transliterate(t)
            if tr:
                en_terms.append(tr)
        else:
            en_terms.append(t)
    return dedupe_terms(ru_terms), dedupe_terms(en_terms)


def load_clients_full(path: Path, *, jurisdiction_mode: str = "by-all-but-last") -> List[ClientRecord]:
    raw = path.read_text(encoding="utf-8")
    raw_records: List[Tuple[str, List[str], List[str]]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" not in line:
            continue
        if re.match(r"^\d+\.", line):
            continue
        if line.lower().startswith("клиенты"):
            continue
        parts = [p.strip() for p in line.split("|")]
        parts = [p for p in parts if p]
        if not parts:
            continue
        name = parts[0]
        owners = [p for p in parts if looks_like_person(p)]
        variants = [p for p in parts if p not in owners]
        if not variants:
            variants = parts
        raw_records.append((name, variants, owners))

    if not raw_records:
        raise ValueError(f"No clients found in {path}")

    n = len(raw_records)
    records: List[ClientRecord] = []
    for idx, (name, variants, owners) in enumerate(raw_records):
        if jurisdiction_mode == "by-all-but-last":
            jurisdiction = "ru" if idx == n - 1 else "by"
        elif jurisdiction_mode == "all-by":
            jurisdiction = "by"
        elif jurisdiction_mode == "all-ru":
            jurisdiction = "ru"
        else:
            jurisdiction = "by"
        records.append(
            ClientRecord(
                name=name,
                variants=variants,
                owners=owners,
                jurisdiction=jurisdiction,
            )
        )
    return records


RU_CATEGORIES_COMMON: Dict[str, List[str]] = {
    "sanctions": [
        "санкции",
        "санкционный",
        '"санкционный список"',
        '"черный список"',
        "SDN",
        "OFAC",
        '"EU sanctions"',
        '"UK sanctions"',
        '"UN sanctions"',
        '"санкции ЕС"',
        '"санкции США"',
        "ограничения",
    ],
    "litigation": [
        "суд",
        "суды",
        "иск",
        "арбитраж",
        '"судебное решение"',
        '"судебное дело"',
        '"постановление суда"',
        "апелляция",
        "кассация",
        "взыскал",
        "взыскание",
        "истец",
        "ответчик",
    ],
    "criminal_case": [
        '"уголовное дело"',
        "уголовное",
        "обвинение",
        "следствие",
        "преступление",
        "арест",
        "обыск",
        "приговор",
        "судимость",
        "подозреваемый",
        "обвиняемый",
        '"возбуждено дело"',
    ],
    "fraud_corruption": [
        "мошенничество",
        "мошенник",
        "коррупция",
        "взятка",
        '"коммерческий подкуп"',
        '"отмывание денег"',
        '"легализация доходов"',
        "хищение",
        "присвоение",
        "растрата",
        "подлог",
        "сговор",
        "картель",
        "фальсификация",
        "подделка",
    ],
    "bankruptcy": [
        "банкротство",
        "несостоятельность",
        "наблюдение",
        '"внешнее управление"',
        '"финансовое оздоровление"',
        '"конкурсное производство"',
        '"реестр требований кредиторов"',
        "ликвидация",
        "реорганизация",
    ],
    "enforcement_debts": [
        '"исполнительное производство"',
        "задолженность",
        "долг",
        "просрочка",
        '"неисполнение обязательств"',
        '"неплатеж"',
        "взыскание",
        "коллекторы",
    ],
    "regulatory": [
        "прокуратура",
        "проверка",
        "предписание",
        "нарушение",
        "штраф",
        '"административное дело"',
        "надзор",
        "регулятор",
    ],
    "tax": [
        "налоговая",
        '"уклонение от уплаты налогов"',
        "неуплата налогов",
        '"налоговая проверка"',
        '"налоговый спор"',
        "налоговые нарушения",
        "доначисление",
        "пени",
        "штраф",
    ],
    "labor": [
        "невыплата зарплаты",
        "задержка зарплаты",
        "долг по зарплате",
        "забастовка",
        "увольнение",
        '"массовые увольнения"',
        "трудовая инспекция",
        '"охрана труда"',
        "травматизм",
        '"несчастный случай"',
    ],
    "environment": [
        '"экологическое нарушение"',
        "загрязнение",
        "выбросы",
        "разлив",
        '"ущерб окружающей среде"',
        '"экологический штраф"',
        '"экологическая катастрофа"',
    ],
    "reputation_reviews": [
        "отзыв",
        "отзывы",
        "жалоба",
        "претензия",
        "негатив",
        "скандал",
        "обман",
        "развод",
        "кидалово",
        "мошенники",
        '"плохие отзывы"',
        '"черный список"',
        "недобросовестный",
    ],
}

RU_CATEGORIES_RU_EXTRA: Dict[str, List[str]] = {
    "bankruptcy": ["ЕФРСБ", "федресурс"],
    "enforcement_debts": ["ФССП"],
    "regulatory": ["ФАС", "ЦБ", "Росфинмониторинг", "Роспотребнадзор", "Росприроднадзор"],
    "tax": ["ФНС"],
}

RU_CATEGORIES_BY_EXTRA: Dict[str, List[str]] = {
    "litigation": ["экономический суд", "хозяйственный суд"],
    "bankruptcy": ["санация"],
    "enforcement_debts": [
        "принудительное исполнение",
        "судебный исполнитель",
        "отдел принудительного исполнения",
        "исполнительный лист",
    ],
    "regulatory": [
        "МАРТ",
        "МНС",
        "Нацбанк",
        "НБРБ",
        "КГК",
        "Комитет государственного контроля",
        "госконтроль",
        "ДФР",
        "Департамент финансовых расследований",
        "Минприроды",
        "Минтруда",
        "Следственный комитет",
    ],
    "tax": ["МНС", "налоговая инспекция"],
}


def _merge_categories(
    base: Dict[str, List[str]], extra: Dict[str, List[str]]
) -> Dict[str, List[str]]:
    merged = {k: list(v) for k, v in base.items()}
    for key, terms in extra.items():
        merged.setdefault(key, []).extend(list(terms))
    return merged


def ru_categories_for(jurisdiction: str) -> Dict[str, List[str]]:
    extra = RU_CATEGORIES_BY_EXTRA if jurisdiction == "by" else RU_CATEGORIES_RU_EXTRA
    return _merge_categories(RU_CATEGORIES_COMMON, extra)


EN_CATEGORIES: Dict[str, List[str]] = {
    "sanctions": [
        "sanctions",
        "sanctioned",
        "SDN",
        "OFAC",
        '"EU sanctions"',
        '"UK sanctions"',
        '"UN sanctions"',
        "blacklist",
        '"black list"',
    ],
    "litigation": [
        "lawsuit",
        "court",
        "arbitration",
        '"court decision"',
        "judgment",
        "claim",
        "plaintiff",
        "defendant",
        "appeal",
    ],
    "criminal_case": [
        '"criminal case"',
        '"criminal investigation"',
        "indictment",
        "prosecution",
        "arrest",
        "conviction",
        "charges",
        "raids",
    ],
    "fraud_corruption": [
        "fraud",
        "fraudulent",
        "corruption",
        "bribery",
        "kickback",
        '"money laundering"',
        "embezzlement",
        "forgery",
        "collusion",
        "cartel",
        '"price fixing"',
    ],
    "bankruptcy": [
        "bankruptcy",
        "insolvency",
        "restructuring",
        "liquidation",
        "administration",
        "creditors",
        "insolvent",
    ],
    "enforcement_debts": [
        "debt",
        "default",
        '"non-payment"',
        "overdue",
        "collections",
        "delinquent",
    ],
    "regulatory": [
        "regulator",
        "investigation",
        "probe",
        "fine",
        "penalty",
        "violation",
        "compliance",
        '"enforcement action"',
    ],
    "tax": [
        '"tax evasion"',
        '"tax fraud"',
        '"tax arrears"',
        '"tax penalty"',
        "IRS",
        '"tax investigation"',
    ],
    "labor": [
        '"unpaid wages"',
        '"wage arrears"',
        "strike",
        '"labor inspection"',
        "layoffs",
        '"workplace accident"',
        '"occupational safety"',
    ],
    "environment": [
        '"environmental violation"',
        "pollution",
        "spill",
        "emissions",
        '"environmental fine"',
    ],
    "reputation_reviews": [
        "scandal",
        "complaint",
        '"negative reviews"',
        "scam",
        "ripoff",
        "boycott",
        '"fraud allegations"',
    ],
}

OWNER_CATEGORY_KEYS = [
    "sanctions",
    "litigation",
    "criminal_case",
    "fraud_corruption",
    "regulatory",
    "tax",
    "reputation_reviews",
]


def prefixed_categories(
    categories: Dict[str, List[str]], prefix: str, keys: Sequence[str]
) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for key in keys:
        if key in categories:
            out[f"{prefix}{key}"] = categories[key]
    return out


def owner_ru_categories_for(jurisdiction: str) -> Dict[str, List[str]]:
    return prefixed_categories(ru_categories_for(jurisdiction), "owner_", OWNER_CATEGORY_KEYS)


OWNER_EN_CATEGORIES = prefixed_categories(EN_CATEGORIES, "owner_", OWNER_CATEGORY_KEYS)


RU_PRECISION_RU = {
    "courts_arbitr": ["site:kad.arbitr.ru"],
    "courts_general": ["site:sudrf.ru"],
    "enforcement": ["site:fssp.gov.ru"],
    "fedresurs": ["site:fedresurs.ru", "site:bankrot.fedresurs.ru"],
    "prosecutor": ["site:genproc.gov.ru"],
    "fas": ["site:fas.gov.ru"],
    "tax_service": ["site:nalog.gov.ru"],
}

RU_PRECISION_BY = {
    "by_gov": ["site:gov.by"],
    "by_courts": ["site:court.gov.by"],
    "by_laws": ["site:pravo.by"],
    "by_prosecutor": ["site:prokuratura.gov.by"],
    "by_tax": ["site:nalog.gov.by"],
    "by_nbrb": ["site:nbrb.by"],
    "by_mart": ["site:mart.gov.by"],
    "by_kgk": ["site:kgk.gov.by"],
    "by_sk": ["site:sk.gov.by"],
}


def ru_precision_for(jurisdiction: str) -> Dict[str, List[str]]:
    return RU_PRECISION_BY if jurisdiction == "by" else RU_PRECISION_RU


EN_PRECISION = {
    "ofac": ["site:ofac.treasury.gov", "site:home.treasury.gov"],
    "eu_sanctions": ["site:eur-lex.europa.eu", "site:ec.europa.eu"],
    "uk_sanctions": ["site:gov.uk"],
}


def build_specs_for_categories(
    *,
    client: str,
    subject_q: str,
    categories: Dict[str, List[str]],
    lang: str,
    expand: bool,
    notes: str,
) -> List[QuerySpec]:
    subject_q = subject_q.strip()
    if not subject_q:
        return []
    out: List[QuerySpec] = []
    for cat, terms in categories.items():
        cooked = [cook_term(t) for t in terms if t.strip()]
        cooked = dedupe_terms(cooked)
        if not cooked:
            continue
        block = or_block_terms(cooked)
        if block:
            out.append(
                QuerySpec(
                    client=client,
                    category=cat,
                    lang=lang,
                    query=f"{subject_q} {block}".strip(),
                    notes=notes,
                )
            )
        if expand and len(cooked) > 1:
            for term in cooked:
                out.append(
                    QuerySpec(
                        client=client,
                        category=cat,
                        lang=lang,
                        query=f"{subject_q} {term}".strip(),
                        notes=f"{notes} (single-term)",
                    )
                )
    return out


def build_query_specs(
    records: Sequence[ClientRecord],
    *,
    expand: bool = True,
    include_owner: bool = True,
    include_owner_company: bool = True,
) -> List[QuerySpec]:
    specs: List[QuerySpec] = []
    for rec in records:
        company_terms = expand_company_terms([rec.name] + list(rec.variants))
        company_terms_ru, company_terms_en = split_terms_by_lang(company_terms)

        entity_q_ru = or_block_phrases(company_terms_ru)
        entity_q_en = or_block_phrases(company_terms_en)
        ru_categories = ru_categories_for(rec.jurisdiction)
        owner_ru_categories = owner_ru_categories_for(rec.jurisdiction)
        ru_precision = ru_precision_for(rec.jurisdiction)
        ru_notes = "BY negative keywords" if rec.jurisdiction == "by" else "RU negative keywords"
        ru_precision_notes = (
            "BY precision registry query" if rec.jurisdiction == "by" else "RU precision registry query"
        )
        owner_ru_notes = "BY owner negative keywords" if rec.jurisdiction == "by" else "RU owner negative keywords"
        owner_ru_company_notes = (
            "BY owner+company negative keywords"
            if rec.jurisdiction == "by"
            else "RU owner+company negative keywords"
        )

        specs += build_specs_for_categories(
            client=rec.name,
            subject_q=entity_q_ru,
            categories=ru_categories,
            lang="ru",
            expand=expand,
            notes=ru_notes,
        )
        specs += build_specs_for_categories(
            client=rec.name,
            subject_q=entity_q_en,
            categories=EN_CATEGORIES,
            lang="en",
            expand=expand,
            notes="EN negative keywords",
        )

        for cat, ops in ru_precision.items():
            for op in ops:
                specs.append(
                    QuerySpec(
                        client=rec.name,
                        category=cat,
                        lang="any",
                        query=f"{op} {entity_q_ru}".strip(),
                        notes=ru_precision_notes,
                    )
                )

        for cat, ops in EN_PRECISION.items():
            for op in ops:
                specs.append(
                    QuerySpec(
                        client=rec.name,
                        category=cat,
                        lang="any",
                        query=f"{op} {entity_q_en}".strip(),
                        notes="EN precision sanctions query",
                    )
                )

        owner_terms = expand_owner_terms(rec.owners)
        if include_owner and owner_terms:
            owner_terms_ru, owner_terms_en = split_terms_by_lang(owner_terms)
            owner_q_ru = or_block_phrases(owner_terms_ru)
            owner_q_en = or_block_phrases(owner_terms_en)

            specs += build_specs_for_categories(
                client=rec.name,
                subject_q=owner_q_ru,
                categories=owner_ru_categories,
                lang="ru",
                expand=expand,
                notes=owner_ru_notes,
            )
            specs += build_specs_for_categories(
                client=rec.name,
                subject_q=owner_q_en,
                categories=OWNER_EN_CATEGORIES,
                lang="en",
                expand=expand,
                notes="EN owner negative keywords",
            )

            if include_owner_company:
                specs += build_specs_for_categories(
                    client=rec.name,
                    subject_q=f"{owner_q_ru} {entity_q_ru}".strip(),
                    categories=owner_ru_categories,
                    lang="ru",
                    expand=expand,
                    notes=owner_ru_company_notes,
                )
                specs += build_specs_for_categories(
                    client=rec.name,
                    subject_q=f"{owner_q_en} {entity_q_en}".strip(),
                    categories=OWNER_EN_CATEGORIES,
                    lang="en",
                    expand=expand,
                    notes="EN owner+company negative keywords",
                )

    # Deduplicate final specs
    seen = set()
    unique: List[QuerySpec] = []
    for s in specs:
        key = (s.client, s.category, s.lang, s.query)
        if key in seen:
            continue
        seen.add(key)
        unique.append(s)
    return unique


def write_queries_csv(specs: Iterable[QuerySpec], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["client", "client_slug", "category", "lang", "query", "notes"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for s in specs:
            w.writerow(
                {
                    "client": s.client,
                    "client_slug": normalize_for_slug(s.client),
                    "category": s.category,
                    "lang": s.lang,
                    "query": s.query,
                    "notes": s.notes,
                }
            )


def write_per_client_csvs(
    specs: Iterable[QuerySpec], out_dir: Path, stamp: str | None
) -> Dict[str, Path]:
    by_slug: Dict[str, List[QuerySpec]] = {}
    for s in specs:
        slug = normalize_for_slug(s.client)
        by_slug.setdefault(slug, []).append(s)

    out_paths: Dict[str, Path] = {}
    for slug, rows in sorted(by_slug.items(), key=lambda kv: kv[0]):
        out_path = out_dir / _queries_filename(stamp=stamp, slug=slug)
        write_queries_csv(rows, out_path)
        out_paths[slug] = out_path
    return out_paths


def write_run_commands(out_dir: Path, stamp: str | None, query_paths: Dict[str, Path]) -> Path:
    lines: List[str] = []
    lines.append("#!/usr/bin/env bash")
    stamp_label = f" ({stamp})" if _clean_stamp(stamp) else ""
    lines.append(f"# Commands to run Yandex collection per client{stamp_label}.")
    lines.append("#")
    lines.append("# Prereqs:")
    lines.append('#   export YANDEX_API_KEY="..."')
    lines.append("# Optional:")
    lines.append('#   export YANDEX_FOLDER_ID="..."              # if required by your setup')
    lines.append('#   export YANDEX_CLOUD_SEARCH_ENDPOINT="..."  # if different in your YC docs')
    lines.append("#")
    lines.append("# Notes:")
    lines.append("# - Recency filter (last 2 years) is ON by default in collect_findings_yandex.py.")
    lines.append("# - Add flags if needed:")
    lines.append("#     --require-client-match   (keep only results where client name appears in title/snippet)")
    lines.append("#     --keep-undated           (keep results with unknown/unparsed date)")
    lines.append("#     --emit-dropped-age       (audit rows for dropped-by-age results)")
    lines.append("")

    for slug, path in sorted(query_paths.items(), key=lambda kv: kv[0]):
        rel = path.as_posix()
        lines.append(
            f'python3 collect_findings_yandex.py --queries "{rel}" --sleep 0.5 --num 10 --progress --log'
        )

    out_path = out_dir / _run_commands_filename(stamp=stamp)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate expanded negative-media query CSVs per client from clients_full.txt."
    )
    ap.add_argument(
        "--clients-full",
        type=Path,
        default=Path("clients_full.txt"),
        help="Path to clients_full.txt (variants + owners).",
    )
    ap.add_argument(
        "--jurisdiction-mode",
        choices=["by-all-but-last", "all-by", "all-ru"],
        default="by-all-but-last",
        help="Jurisdiction for RU queries: by-all-but-last (default), all-by, all-ru.",
    )
    ap.add_argument("--out", type=Path, default=Path("out"), help="Output directory.")
    ap.add_argument(
        "--stamp",
        default=None,
        help="Stamp used in output filenames (default: no date).",
    )
    ap.add_argument(
        "--use-date",
        action="store_true",
        help="Use today's date as stamp (YYYY-MM-DD).",
    )
    ap.add_argument(
        "--compact",
        action="store_true",
        help="Generate only OR-block queries (no per-term expansion).",
    )
    ap.add_argument(
        "--no-owner",
        action="store_true",
        help="Skip owner-only queries.",
    )
    ap.add_argument(
        "--no-owner-company",
        action="store_true",
        help="Skip owner+company combined queries.",
    )
    ap.add_argument(
        "--no-combined",
        action="store_true",
        help="Do not write a combined queries_<stamp>.csv file.",
    )
    ap.add_argument(
        "--no-run-commands",
        action="store_true",
        help="Do not write run_yandex_commands_<stamp>.txt.",
    )
    args = ap.parse_args()

    stamp = args.stamp
    if args.use_date and not stamp:
        stamp = _today().isoformat()
    stamp = _clean_stamp(stamp)
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    records = load_clients_full(args.clients_full, jurisdiction_mode=args.jurisdiction_mode)
    specs = build_query_specs(
        records,
        expand=not args.compact,
        include_owner=not args.no_owner,
        include_owner_company=not args.no_owner_company,
    )

    if not args.no_combined:
        combined_path = out_dir / _queries_filename(stamp=stamp)
        write_queries_csv(specs, combined_path)
        print(f"Wrote: {combined_path}")

    per_client = write_per_client_csvs(specs, out_dir, stamp)
    for slug, path in per_client.items():
        print(f"Wrote: {path} ({slug})")

    if not args.no_run_commands:
        run_path = write_run_commands(out_dir, stamp, per_client)
        print(f"Wrote: {run_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
