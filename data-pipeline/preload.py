#!/usr/bin/env python3
"""
Pre-download BGE-M3 embedding model & test encoding.
Jalankan sekali sebelum upload pertama agar tidak timeout.
"""

import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("preload")

if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("PRELOAD: BGE-M3 Embedding Model")
    logger.info("=" * 50)

    # Step 1: Load model
    logger.info("Tahap 1/2: Mendownload & memuat model BAAI/bge-m3 (~2.2GB)...")
    logger.info("Ini hanya perlu dilakukan SATU KALI. Sabar ya, bisa 5-10 menit...")
    sys.stdout.flush()

    start = time.time()
    from FlagEmbedding import FlagModel

    model = FlagModel(
        "BAAI/bge-m3",
        query_instruction_for_retrieval="",
        use_fp16=True,
    )
    elapsed = time.time() - start
    logger.info(f"✅ Model BGE-M3 siap dalam {elapsed:.1f} detik!")

    # Step 2: Test encoding
    logger.info("Tahap 2/2: Test encoding kalimat...")
    sys.stdout.flush()
    test = model.encode(["Ini adalah kalimat uji coba untuk RAG pipeline."])
    logger.info(f"✅ Test encoding berhasil! Shape embedding: {test.shape}")
    logger.info(f"   Sample values: {test[0][:5]} ...")
    logger.info("")
    logger.info("=" * 50)
    logger.info("PRELOAD SELESAI! Semua model siap digunakan.")
    logger.info("Sekarang kamu bisa upload dokumen melalui dashboard.")
    logger.info("=" * 50)
