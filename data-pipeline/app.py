#!/usr/bin/env python3
"""
Flask application untuk Pipeline Manager Dashboard.
Melayani halaman HTML dan API endpoint manajemen kategori & ingestion.
"""

import logging
import os

from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

from ingest import (
    create_category,
    delete_category,
    delete_document,
    get_categories,
    get_db_connection,
    get_documents,
    ingest_document,
    update_category,
)

# ----------------------------------------------------------------------
# Logging configuration
# ----------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB
app.config["UPLOAD_FOLDER"] = "/tmp/rag_uploads"
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


# ----------------------------------------------------------------------
# Halaman Utama (Dashboard)
# ----------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# ----------------------------------------------------------------------
# API Categories
# ----------------------------------------------------------------------
@app.route("/api/categories", methods=["GET", "POST"])
def handle_categories():
    conn = get_db_connection()
    try:
        if request.method == "GET":
            categories = get_categories(conn)
            # Ubah RealDictRow ke list of dict normal
            return jsonify(
                {"success": True, "categories": [dict(row) for row in categories]}
            )

        elif request.method == "POST":
            data = request.get_json()
            if not data or "schema_name" not in data or "display_name" not in data:
                return jsonify(
                    {"success": False, "error": "schema_name dan display_name wajib"}
                ), 400

            result = create_category(
                conn,
                data["schema_name"],
                data["display_name"],
                data.get("description", ""),
            )
            status_code = 200 if result["success"] else 400
            return jsonify(result), status_code

        return jsonify({"success": False, "error": "Method not allowed"}), 405
    finally:
        conn.close()


@app.route("/api/categories/<schema_name>", methods=["PUT", "DELETE"])
def handle_category(schema_name):
    conn = get_db_connection()
    try:
        if request.method == "PUT":
            data = request.get_json()
            if not data:
                return jsonify({"success": False, "error": "Data kosong"}), 400
            result = update_category(
                conn,
                schema_name,
                data.get("display_name", ""),
                data.get("description", ""),
            )
        elif request.method == "DELETE":
            result = delete_category(conn, schema_name)
        else:
            return jsonify({"success": False, "error": "Method not allowed"}), 405
        status_code = 200 if result["success"] else 400
        return jsonify(result), status_code
    finally:
        conn.close()


# ----------------------------------------------------------------------
# API Documents
# ----------------------------------------------------------------------
@app.route("/api/documents/<schema_name>", methods=["GET"])
def list_documents(schema_name):
    conn = get_db_connection()
    try:
        docs = get_documents(conn, schema_name)
        return jsonify({"success": True, "documents": [dict(row) for row in docs]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/documents/<schema_name>/<path:file_name>", methods=["DELETE"])
def remove_document(schema_name, file_name):
    conn = get_db_connection()
    try:
        result = delete_document(conn, schema_name, file_name)
        status_code = 200 if result["success"] else 400
        return jsonify(result), status_code
    finally:
        conn.close()


# ----------------------------------------------------------------------
# API Ingestion (Upload PDF)
# ----------------------------------------------------------------------
@app.route("/api/ingest", methods=["POST"])
def ingest_file():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "File tidak ditemukan"}), 400

    file = request.files["file"]
    schema_name = request.form.get("category")

    if not schema_name:
        return jsonify(
            {"success": False, "error": "Kategori (category) diperlukan"}
        ), 400
    if file.filename == "":
        return jsonify({"success": False, "error": "Nama file kosong"}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    file_size = os.path.getsize(filepath)
    logger.info(
        f"Menerima file: {filename} ({file_size} bytes) -> kategori: {schema_name}"
    )

    try:
        logger.info(f"Memulai ingestion: {filename} -> {schema_name}")
        result = ingest_document(filepath, schema_name)
        logger.info(f"Hasil ingestion: {result}")
        return jsonify(result)
    except Exception as e:
        logger.error(f"Ingestion gagal: {e}", exc_info=True)
        return jsonify(
            {
                "status": "error",
                "chunks_inserted": 0,
                "target_schema": schema_name,
                "error": f"Internal error: {str(e)}",
            }
        ), 500
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.info(f"File sementara dihapus: {filepath}")


# ----------------------------------------------------------------------
# Start server
# ----------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
