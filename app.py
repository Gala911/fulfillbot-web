"""
Backend-сервер (Flask) для веб-версии сравнения фулфилментов.

В отличие от чисто браузерной версии, здесь сервер сам делает сетевые запросы
(парсинг сайтов, отправка форм заявок) — поэтому CORS-ограничения браузера
здесь не действуют, ровно как в Telegram-боте.

Запуск:
    pip install -r requirements.txt
    python app.py
Открой http://localhost:5000 в браузере.
"""

from flask import Flask, jsonify, request, send_from_directory
import os

import database as db
import forms
import scraper
from seed import seed

app = Flask(__name__, static_folder="static", static_url_path="")

db.init_db()
if not db.list_companies():
    seed()


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------------- companies ----------------

@app.route("/api/companies", methods=["GET"])
def api_list_companies():
    companies = db.list_companies()
    result = []
    for c in companies:
        tariffs = db.get_tariffs_for_company(c["id"])
        result.append({
            "id": c["id"],
            "name": c["name"],
            "website": c["website"],
            "city": c["city"],
            "notes": c["notes"],
            "tariffs": {
                t["service_type"]: {"price": t["price"], "unit": t["unit"], "comment": t["comment"]}
                for t in tariffs
            },
        })
    return jsonify(result)


@app.route("/api/companies", methods=["POST"])
def api_add_company():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    website = (data.get("website") or "").strip() or None
    city = (data.get("city") or "").strip() or None
    if not name:
        return jsonify({"error": "Название обязательно"}), 400
    if db.get_company_by_name(name):
        return jsonify({"error": "Компания с таким названием уже есть"}), 409
    company_id = db.add_company(name=name, website=website, city=city)
    return jsonify({"id": company_id, "name": name, "website": website, "city": city}), 201


@app.route("/api/companies/<int:company_id>", methods=["DELETE"])
def api_delete_company(company_id):
    company = db.get_company(company_id)
    if not company:
        return jsonify({"error": "Не найдено"}), 404
    db.delete_company(company["name"])
    return jsonify({"ok": True})


# ---------------- tariffs ----------------

@app.route("/api/tariffs", methods=["POST"])
def api_set_tariff():
    data = request.get_json(force=True)
    company_id = data.get("company_id")
    service_type = data.get("service_type")
    price = data.get("price")
    unit = data.get("unit", "")
    comment = data.get("comment", "")

    if not company_id or service_type not in db.SERVICE_TYPES:
        return jsonify({"error": "Некорректные данные"}), 400
    try:
        price = float(price)
    except (TypeError, ValueError):
        return jsonify({"error": "Цена должна быть числом"}), 400

    db.set_tariff(company_id, service_type, price=price, unit=unit, comment=comment)
    return jsonify({"ok": True})


@app.route("/api/services", methods=["GET"])
def api_services():
    return jsonify(db.SERVICE_TYPES)


@app.route("/api/compare/<service_type>", methods=["GET"])
def api_compare(service_type):
    if service_type not in db.SERVICE_TYPES:
        return jsonify({"error": "Неизвестный вид услуги"}), 400
    rows = db.compare_service(service_type)
    return jsonify([
        {"name": r["name"], "website": r["website"], "city": r["city"], "price": r["price"],
         "unit": r["unit"], "comment": r["comment"], "updated_at": r["updated_at"]}
        for r in rows
    ])


# ---------------- scan (best-effort price discovery) ----------------

@app.route("/api/scan/<int:company_id>", methods=["POST"])
def api_scan(company_id):
    company = db.get_company(company_id)
    if not company:
        return jsonify({"error": "Не найдено"}), 404
    if not company["website"]:
        return jsonify({"error": "У компании не указан сайт"}), 400
    results = scraper.find_candidate_prices(company["website"])
    if "_error" in results:
        return jsonify({"error": results["_error"]}), 502
    readable = {db.SERVICE_TYPES[k]: v for k, v in results.items()}
    return jsonify({"candidates": readable})


# ---------------- send request (best-effort form autofill) ----------------

@app.route("/api/send_request", methods=["POST"])
def api_send_request():
    data = request.get_json(force=True)
    company_id = data.get("company_id")
    lead = {
        "name": data.get("name", ""),
        "phone": data.get("phone", ""),
        "email": data.get("email", ""),
        "message": data.get("message", ""),
    }
    confirm = bool(data.get("confirm", False))

    company = db.get_company(company_id)
    if not company:
        return jsonify({"error": "Компания не найдена"}), 404
    if not lead["name"] or not lead["phone"]:
        return jsonify({"error": "Укажите имя и телефон"}), 400

    if not company["website"]:
        text = forms.build_manual_request_text(lead, company["name"])
        db.log_request(company_id, "manual_no_website", text)
        return jsonify({"mode": "manual", "text": text})

    ok, details = forms.find_and_submit_form(company["website"], lead, dry_run=not confirm)
    if not ok:
        text = forms.build_manual_request_text(lead, company["name"])
        db.log_request(company_id, "manual_form_failed", details)
        return jsonify({"mode": "manual", "text": text, "reason": details})

    if confirm:
        db.log_request(company_id, "sent", details)
        return jsonify({"mode": "sent", "details": details})
    else:
        return jsonify({"mode": "preview", "details": details})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
