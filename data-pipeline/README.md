
> **📄 Bahasa:** Dokumentasi ini ditulis dalam Bahasa Indonesia.

# Minimalist RAG — Dokumentasi Teknis

Sistem RAG (Retrieval-Augmented Generation) ringan untuk dokumen hukum, medis, dan keuangan berbahasa Indonesia. Fokus pada ekstraksi teks dan tabel — elemen grafis sengaja dikecualikan karena keterbatasan VLM (Vision Language Model) saat ini.

---

## Daftar Isi

- [Arsitektur Sistem](#arsitektur-sistem)
- [Alur Data Pipeline](#alur-data-pipeline)
- [Komponen](#komponen)
- [Database Schema](#database-schema)
- [Quick Start](#quick-start)
- [Konfigurasi](#konfigurasi)
- [Keterbatasan](#keterbatasan)
- [Status Proyek](#status-proyek)

---

## Arsitektur Sistem

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         DATA PIPELINE (Python)                               │
│                                                                              │
│  ┌────────┐   ┌──────────┐   ┌────────────────┐   ┌──────────────────────┐  │
│  │  PDF   │──▶│ Docling  │──▶│ SentenceSplitter│──▶│   BGE-M3 Embedding   │  │
│  │(input) │   │(parse)   │   │(chunking 512)   │   │   (FlagModel)        │  │
│  └────────┘   └──────────┘   └────────────────┘   └──────────┬───────────┘  │
│                    │                                          │              │
│                    ▼                                          │              │
│          ┌─────────────────┐                                  │              │
│          │ Markdown output │                                  │              │
│          │ - teks ✅       │                                  │              │
│          │ - tabel ✅      │                                  │              │
│          │ - grafik ❌     │                                  │              │
│          └─────────────────┘                                  │              │
│                                                               ▼              │
│                                                    ┌──────────────────────┐  │
│                                                    │  PostgreSQL + pgvector│  │
│                                                    │  - HNSW index (cosine)│  │
│                                                    │  - BM25 via tsvector  │  │
│                                                    │  - Multi-schema       │  │
│                                                    │  - Auto-count trigger │  │
│                                                    └──────────────────────┘  │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │                     Flask Dashboard (app.py)                         │    │
│  │  ┌────────────┐  ┌──────────────┐  ┌────────────┐  ┌─────────────┐  │    │
│  │  │ Upload PDF │  │ Category CRUD│  │ Document   │  │ Real-time   │  │    │
│  │  │            │  │ (create/edit │  │ List/Delete│  │ Log Output  │  │    │
│  │  │            │  │  /delete)    │  │            │  │             │  │    │
│  │  └────────────┘  └──────────────┘  └────────────┘  └─────────────┘  │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ koneksi DB langsung
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                       RETRIEVAL SERVICE (Go) — ⏳ Pending                    │
│                                                                              │
│  ┌───────┐   ┌───────────┐   ┌───────────────┐   ┌──────┐   ┌──────────┐   │
│  │ Query │──▶│ Embedding │──▶│ Hybrid Search  │──▶│Rerank│──▶│ LLM      │   │
│  │(user) │   │(BGE-M3)   │   │(BM25 + Vector) │   │      │   │(DeepSeek)│   │
│  └───────┘   └───────────┘   └───────────────┘   └──────┘   └──────────┘   │
│                                                                              │
│  Keterangan: Service ini belum diimplementasikan. Rencananya menggunakan     │
│  Go 1.21+ dengan stdlib net/http, tanpa framework eksternal.                │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Alur Data Pipeline

Pipeline dimulai dari unggahan file PDF hingga tersimpan sebagai vektor di database. Berikut adalah penjelasan langkah demi langkah:

### Langkah 1: Unggah PDF (via Dashboard)

```
Pengguna ──▶ Pilih kategori ──▶ Pilih file PDF ──▶ Klik "Upload"
                                                      │
                                                      ▼
                                              app.py (/api/ingest)
                                                      │
                                                      ├── Validasi file (.pdf)
                                                      ├── Secure filename
                                                      ├── Simpan ke /tmp/rag_uploads
                                                      └── Panggil ingest_document()
```

Pengguna mengunggah dokumen PDF melalui halaman dashboard (`http://localhost:5000`). File diverifikasi, diberi nama aman, dan disimpan sementara sebelum diproses.

### Langkah 2: Parsing PDF (Docling + Tesseract OCR)

```
PDF ──▶ DocumentConverter(PdfFormatOption) ──▶ export_to_markdown()
           │                                      │
           │ pipeline_options:                    ▼
           │   do_ocr = True              Markdown string
           │   ocr_options:                     │
           │     lang = ["ind", "eng"]          ▼
           │     force_full_page_ocr = True  extract_document_structure()
           │                                      │
           ▼                                      ├── page_map[]     : nomor halaman
Tesseract OCR                                     ├── headings[]     : daftar heading
(engine ocr_ind & tesserocr)                      └── paragraphs[]   : teks per paragraf
```

**Docling** (v2.23.0) + **Tesseract OCR** (`tesserocr` 2.8.0):
- OCR menggunakan engine Tesseract dengan bahasa Indonesia (`ind`) dan Inggris (`eng`).
- `force_full_page_ocr=True` — OCR diterapkan ke seluruh halaman, bukan hanya area gambar.
- `TESSDATA_PREFIX` dideteksi otomatis untuk path Ubuntu/Debian.
- Mempertahankan struktur tabel sebagai Markdown.
- Gambar/grafik diabaikan karena akurasi ekstraksi dari VLM masih di bawah ambang batas produksi.
- Metadata struktural (peta halaman, heading) diekstrak untuk konteks chunk nantinya.

### Langkah 3: Chunking (LlamaIndex SentenceSplitter)

```
Markdown ──▶ SentenceSplitter(chunk_size=512, overlap=64)
                │
                ▼
          List of chunks [c₁, c₂, c₃, ..., cₙ]
                │
                ▼
          Untuk setiap chunk:
          - estimate_page_number(i)   → perkiraan halaman asal
          - extract_nearest_heading() → heading terdekat dalam chunk
```

**Parameter chunking:**
| Parameter | Nilai | Keterangan |
|-----------|-------|------------|
| `chunk_size` | 512 | Jumlah token per chunk |
| `chunk_overlap` | 64 | Token overlap antar chunk berurutan |
| `separator` | `" "` | Pemisah antar kata |
| `paragraph_separator` | `"\n\n"` | Pemisah antar paragraf |

Overlap 64 token memastikan tidak ada konteks yang terputus di batas antar chunk.

### Langkah 4: Embedding (BGE-M3)

```
Chunks ──▶ FlagModel("BAAI/bge-m3").encode(chunks)
              │
              ▼
        NumPy array shape (n_chunks, 1024)
              │
              ▼
        Setiap embedding → list of float (1024 dimensi)
```

**BGE-M3** (FlagEmbedding 1.2.11):
- Output vektor 1024 dimensi.
- Berjalan di CPU (FP16) — lebih lambat tapi tanpa biaya GPU.
- Ukuran model ~2.2 GB, di-download otomatis saat pertama kali dijalankan.
- Gunakan `python preload.py` sebelum upload pertama untuk menghindari timeout.

### Langkah 5: Penyimpanan ke PostgreSQL

```
Records ──▶ INSERT INTO {schema}.documents (...) VALUES (...)
                │
                ├── content        : teks chunk
                ├── embedding      : vector(1024)
                ├── file_name      : nama file asal
                ├── page_number    : perkiraan halaman
                ├── heading_context: heading terdekat
                ├── chunk_index    : urutan chunk ke-i
                └── metadata       : JSONB (path file, timestamp, ukuran)
```

Setelah insert, trigger `update_document_count()` otomatis memperbarui jumlah dokumen di `public.categories`.

### Diagram Alur Lengkap

```
                         ┌──────────────┐
                         │  User Upload │
                         │   (PDF file) │
                         └──────┬───────┘
                                │
                                ▼
                     ┌─────────────────────┐
                     │  app.py             │
                     │  /api/ingest POST   │
                     │  - validasi kategori│
                     │  - simpan file temp │
                     └──────────┬──────────┘
                                │
                                ▼
                     ┌─────────────────────┐
                     │  ingest.py          │
                     │  ingest_document()  │
                     └──────────┬──────────┘
                                │
                    ┌───────────┴───────────┐
                    │                       │
                    ▼                       ▼
         ┌──────────────────┐   ┌──────────────────────┐
         │ 1. Parse PDF     │   │ Validasi schema      │
         │    (Docling)     │   │ di public.categories │
         └────────┬─────────┘   └──────────────────────┘
                  │
                  ▼
         ┌──────────────────┐
         │ 2. Markdown      │
         │    + struktur    │
         └────────┬─────────┘
                  │
                  ▼
         ┌──────────────────┐
         │ 3. Chunking      │
         │    (512/64)      │
         └────────┬─────────┘
                  │
                  ▼
         ┌──────────────────┐
         │ 4. Embedding     │
         │    (BGE-M3)      │
         └────────┬─────────┘
                  │
                  ▼
         ┌──────────────────┐
         │ 5. INSERT INTO   │
         │    PostgreSQL    │
         │    + auto trigger│
         └──────────────────┘
```

---

## Komponen

| Komponen | Teknologi | Fungsi |
|----------|-----------|--------|
| PDF Parser | Docling 2.23+ | Ekstrak teks dan tabel ke Markdown |
| Chunking | LlamaIndex SentenceSplitter | Potong teks jadi chunk 512 token (overlap 64) |
| Embedding | BGE-M3 (FlagEmbedding 1.2.11) | Vektor 1024 dimensi, jalan di CPU |
| Vector DB | PostgreSQL 16 + pgvector 0.5+ | HNSW indexing, multi-schema, BM25 |
| Dashboard | Flask 3.0 | Upload dokumen, manajemen kategori |
| Retrieval | Go 1.21+ (net/http stdlib) | Hybrid search, LLM orchestration (⏳ pending) |
| LLM | DeepSeek API (eksternal) | Generate jawaban dari konteks (⏳ pending) |

---

## Database Schema

```
┌──────────────────────────────────────────────────────────────────┐
│                  public.categories (registry)                    │
├──────────────────────────────────────────────────────────────────┤
│  id          │ SERIAL PRIMARY KEY                               │
│  schema_name │ VARCHAR(64) UNIQUE NOT NULL                       │
│  display_name│ VARCHAR(128) NOT NULL                             │
│  description │ TEXT                                              │
│  is_active   │ BOOLEAN DEFAULT TRUE                              │
│  created_at  │ TIMESTAMP DEFAULT CURRENT_TIMESTAMP                │
│  document_count│ INTEGER DEFAULT 0                               │
└───────────────────────────┬──────────────────────────────────────┘
                           │
                           │ 1:N (setiap baris = satu schema di PostgreSQL)
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                  {schema_name}.documents                          │
├──────────────────────────────────────────────────────────────────┤
│  id             │ BIGSERIAL PRIMARY KEY                          │
│  content        │ TEXT NOT NULL                                  │
│  content_tsv    │ TSVECTOR GENERATED ALWAYS                      │
│                 │   to_tsvector('indonesian', content)           │
│  embedding      │ VECTOR(1024) NOT NULL                          │
│  file_name      │ VARCHAR(512) NOT NULL                          │
│  page_number    │ INTEGER NOT NULL                               │
│  heading_context│ VARCHAR(1024)                                   │
│  chunk_index    │ INTEGER NOT NULL                               │
│  created_at     │ TIMESTAMP DEFAULT CURRENT_TIMESTAMP             │
│  metadata       │ JSONB DEFAULT '{}'                             │
└──────────────────────────────────────────────────────────────────┘

Indexes (dibuat otomatis per schema):
  ├─ idx_{schema}_embedding_hnsw  ── HNSW (vector_cosine_ops, m=16, ef=64)
  ├─ idx_{schema}_fts             ── GIN  (BM25 via tsvector)
  ├─ idx_{schema}_metadata        ── GIN  (jsonb_path_ops)
  └─ idx_{schema}_file_page       ── BTREE (file_name, page_number)
```

### Multi-Schema Isolation

Setiap kategori dokumen memiliki **schema PostgreSQL sendiri** (`hukum`, `medis`, `hr`, `teknik`, `keuangan`). Ini memberikan:

- **Isolasi data**: Tidak mungkin dokumen hukum tercampur dengan dokumen medis.
- **Keamanan**: Hak akses bisa diatur per schema.
- **Kinerja**: Ukuran index per schema lebih kecil, query lebih cepat.
- **Fleksibilitas**: Schema bisa ditambah/dihapus tanpa mempengaruhi schema lain.

### Auto-Update Trigger

Setiap kali dokumen ditambahkan atau dihapus, trigger `update_document_count()` otomatis memperbarui kolom `document_count` di `public.categories`, sehingga dashboard selalu menampilkan jumlah yang akurat.

---

## Quick Start

### Prasyarat

- Docker & Docker Compose
- Python 3.11+
- Go 1.21+ (untuk retrieval service — belum digunakan)

### Instalasi

```bash
# Clone repository
git clone <repository-url>
cd minimalist-rag

# Start PostgreSQL dengan pgvector
docker compose up -d

# Setup environment Python
cd data-pipeline
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# atau: .venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
sudo apt update
sudo apt install tesseract-ocr tesseract-ocr-ind -y
sudo apt update
sudo apt install libtesseract-dev libleptonica-dev pkg-config -y

# Preload model embedding (wajib sekali sebelum upload pertama)
python preload.py

# Jalankan dashboard
python app.py
```

### Upload Dokumen

1. Buka `http://localhost:5000`
2. Pilih kategori (hukum, medis, hr, teknik, keuangan)
3. Upload file PDF
4. Pantau progress — ingestion dokumen 300 halaman selesai dalam 10-20 menit

### Verifikasi

```bash
# Masuk ke database
docker exec -it rag_db psql -U rag_user -d rag_main

# Lihat jumlah chunk per file di schema tertentu
SET search_path TO keuangan;
SELECT file_name, COUNT(*) as chunks FROM documents GROUP BY file_name;
```

---

## Performa

Lingkungan uji: Ryzen 6000H, 16GB RAM, CPU-only (tanpa GPU)

| Dokumen | Halaman | Chunk | Parse | Embed | Total | Suhu Maks |
|---------|---------|-------|-------|-------|-------|-----------|
| How the Economic Machine Works (Ray Dalio) | 300 | 548 | 7 menit | 11.7 menit | **18.7 menit** | 99°C |
| BUKU OUD BATAVIA 1935 (OCR) | 50 | 40 | 108.74 dtk (OCR) | 53.5 dtk | **~2.7 menit** | 99°C |

> **Catatan:** Beban CPU berkelanjutan saat embedding mencapai 88-99°C. Ini masih dalam spesifikasi Ryzen 6000 (thermal limit 105°C). Pastikan ventilasi memadai. OCR pada dokumen lama (non-digital) menambah waktu parsing ~2 menit untuk 50 halaman. Suhu konsisten di kisaran yang sama baik dengan maupun tanpa OCR.

---

## Konfigurasi

### Environment Variables (`.env`)

```bash
POSTGRES_USER=rag_user
POSTGRES_PASSWORD=rag_password
POSTGRES_DB=rag_main
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
```

### Parameter Chunking (di `ingest.py`)

```python
chunk_size = 512      # token per chunk
overlap = 64          # overlap antar chunk berurutan
```

### Konfigurasi Tesseract OCR (di `ingest.py`)

```python
pipeline_options = PdfPipelineOptions()
pipeline_options.do_ocr = True
pipeline_options.ocr_options = TesseractOcrOptions(
    lang=["ind", "eng"],
    force_full_page_ocr=True,
)
```

`TESSDATA_PREFIX` dideteksi otomatis dari:
- `/usr/share/tesseract-ocr/5/tessdata`
- `/usr/share/tesseract-ocr/4.00/tessdata`
- `/usr/share/tessdata`

---

## Keterbatasan

| Keterbatasan | Alasan |
|-------------|--------|
| **Grafik/gambar diabaikan** | VLM (GPT-4o, Gemini, Claude) masih salah interpretasi grafik ekonomi kompleks. Akurasi di bawah ambang produksi. |
| **OCR hanya id/eng** | Bahasa lain belum didukung. Tambahkan lang pack (`tesseract-ocr-xxx`) untuk bahasa tambahan. |
| **Tidak ada autentikasi** | Single-user MVP. Akan ditambahkan jika pengguna > 5. |
| **Tidak ada quota system** | Tidak ada dependensi API berbayar. Akan ditambahkan jika menggunakan layanan billable. |
| **CPU-only embedding** | Lebih lambat tapi gratis. Akselerasi GPU opsional. |
| **Retrieval service belum siap** | Masih dalam pengembangan (Go). |

---

## Status Proyek

| Komponen | Status |
|----------|--------|
| Data Pipeline (ingestion) | ✅ Selesai |
| Database Schema | ✅ Selesai |
| Dashboard (Flask) | ✅ Selesai |
| Retrieval Service (Go) | ⏳ Dalam pengembangan |

---

## Struktur Direktori

```
minimalist-rag/
├── data-pipeline/
│   ├── app.py                 # Flask dashboard — route API & halaman
│   ├── ingest.py              # Logika ingestion: parse, chunk, embed, simpan
│   ├── preload.py             # Pre-download model BGE-M3
│   ├── sample/                # Dokumen contoh uji & hasil ekstraksi
│   │   ├── BUKU OUD BATAVIA 1935_50halaman.pdf          # 4.6 MB — scan Belanda 1935, perlu OCR
│   │   ├── Principles by Ray Dalio_page-0001.pdf        # 84 KB — 1 halaman sampul
│   │   ├── ray_dalio_how_the_economic_machine_works.pdf # 3.1 MB — 300 halaman digital-born
│   │   └── results/             # Output Markdown hasil ekstraksi Docling + OCR
│   │       ├── BUKU_OUD_BATAVIA_1935.md                  # 762 baris — OCR id/eng
│   │       ├── Principles_by_Ray_Dalio_page-0001.pdf.md  # 47 baris — 1 chunk
│   │       └── ray_dalio_how_the_economic_machine_works.md # 9.569 baris — 548 chunk
│   ├── requirements.txt       # Dependensi Python
│   └── templates/
│       └── index.html         # HTML dashboard (single page)
├── database/
│   └── init.sql               # Schema SQL: extension, tabel, index, trigger
├── docker-compose.yaml        # Container PostgreSQL + pgvector
├── README.md                  # Root README (link ke sini)
└── retrieval/                 # Go service (belum dibuat)
```

---

## Lisensi

MIT
