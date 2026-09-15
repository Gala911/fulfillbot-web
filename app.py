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
import uuid
from werkzeug.utils import secure_filename

import database as db
import forms
import scraper
from seed import seed

app = Flask(__name__, static_folder="static", static_url_path="")

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {"pdf", "xlsx", "xls", "csv", "doc", "docx", "png", "jpg", "jpeg", "webp"}
MAX_FILE_SIZE = 15 * 1024 * 1024  # 15 МБ
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

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
            "price_file_url": f"/api/files/{c['price_file']}" if c["price_file"] else None,
            "price_file_name": c["price_file_original_name"],
            "tariffs": {
                t["service_type"]: {"price": t["price"], "unit": t["unit"], "comment": t["comment"]}
                for t in tariffs
            },
        })
    return jsonify(result)


def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _save_uploaded_file(file_storage):
    """Сохраняет загруженный файл под уникальным именем, возвращает (stored_name, original_name)."""
    original_name = secure_filename(file_storage.filename)
    if not original_name or not _allowed_file(original_name):
        return None, None
    ext = original_name.rsplit(".", 1)[1].lower()
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(UPLOAD_FOLDER, stored_name))
    return stored_name, original_name


@app.route("/api/companies", methods=["POST"])
def api_add_company():
    # Поддерживаем и обычный JSON (без файла), и multipart/form-data (с файлом)
    if request.content_type and "multipart/form-data" in request.content_type:
        name = (request.form.get("name") or "").strip()
        website = (request.form.get("website") or "").strip() or None
        city = (request.form.get("city") or "").strip() or None
        uploaded = request.files.get("price_file")
    else:
        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        website = (data.get("website") or "").strip() or None
        city = (data.get("city") or "").strip() or None
        uploaded = None

    if not name:
        return jsonify({"error": "Название обязательно"}), 400
    if db.get_company_by_name(name):
        return jsonify({"error": "Компания с таким названием уже есть"}), 409

    stored_name, original_name = (None, None)
    if uploaded and uploaded.filename:
        stored_name, original_name = _save_uploaded_file(uploaded)
        if stored_name is None:
            allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
            return jsonify({"error": f"Недопустимый формат файла. Разрешены: {allowed}"}), 400

    company_id = db.add_company(
        name=name, website=website, city=city,
        price_file=stored_name, price_file_original_name=original_name,
    )
    return jsonify({
        "id": company_id, "name": name, "website": website, "city": city,
        "price_file_url": f"/api/files/{stored_name}" if stored_name else None,
        "price_file_name": original_name,
    }), 201


@app.route("/api/companies/<int:company_id>/price_file", methods=["POST"])
def api_upload_price_file(company_id):
    """Прикрепить/заменить файл прайса у уже существующей компании."""
    company = db.get_company(company_id)
    if not company:
        return jsonify({"error": "Компания не найдена"}), 404
    uploaded = request.files.get("price_file")
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "Файл не передан"}), 400
    stored_name, original_name = _save_uploaded_file(uploaded)
    if stored_name is None:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        return jsonify({"error": f"Недопустимый формат файла. Разрешены: {allowed}"}), 400
    # удаляем старый файл, если был
    if company["price_file"]:
        old_path = os.path.join(UPLOAD_FOLDER, company["price_file"])
        if os.path.exists(old_path):
            os.remove(old_path)
    db.set_price_file(company_id, stored_name, original_name)
    return jsonify({"price_file_url": f"/api/files/{stored_name}", "price_file_name": original_name})


@app.route("/api/files/<path:filename>", methods=["GET"])
def api_get_file(filename):
    safe_name = secure_filename(filename)
    if safe_name != filename:
        return jsonify({"error": "Некорректное имя файла"}), 400
    return send_from_directory(UPLOAD_FOLDER, safe_name)


@app.route("/api/companies/<int:company_id>", methods=["DELETE"])
def api_delete_company(company_id):
    company = db.get_company(company_id)
    if not company:
        return jsonify({"error": "Не найдено"}), 404
    if company["price_file"]:
        file_path = os.path.join(UPLOAD_FOLDER, company["price_file"])
        if os.path.exists(file_path):
            os.remove(file_path)
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


@app.errorhandler(413)
def too_large(e):
    mb = MAX_FILE_SIZE // (1024 * 1024)
    return jsonify({"error": f"Файл слишком большой (максимум {mb} МБ)"}), 413


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
