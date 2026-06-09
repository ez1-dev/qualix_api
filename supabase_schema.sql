-- =============================================================================
-- QUALIFX - Schema minimo Supabase para a API certificado.py
-- Rode no SQL Editor do Supabase se as tabelas ainda nao existirem.
-- =============================================================================

create table if not exists public.lots (
  id uuid primary key default gen_random_uuid(),
  lot_key text unique not null,
  lot_number text,
  erp_codemp integer,
  erp_codlot integer,
  erp_codfil integer,
  erp_codfor integer,
  supplier_name text,
  erp_numnfc integer,
  erp_codsnf text,
  purchase_order text,
  erp_codpro text,
  erp_codder text,
  product_description text,
  erp_codfam text,
  family_description text,
  erp_codori text,
  erp_pro_exicer text,
  requires_certificate boolean default true,
  erp_codcer text,
  status_app text default 'PENDENTE',
  status_erp text default 'PENDENTE',
  synced_from_erp_at timestamptz,
  synced_to_erp_at timestamptz,
  updated_at timestamptz default now(),
  created_at timestamptz default now()
);

create table if not exists public.documents (
  id uuid primary key default gen_random_uuid(),
  lot_key text not null,
  lot_id uuid,
  erp_seqane integer,
  original_file_name text,
  file_name text,
  storage_path text,
  public_url text,
  mime_type text,
  file_size bigint,
  page_count integer,
  certificate_code text,
  document_status text default 'UPLOADED',
  final_server_path text,
  final_file_name text,
  upload_source text,
  uploaded_by text,
  uploaded_at timestamptz default now(),
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists public.certificates_ai (
  id uuid primary key default gen_random_uuid(),
  document_id uuid,
  lot_key text,
  analysis_status text,
  confidence numeric,
  code_found_by text,
  extracted_certificate_code text,
  extracted_production_lot text,
  extracted_invoice_number text,
  supplier_detected text,
  product_detected text,
  ai_summary text,
  ai_raw_json jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists public.analysis_jobs (
  id uuid primary key default gen_random_uuid(),
  document_id uuid,
  lot_key text,
  job_type text,
  job_status text,
  attempts integer default 0,
  error_message text,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists public.document_comments (
  id uuid primary key default gen_random_uuid(),
  lot_key text,
  document_id uuid,
  comment_type text,
  comment_text text,
  user_id text,
  created_at timestamptz default now()
);

create index if not exists idx_lots_status_app on public.lots(status_app);
create index if not exists idx_lots_lot_key on public.lots(lot_key);
create index if not exists idx_documents_lot_key on public.documents(lot_key);
create index if not exists idx_certificates_ai_document_id on public.certificates_ai(document_id);
create index if not exists idx_analysis_jobs_document_id on public.analysis_jobs(document_id);
create index if not exists idx_document_comments_document_id on public.document_comments(document_id);
