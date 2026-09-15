"""
Best-effort парсер цен со страницы фулфилмент-компании.

ВАЖНО (честно о лимитах):
- Многие сайты фулфилментов не публикуют точные цены вообще (только "оставить заявку"),
  или считают тариф через JS-калькулятор, который не отдаёт цифры в HTML.
  В таких случаях парсер ничего не найдёт — это нормально, не баг.
- Работает надёжно только там, где цены/тарифы есть прямо в тексте страницы
  (статичный HTML), рядом с ключевыми словами вида "приёмка", "хранение" и т.д.
- Это ЧЕРНОВОЙ инструмент разведки: он предлагает найденные числа пользователю
  на подтверждение, а не сохраняет их в базу автоматически.
"""

import re

import requests
from bs4 import BeautifulSoup

from database import SERVICE_TYPES

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Ключевые слова для каждого вида услуги, по которым ищем упоминания цены рядом.
KEYWORDS = {
    "receiving": ["приёмк", "приемк", "разгрузк"],
    "storage": ["хранени"],
    "packing": ["упаковк", "сборк", "комплектаци"],
    "shipping": ["доставк", "отгрузк", "логистик"],
    "min_check": ["минимальн"],
}

PRICE_RE = re.compile(r"(\d[\d\s]{0,7}(?:[.,]\d+)?)\s*(?:₽|руб)")


def fetch_text(url: str, timeout: int = 15) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def find_candidate_prices(url: str):
    """
    Возвращает dict: service_type -> список найденных подсказок (кусков текста
    с ценой рядом с ключевым словом). Не гарантирует точность — только наводка.
    """
    try:
        text = fetch_text(url)
    except requests.RequestException as e:
        return {"_error": str(e)}

    results = {}
    for service_type, keywords in KEYWORDS.items():
        hits = []
        for kw in keywords:
            for match in re.finditer(kw, text, flags=re.IGNORECASE):
                start = max(0, match.start() - 60)
                end = min(len(text), match.end() + 60)
                snippet = text[start:end]
                if PRICE_RE.search(snippet):
                    hits.append(snippet.strip())
        if hits:
            # убрать дубли, ограничить количество подсказок
            seen = []
            for h in hits:
                if h not in seen:
                    seen.append(h)
            results[service_type] = seen[:3]
    return results


def format_candidates(results: dict) -> str:
    if "_error" in results:
        return f"Не удалось загрузить страницу: {results['_error']}"
    if not results:
        return (
            "Явных цен рядом с ключевыми словами не найдено. "
            "Скорее всего, тарифы считаются только по заявке или рендерятся через JS."
        )
    lines = ["Найдены возможные упоминания цен (проверьте вручную перед сохранением):"]
    for service_type, snippets in results.items():
        label = SERVICE_TYPES.get(service_type, service_type)
        lines.append(f"\n— {label}:")
        for s in snippets:
            lines.append(f"  · …{s}…")
    return "\n".join(lines)
