#!/usr/bin/env python3
"""
Ingestion module: PDF parsing (dengan dukungan Tesseract OCR), embedding, chunking,
dan penyimpanan ke PostgreSQL multi‑schema.
Juga menyediakan fungsi-fungsi manajemen kategori dan dokumen untuk dashboard.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from dotenv import load_dotenv
from FlagEmbedding import FlagModel
from llama_index.core.node_parser import SentenceSplitter
from loguru import logger
from psycopg2.extras import RealDictCursor, execute_values
from psycopg2.sql import SQL, Identifier

# ----------------------------------------------------------------------
# Konfigurasi dari environment
# ----------------------------------------------------------------------
load_dotenv()

# Set TESSDATA_PREFIX jika belum diset (untuk Tesseract OCR via tesserocr)
if not os.environ.get("TESSDATA_PREFIX"):
    # Coba deteksi路径 default di Ubuntu/Debian
    for candidate in [
        "/usr/share/tesseract-ocr/5/tessdata",
        "/usr/share/tesseract-ocr/4.00/tessdata",
        "/usr/share/tessdata",
    ]:
        if os.path.isdir(candidate):
            os.environ["TESSDATA_PREFIX"] = candidate
            logger.info(f"TESSDATA_PREFIX diset ke {candidate}")
            break

DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_USER = os.getenv("POSTGRES_USER", "rag_user")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "rag_password")
DB_NAME = os.getenv("POSTGRES_DB", "rag_main")
CONNECTION_STRING = (
    f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

EMBEDDING_MODEL_NAME = "BAAI/bge-m3"


# ----------------------------------------------------------------------
# Database connection helper
# ----------------------------------------------------------------------
def get_db_connection():
    """Membuat koneksi baru ke PostgreSQL."""
    return psycopg2.connect(CONNECTION_STRING)


# ----------------------------------------------------------------------
# Fungsi helper untuk ekstraksi metadata dari Docling
# ----------------------------------------------------------------------
def extract_document_structure(docling_doc):
    """Ekstrak peta halaman dan heading dari dokumen Docling."""
    paragraphs = []
    page_map = []
    headings = []

    for item, level in docling_doc.iterate_items():
        if hasattr(item, "label") and item.label in ("heading", "title"):
            headings.append(
                (
                    level,
                    item.text,
                    item.prov[0].absolute_pos if hasattr(item, "prov") else 0,
                    item.prov[-1].absolute_pos if hasattr(item, "prov") else 0,
                )
            )
        if hasattr(item, "text"):
            paragraphs.append(item.text)
            if hasattr(item, "prov") and item.prov:
                page_map.append(item.prov[0].page_no)
            else:
                page_map.append(0)

    return {"page_map": page_map, "headings": headings, "paragraphs": paragraphs}


def estimate_page_number(chunk_idx: int, metadata: dict) -> int:
    """Perkirakan nomor halaman untuk chunk_idx."""
    page_map = metadata.get("page_map", [])
    if page_map and chunk_idx < len(page_map):
        return page_map[chunk_idx]
    return 0


def extract_nearest_heading(chunk_text: str, metadata: dict) -> str:
    """Cari heading terdekat yang muncul di chunk."""
    headings = metadata.get("headings", [])
    for _, text, start, end in reversed(headings):
        if text in chunk_text:
            return text
    return ""


def schema_exists(conn, schema_name: str) -> bool:
    """Cek apakah schema terdaftar di public.categories."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM public.categories WHERE schema_name = %s)",
            (schema_name,),
        )
        return cur.fetchone()[0]


# ----------------------------------------------------------------------
# CRUD Kategori
# ----------------------------------------------------------------------
def get_categories(conn) -> List[Dict[str, Any]]:
    """Ambil semua kategori aktif."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT schema_name, display_name, description, document_count FROM public.categories WHERE is_active = TRUE ORDER BY schema_name"
        )
        return cur.fetchall()


def create_category(
    conn, schema_name: str, display_name: str, description: str
) -> Dict[str, Any]:
    """
    Buat kategori baru: daftar di public.categories + buat schema + tabel dokumen + index.
    """
    try:
        with conn.cursor() as cur:
            # Cek apakah sudah ada
            cur.execute(
                "SELECT 1 FROM public.categories WHERE schema_name = %s", (schema_name,)
            )
            if cur.fetchone():
                return {"success": False, "error": f"Schema '{schema_name}' sudah ada"}

            # Buat schema
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
            # Buat tabel documents
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {schema_name}.documents (
                    id BIGSERIAL PRIMARY KEY,
                    content TEXT NOT NULL,
                    content_tsv TSVECTOR GENERATED ALWAYS AS (
                        to_tsvector('indonesian', COALESCE(content, ''))
                    ) STORED,
                    embedding vector(1024) NOT NULL,
                    file_name VARCHAR(512) NOT NULL,
                    page_number INTEGER NOT NULL,
                    heading_context VARCHAR(1024),
                    chunk_index INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    metadata JSONB DEFAULT '{{}}'::jsonb
                )
            """)
            # Index
            cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{schema_name}_embedding_hnsw
                ON {schema_name}.documents USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
            """)
            cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{schema_name}_fts
                ON {schema_name}.documents USING GIN (content_tsv)
            """)
            cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{schema_name}_metadata
                ON {schema_name}.documents USING GIN (metadata jsonb_path_ops)
            """)
            cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{schema_name}_file_page
                ON {schema_name}.documents (file_name, page_number)
            """)
            # Tambahkan ke public.categories
            cur.execute(
                "INSERT INTO public.categories (schema_name, display_name, description) VALUES (%s, %s, %s)",
                (schema_name, display_name, description),
            )
            conn.commit()
            return {
                "success": True,
                "message": f"Kategori '{display_name}' berhasil dibuat",
            }
    except Exception as e:
        conn.rollback()
        logger.error(f"create_category error: {e}")
        return {"success": False, "error": str(e)}


def update_category(
    conn, schema_name: str, display_name: str, description: str
) -> Dict[str, Any]:
    """Update display name dan deskripsi kategori."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE public.categories SET display_name = %s, description = %s WHERE schema_name = %s",
                (display_name, description, schema_name),
            )
            if cur.rowcount == 0:
                return {"success": False, "error": "Kategori tidak ditemukan"}
            conn.commit()
            return {"success": True, "message": "Kategori berhasil diupdate"}
    except Exception as e:
        conn.rollback()
        return {"success": False, "error": str(e)}


def delete_category(conn, schema_name: str) -> Dict[str, Any]:
    """
    Hapus seluruh kategori: drop schema CASCADE dan hapus dari public.categories.
    """
    try:
        with conn.cursor() as cur:
            # Hapus dari registry dulu (untuk memastikan valid)
            cur.execute(
                "DELETE FROM public.categories WHERE schema_name = %s", (schema_name,)
            )
            if cur.rowcount == 0:
                return {"success": False, "error": "Kategori tidak ditemukan"}
            # Hapus schema dan semua isinya
            cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
            conn.commit()
            return {
                "success": True,
                "message": f"Kategori '{schema_name}' dan semua dokumen dihapus",
            }
    except Exception as e:
        conn.rollback()
        return {"success": False, "error": str(e)}


# ----------------------------------------------------------------------
# CRUD Dokumen (ringkasan)
# ----------------------------------------------------------------------
def get_documents(conn, schema_name: str) -> List[Dict[str, Any]]:
    """Ambil daftar file unik beserta jumlah chunk dan data upload."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"""
            SELECT
                file_name,
                COUNT(*) as chunks,
                MIN(created_at)::date as uploaded_at,
                MIN(id) as id  -- representasi id pertama (untuk keperluan delete)
            FROM {schema_name}.documents
            GROUP BY file_name
            ORDER BY uploaded_at DESC
        """)
        return cur.fetchall()


def delete_document(conn, schema_name: str, file_name: str) -> Dict[str, Any]:
    """Hapus semua chunk milik file tertentu."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                SQL("DELETE FROM {} WHERE file_name = %s").format(
                    Identifier(schema_name, "documents")
                ),
                (file_name,),
            )
            deleted = cur.rowcount
            # Update hitungan otomatis (trigger sudah ada, tapi kita panggil manual jika trigger tidak berfungsi)
            cur.execute(
                SQL(
                    """
                UPDATE public.categories
                SET document_count = (
                    SELECT COUNT(*) FROM {}.documents
                )
                WHERE schema_name = %s
            """
                ).format(Identifier(schema_name)),
                (schema_name,),
            )
            conn.commit()
            return {
                "success": True,
                "message": f"{deleted} chunk dari '{file_name}' dihapus",
            }
    except Exception as e:
        conn.rollback()
        return {"success": False, "error": str(e)}


# ----------------------------------------------------------------------
# Fungsi Utama Ingestion
# ----------------------------------------------------------------------
def ingest_document(file_path: str, target_schema: str) -> dict:
    """
    Proses lengkap: PDF -> chunk -> embedding -> simpan.
    """
    logger.info(f"Memulai ingestion: {file_path} → schema '{target_schema}'")

    conn = get_db_connection()
    conn.autocommit = False
    try:
        # Validasi schema
        if not schema_exists(conn, target_schema):
            raise ValueError(
                f"Schema '{target_schema}' tidak ditemukan di public.categories"
            )

        # ----------------------------------------------------------------------
        # Konfigurasi Tesseract OCR
        # ----------------------------------------------------------------------
        logger.info("Mengonfigurasi Docling dengan Tesseract OCR (id/en)...")
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.ocr_options = TesseractOcrOptions(
            lang=["ind", "eng"],
            force_full_page_ocr=True,
        )

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )

        logger.info("Memulai ekstraksi dokumen...")
        result = converter.convert(file_path)
        markdown_content = result.document.export_to_markdown()

        # Metadata struktural
        metadata_struct = extract_document_structure(result.document)

        # Chunking
        logger.info("Chunking teks...")
        parser = SentenceSplitter(
            chunk_size=512, chunk_overlap=64, separator=" ", paragraph_separator="\n\n"
        )
        chunks = parser.split_text(markdown_content)
        logger.info(f"{len(chunks)} chunk dibuat")

        # Embeddings
        logger.info("Memuat model BGE-M3...")
        model = FlagModel(
            EMBEDDING_MODEL_NAME, query_instruction_for_retrieval="", use_fp16=True
        )
        chunk_embeddings = model.encode(chunks)
        logger.info(f"Shape embedding: {chunk_embeddings.shape}")

        # Bangun record
        records = []
        for i, (chunk_text, emb) in enumerate(zip(chunks, chunk_embeddings)):
            records.append(
                {
                    "content": chunk_text,
                    "embedding": emb.tolist(),
                    "file_name": Path(file_path).name,
                    "page_number": estimate_page_number(i, metadata_struct),
                    "heading_context": extract_nearest_heading(
                        chunk_text, metadata_struct
                    ),
                    "chunk_index": i,
                    "metadata_json": json.dumps(
                        {
                            "original_file": str(Path(file_path).resolve()),
                            "ingestion_timestamp": datetime.now(
                                timezone.utc
                            ).isoformat(),
                            "chunk_size": len(chunk_text),
                        }
                    ),
                }
            )

        # Insert
        logger.info("Menyimpan ke database...")
        with conn.cursor() as cur:
            cur.execute("SET search_path TO %s", (target_schema,))

            insert_sql = """
                INSERT INTO documents
                    (content, embedding, file_name, page_number, heading_context, chunk_index, metadata)
                VALUES %s
            """
            values = []
            for rec in records:
                emb_str = "[" + ",".join(f"{x:.8f}" for x in rec["embedding"]) + "]"
                values.append(
                    (
                        rec["content"],
                        emb_str,
                        rec["file_name"],
                        rec["page_number"],
                        rec["heading_context"],
                        rec["chunk_index"],
                        rec["metadata_json"],
                    )
                )
            execute_values(cur, insert_sql, values)

            # Update hitungan (jika trigger tidak otomatis)
            cur.execute(
                SQL(
                    """
                UPDATE public.categories
                SET document_count = (
                    SELECT COUNT(*) FROM {}.documents
                )
                WHERE schema_name = %s
            """
                ).format(Identifier(target_schema)),
                (target_schema,),
            )

        conn.commit()
        inserted = len(values)
        logger.success(f"Ingestion selesai: {inserted} chunk tersimpan")
        return {
            "status": "success",
            "chunks_inserted": inserted,
            "target_schema": target_schema,
        }

    except Exception as e:
        conn.rollback()
        logger.error(f"Ingestion gagal: {e}")
        return {
            "status": "error",
            "chunks_inserted": 0,
            "target_schema": target_schema,
            "error": str(e),
        }
    finally:
        conn.close()


# ----------------------------------------------------------------------
# CLI support (jika dijalankan langsung)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RAG Document Ingestion")
    parser.add_argument("--file", required=True, help="Path file PDF")
    parser.add_argument("--schema", required=True, help="Schema target (contoh: hukum)")
    args = parser.parse_args()
    result = ingest_document(args.file, args.schema)
    print(json.dumps(result, indent=2))
