"""
Best-effort автозаполнение и отправка простых веб-форм заявок.

ЧЕСТНО О ЛИМИТАХ:
- Работает только для форм с обычным HTML <form>, отправляемых как
  application/x-www-form-urlencoded или multipart без JS-валидации/капчи.
- Большинство современных лендингов фулфилментов используют React/JS-виджеты
  форм (Bitrix24, Tilda, amoCRM-виджеты и т.п.) — их нельзя надёжно отправить
  простым POST-запросом без реального браузера. Для них функция вернёт
  результат "не удалось найти форму" — и тогда лучший вариант это открыть
  ссылку и заполнить форму руками (бот даёт готовый текст для копирования).
- Перед использованием ОБЯЗАТЕЛЬНО проверьте на 1-2 компаниях, что заявка
  реально доходит (например, посмотрите, приходит ли автоответ на почту).
"""

import re

import requests
from bs4 import BeautifulSoup

from database import log_request

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Сопоставление наших данных с типичными именами полей в HTML-формах (RU/EN сайты)
FIELD_MAP = {
    "name": ["name", "имя", "fio", "your-name", "user_name"],
    "phone": ["phone", "tel", "телефон", "your-phone"],
    "email": ["email", "mail", "почта", "your-email"],
    "message": ["message", "comment", "сообщение", "текст", "your-message", "volume", "объем", "объём"],
}


def _match_field(field_name: str):
    field_name_l = field_name.lower()
    for key, aliases in FIELD_MAP.items():
        for alias in aliases:
            if alias in field_name_l:
                return key
    return None


def find_and_submit_form(page_url: str, lead: dict, dry_run: bool = True):
    """
    lead: {"name":..., "phone":..., "email":..., "message":...}
    dry_run=True — только показать, что было бы отправлено, не отправлять реально.

    Возвращает (success: bool, details: str)
    """
    try:
        resp = requests.get(page_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        return False, f"Не удалось открыть страницу: {e}"

    soup = BeautifulSoup(resp.text, "html.parser")
    forms = soup.find_all("form")
    if not forms:
        return False, (
            "На странице не найдено обычной HTML-формы (скорее всего форма "
            "рендерится через JS-виджет). Автоотправка невозможна — "
            "используйте ручную отправку по ссылке."
        )

    # берём первую форму с полем, похожим на телефон или имя — обычно это форма заявки
    target_form = None
    payload = {}
    for form in forms:
        inputs = form.find_all(["input", "textarea"])
        local_payload = {}
        matched_any = False
        for inp in inputs:
            field_name = inp.get("name")
            if not field_name:
                continue
            match = _match_field(field_name)
            if match and match in lead and lead[match]:
                local_payload[field_name] = lead[match]
                matched_any = True
            elif inp.get("value"):
                local_payload[field_name] = inp.get("value")
        if matched_any:
            target_form = form
            payload = local_payload
            break

    if not target_form:
        return False, (
            "Форма на странице найдена, но не удалось сопоставить поля "
            "(имя/телефон/email) — вероятно, нестандартная разметка. "
            "Нужна ручная отправка."
        )

    action = target_form.get("action") or page_url
    if not action.startswith("http"):
        from urllib.parse import urljoin
        action = urljoin(page_url, action)
    method = (target_form.get("method") or "post").lower()

    if dry_run:
        return True, f"[ПРОВЕРКА, без реальной отправки]\nURL: {action}\nМетод: {method}\nПоля: {payload}"

    try:
        if method == "post":
            r = requests.post(action, data=payload, headers=HEADERS, timeout=15)
        else:
            r = requests.get(action, params=payload, headers=HEADERS, timeout=15)
        ok = r.status_code < 400
        return ok, f"HTTP {r.status_code} при отправке на {action}"
    except requests.RequestException as e:
        return False, f"Ошибка отправки: {e}"


def build_manual_request_text(lead: dict, company_name: str) -> str:
    """Готовый текст заявки, если автоотправка невозможна — просто скопировать и вставить."""
    return (
        f"Заявка для: {company_name}\n\n"
        f"Имя: {lead.get('name', '-')}\n"
        f"Телефон: {lead.get('phone', '-')}\n"
        f"Email: {lead.get('email', '-')}\n"
        f"Комментарий/объём: {lead.get('message', '-')}\n"
    )
