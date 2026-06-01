-- 01_init.sql - READY TO USE

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Create schemas for each category
CREATE SCHEMA IF NOT EXISTS hukum;
CREATE SCHEMA IF NOT EXISTS medis;
CREATE SCHEMA IF NOT EXISTS hr;
CREATE SCHEMA IF NOT EXISTS teknik;
CREATE SCHEMA IF NOT EXISTS keuangan;

-- Create documents table template function
CREATE OR REPLACE FUNCTION create_documents_table(schema_name TEXT)
RETURNS VOID AS $$
BEGIN
    EXECUTE format('
        CREATE TABLE IF NOT EXISTS %I.documents (
            id BIGSERIAL PRIMARY KEY,
            content TEXT NOT NULL,
            content_tsv TSVECTOR GENERATED ALWAYS AS (
                to_tsvector(''indonesian'', COALESCE(content, ''''))
            ) STORED,
            embedding vector(1024) NOT NULL,
            file_name VARCHAR(512) NOT NULL,
            page_number INTEGER NOT NULL,
            heading_context VARCHAR(1024),
            chunk_index INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata JSONB DEFAULT ''{}''::jsonb
        )
    ', schema_name);

    -- INDEX 1: Vector similarity (HNSW)
    EXECUTE format('
        CREATE INDEX IF NOT EXISTS idx_%I_embedding_hnsw
        ON %I.documents
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    ', schema_name, schema_name);

    -- INDEX 2: Full-text search BM25
    EXECUTE format('
        CREATE INDEX IF NOT EXISTS idx_%I_fts
        ON %I.documents
        USING GIN (content_tsv)
    ', schema_name, schema_name);

    -- INDEX 3: Metadata JSONB
    EXECUTE format('
        CREATE INDEX IF NOT EXISTS idx_%I_metadata
        ON %I.documents
        USING GIN (metadata jsonb_path_ops)
    ', schema_name, schema_name);

    -- INDEX 4: Composite index untuk file_name + page_number
    EXECUTE format('
        CREATE INDEX IF NOT EXISTS idx_%I_file_page
        ON %I.documents (file_name, page_number)
    ', schema_name, schema_name);

END;
$$ LANGUAGE plpgsql;

-- Create documents tables for all schemas
SELECT create_documents_table('hukum');
SELECT create_documents_table('medis');
SELECT create_documents_table('hr');
SELECT create_documents_table('teknik');
SELECT create_documents_table('keuangan');

-- Create category registry table
CREATE TABLE IF NOT EXISTS public.categories (
    id SERIAL PRIMARY KEY,
    schema_name VARCHAR(64) UNIQUE NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    document_count INTEGER DEFAULT 0
);

-- Seed categories
INSERT INTO public.categories (schema_name, display_name, description) VALUES
    ('hukum', 'Hukum & Peraturan', 'Dokumen hukum, UU, peraturan pemerintah'),
    ('medis', 'Kesehatan & Medis', 'Dokumen medis, prosedur kesehatan, SOP rumah sakit'),
    ('hr', 'Sumber Daya Manusia', 'Kebijakan HR, prosedur cuti, peraturan karyawan'),
    ('teknik', 'Teknik & Engineering', 'Dokumen teknis, spesifikasi, manual engineering'),
    ('keuangan', 'Keuangan & Akuntansi', 'Dokumen keuangan, prosedur akuntansi, pajak')
ON CONFLICT (schema_name) DO NOTHING;

-- Function to auto-update document_count
CREATE OR REPLACE FUNCTION update_document_count()
RETURNS TRIGGER AS $$
DECLARE
    target_schema TEXT := TG_TABLE_SCHEMA;
    count_val INTEGER;
BEGIN
    EXECUTE format('SELECT COUNT(*) FROM %I.documents', target_schema) INTO count_val;
    UPDATE public.categories
    SET document_count = count_val
    WHERE schema_name = target_schema;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Create trigger for each schema
DO $$
DECLARE
    schema_record RECORD;
BEGIN
    FOR schema_record IN SELECT schema_name FROM public.categories LOOP
        EXECUTE format('
            DROP TRIGGER IF EXISTS update_count_trigger ON %I.documents;
            CREATE TRIGGER update_count_trigger
            AFTER INSERT OR DELETE ON %I.documents
            FOR EACH STATEMENT
            EXECUTE FUNCTION update_document_count()
        ', schema_record.schema_name, schema_record.schema_name);
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Initial update biar document_count = 0
UPDATE public.categories SET document_count = 0 WHERE document_count IS NULL;
