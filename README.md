
# Minimalist RAG

A lightweight, production-ready Retrieval-Augmented Generation (RAG) pipeline for Indonesian legal, medical, and financial documents.

> **Perhatian:** Dokumentasi teknis lengkap ada di [`data-pipeline/README.md`](data-pipeline/README.md).

## Quick Start

```bash
# 1. Start PostgreSQL with pgvector
docker compose up -d

# 2. Setup Python environment
cd data-pipeline
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo apt update
sudo apt install tesseract-ocr tesseract-ocr-ind -y
sudo apt update
sudo apt install libtesseract-dev libleptonica-dev pkg-config -y
# 3. Preload embedding model (once)
python preload.py

# 4. Run dashboard
python app.py
```

Buka `http://localhost:5000` untuk mengupload dan mengelola dokumen.

## Struktur Proyek

```
minimalist-rag/
├── data-pipeline/        # Python: ingestion, embedding, dashboard
│   ├── app.py            # Flask dashboard
│   ├── ingest.py         # Docling (OCR) + chunking + embedding + DB
│   ├── preload.py        # Pre-download BGE-M3 model
│   ├── requirements.txt
│   ├── templates/
│   ├── .gitignore
│   ├── .env.example
│   └── sample/           # Dokumen contoh & hasil ekstraksi
│       ├── BUKU OUD BATAVIA 1935_50halaman.pdf     # 4.6 MB — 50 hal (scan, OCR)
│       ├── Principles by Ray Dalio_page-0001.pdf   # 84 KB — 1 hal
│       ├── ray_dalio_how_the_economic_machine_works.pdf  # 3.1 MB — 300 hal
│       └── results/       # Output Markdown hasil parsing
│           ├──BUKU_OUD_BATAVIA_1935.md               # 762 baris
│           ├──Principles_by_Ray_Dalio_page-0001.pdf.md # 47 baris
│           └──ray_dalio_how_the_economic_machine_works.md # 9.569 baris
├── database/
│   └── init.sql          # PostgreSQL schema + indexes + triggers
├── docker-compose.yaml   # PostgreSQL + pgvector container
├── README.md             # This file
└── retrieval/            # Go service (pending)
```

## Lisensi

MIT
