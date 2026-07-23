import os
import re
import json
import uuid
import shutil
from datetime import datetime, timedelta, timezone, date
from typing import Any, Optional
from urllib.parse import quote, urlparse

import pyodbc
import requests
import fitz
from PIL import Image
from dotenv import load_dotenv
from jose import jwt, JWTError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Depends, Query, UploadFile, File, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

load_dotenv()

# =============================================================================
# CONFIG
# =============================================================================
# Configure por variáveis de ambiente (.env ao lado deste arquivo):
#
# SECRET_KEY=ERP_SECRET
# ALGORITHM=HS256
#
# SQL_DRIVER=ODBC Driver 17 for SQL Server
# SQL_SERVER=172.16.137.100
# SQL_DATABASE=sapiens
# SQL_USER=sapiens
# SQL_PASSWORD=SUA_SENHA
# EMPRESA_PADRAO=1
#
# LOVABLE_DB_URL=https://SEU-PROJETO.supabase.co
# LOVABLE_DB_KEY=SUA_CHAVE_COM_ESCRITA
#
# LOVABLE_TABLE_LOTS=lots
# LOVABLE_TABLE_DOCUMENTS=documents
# LOVABLE_TABLE_CERTIFICATES_AI=certificates_ai
# LOVABLE_TABLE_ANALYSIS_JOBS=analysis_jobs
# LOVABLE_TABLE_DOCUMENT_COMMENTS=document_comments
#
# APP_ADMIN_PASSWORD=123
# APP_RENATO_PASSWORD=123
# APP_TRIBUTOS_PASSWORD=123456
#
# UPLOAD_DIR=uploads
# FINAL_SERVER_BASE_PATH=\\\\SERVIDOR\\CERTIFICADOS
# USE_MULTIEMPRESA_LOT_KEY=false
#
# Rodar:
# python certificado.py
# ou
# uvicorn certificado:app --reload --host 0.0.0.0 --port 8004

def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


SECRET_KEY = env("SECRET_KEY", "ERP_SECRET")
ALGORITHM = env("ALGORITHM", "HS256")

SQL_DRIVER = env("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
SQL_SERVER = env("SQL_SERVER", "")
SQL_DATABASE = env("SQL_DATABASE", "")
SQL_USER = env("SQL_USER", "")
SQL_PASSWORD = env("SQL_PASSWORD", "")
EMPRESA_PADRAO = int(env("EMPRESA_PADRAO", "1"))

LOVABLE_DB_URL = env("LOVABLE_DB_URL", "").rstrip("/")
LOVABLE_DB_KEY = env("LOVABLE_DB_KEY", "")

TABLE_LOTS = env("LOVABLE_TABLE_LOTS", "lots")
TABLE_DOCUMENTS = env("LOVABLE_TABLE_DOCUMENTS", "documents")
TABLE_CERTIFICATES_AI = env("LOVABLE_TABLE_CERTIFICATES_AI", "certificates_ai")
TABLE_ANALYSIS_JOBS = env("LOVABLE_TABLE_ANALYSIS_JOBS", "analysis_jobs")
TABLE_DOCUMENT_COMMENTS = env("LOVABLE_TABLE_DOCUMENT_COMMENTS", "document_comments")

UPLOAD_DIR = env("UPLOAD_DIR", "uploads")
FINAL_SERVER_BASE_PATH = env("FINAL_SERVER_BASE_PATH", r"\\SERVIDOR\CERTIFICADOS")
USE_MULTIEMPRESA_LOT_KEY = env("USE_MULTIEMPRESA_LOT_KEY", "false").lower() == "true"

SYNC_SCHEDULE_ENABLED = env("SYNC_SCHEDULE_ENABLED", "true").lower() == "true"
SYNC_HOUR = int(env("SYNC_HOUR", "20"))
SYNC_MINUTE = int(env("SYNC_MINUTE", "0"))
SYNC_TIMEZONE = env("SYNC_TIMEZONE", "America/Sao_Paulo")

os.makedirs(UPLOAD_DIR, exist_ok=True)

USERS = {
    "ADMIN": {
        "senha": env("APP_ADMIN_PASSWORD", ""),
        "nome": "Administrador",
        "role": "admin",
    },
    "RENATO": {
        "senha": env("APP_RENATO_PASSWORD", ""),
        "nome": "Renato",
        "role": "admin",
    },
    "TRIBUTOS": {
        "senha": env("APP_TRIBUTOS_PASSWORD", ""),
        "nome": "Tributos",
        "role": "user",
    },
}

app = FastAPI(
    title="QualifX ERP API",
    version="1.0.0",
    description="API externa para Lovable + ERP Senior",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=True)


# =============================================================================
# MODELOS
# =============================================================================

class LoginRequest(BaseModel):
    usuario: str
    senha: str


class AnalyzeRequest(BaseModel):
    texto_extraido: Optional[str] = None
    forcar_codigo_certificado: Optional[str] = None


class ApproveRequest(BaseModel):
    lot_key: str
    document_id: str
    certificate_code: str
    approved_by: Optional[str] = None
    notes: Optional[str] = None
    final_file_name: Optional[str] = None
    final_server_path: Optional[str] = None


class ApproveMultipleRequest(BaseModel):
    lot_key: str
    document_ids: list[str]
    certificate_code: str
    approved_by: Optional[str] = None
    notes: Optional[str] = None


class ImportDocumentRequest(BaseModel):
    lot_key: Optional[str] = None
    original_file_name: str
    source_url: str
    uploaded_by: Optional[str] = None


class RejectRequest(BaseModel):
    lot_key: str
    document_id: str
    rejected_by: Optional[str] = None
    reason: str


class ReturnToAnalysisRequest(BaseModel):
    lot_key: str
    document_id: str
    returned_by: Optional[str] = None
    reason: Optional[str] = None


class AlterRequest(BaseModel):
    lot_key: str
    document_id: str
    altered_by: Optional[str] = None
    certificate_code: Optional[str] = None
    final_file_name: Optional[str] = None
    final_server_path: Optional[str] = None
    notes: Optional[str] = None


class JustifyDeviationRequest(BaseModel):
    lot_key: str
    document_id: str
    justified_by: Optional[str] = None
    reason: str


class RastreabilidadeMateriaPrimaRequest(BaseModel):
    relatorio: int = 1
    empresa: int = EMPRESA_PADRAO
    filial: Optional[int] = None
    obra: Optional[int] = None
    projeto: Optional[int] = None
    codigo_relatorio: Optional[str] = None
    revisao_documento: Optional[str] = "0.0"
    listar_posicao_desenho: Optional[str] = "S"
    ordenacao: Optional[str] = "PROJETO"
    # Filtros avançados (da tela e615prj / u900lcm)
    numdes: Optional[int] = None
    codori: Optional[str] = None
    numorp: Optional[int] = None
    limit: int = 1000


class GravarLotesOpRequest(BaseModel):
    empresa: int = EMPRESA_PADRAO
    origem: str
    op: int
    executar: bool = False
    usar_derivacao: bool = True
    criar_apontamento_se_necessario: bool = False


class RastreabilidadeTintasRequest(BaseModel):
    empresa: int = EMPRESA_PADRAO
    filial: Optional[int] = None
    obra: Optional[int] = None
    codigo_relatorio: Optional[str] = ""
    revisao_documento: Optional[str] = "0.0"
    data_inicial: Optional[str] = None
    data_final: Optional[str] = None
    listar_imagens_certificados: Optional[str] = "S"
    ordenacao: Optional[str] = "DATA"
    limit: int = 1000


class ListaConjuntosRequest(BaseModel):
    empresa: int = EMPRESA_PADRAO
    obra: Optional[int] = None
    desenho: Optional[int] = None
    periodo_entrada: Optional[str] = None
    data_inicial: Optional[str] = None
    data_final: Optional[str] = None
    codigo_barras: Optional[str] = None
    descricao_produto: Optional[str] = None
    limit: int = 50000
    offset: int = 0


# Origens (E075PRO.CODORI) que identificam matéria-prima no cadastro:
# MPM (metálica), MPG (Genius), MPE (esquadrias), MPP (pré-moldados), MCP (civil).
ORIGENS_MATERIA_PRIMA = ["MPM", "MPG", "MPE", "MPP", "MCP"]


class RastreabilidadeItensComerciaisRequest(BaseModel):
    # Itens COMPRADOS (E075PRO.TIPPRO='C') da Relação dos Elementos (USU_T900REE):
    # parafusos/porcas/arruelas etc., separados dos conjuntos fabricados.
    empresa: int = EMPRESA_PADRAO
    obra: Optional[int] = None
    desenho: Optional[int] = None
    codigo_produto: Optional[str] = None
    descricao_produto: Optional[str] = None
    limit: int = 50000
    offset: int = 0


class ListaMateriaPrimaObraRequest(BaseModel):
    empresa: int = EMPRESA_PADRAO
    obra: Optional[int] = None
    desenho: Optional[int] = None
    op: Optional[int] = None
    origem_op: Optional[str] = None
    codigo_componente: Optional[str] = None
    descricao_produto: Optional[str] = None
    data_inicial: Optional[str] = None
    data_final: Optional[str] = None
    limit: int = 50000
    offset: int = 0


class ListaMateriaPrimaEntradaRequest(BaseModel):
    empresa: int = EMPRESA_PADRAO
    filial: Optional[int] = None
    fornecedor: Optional[int] = None
    nota_fiscal: Optional[int] = None
    periodo_entrada: Optional[str] = None
    data_inicial: Optional[str] = None
    data_final: Optional[str] = None
    origens: Optional[list[str]] = None
    familia: Optional[str] = None
    codigo_produto: Optional[str] = None
    descricao_produto: Optional[str] = None
    limit: int = 50000
    offset: int = 0


class ListaMateriaPrimaRequest(BaseModel):
    """Payload unificado: o front manda `visao` conforme a escolha do usuário
    e só os filtros da visão escolhida são considerados."""
    visao: str = "OBRA"  # OBRA (USU_T900LCM) | ENTRADA (NF/USU_TLOTCAB)
    empresa: int = EMPRESA_PADRAO
    # Filtros da visão OBRA
    obra: Optional[int] = None
    desenho: Optional[int] = None
    op: Optional[int] = None
    origem_op: Optional[str] = None
    codigo_componente: Optional[str] = None
    # Filtros da visão ENTRADA
    filial: Optional[int] = None
    fornecedor: Optional[int] = None
    nota_fiscal: Optional[int] = None
    periodo_entrada: Optional[str] = None
    origens: Optional[list[str]] = None
    familia: Optional[str] = None
    codigo_produto: Optional[str] = None
    # Comuns
    descricao_produto: Optional[str] = None
    data_inicial: Optional[str] = None
    data_final: Optional[str] = None
    limit: int = 50000
    offset: int = 0


# =============================================================================
# HELPERS
# =============================================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def to_iso_date(value: Any) -> Optional[str]:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date().isoformat()

    if isinstance(value, date):
        return value.isoformat()

    value_s = clean_str(value)
    return value_s or None


def parse_date_input(value: Any) -> Optional[date]:
    value_s = clean_str(value)
    if not value_s:
        return None

    # Aceita YYYY-MM-DD
    try:
        return datetime.strptime(value_s, "%Y-%m-%d").date()
    except Exception:
        pass

    # Aceita DD/MM/YYYY
    try:
        return datetime.strptime(value_s, "%d/%m/%Y").date()
    except Exception:
        pass

    raise HTTPException(
        status_code=400,
        detail=f"Data inválida: {value_s}. Use YYYY-MM-DD ou DD/MM/YYYY."
    )


def format_date_br(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")

    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")

    value_s = clean_str(value)
    if not value_s:
        return ""

    try:
        return datetime.strptime(value_s[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return value_s


def parse_periodo_entrada(periodo: Any) -> tuple[Optional[date], Optional[date]]:
    periodo_s = clean_str(periodo)
    if not periodo_s:
        return None, None

    periodo_s = periodo_s.replace(" até ", "-").replace(" a ", "-")
    partes = [p.strip() for p in periodo_s.split("-") if p.strip()]

    if len(partes) != 2:
        raise HTTPException(
            status_code=400,
            detail="Período inválido. Use o formato DD/MM/YYYY-DD/MM/YYYY."
        )

    return parse_date_input(partes[0]), parse_date_input(partes[1])


def format_hora_senior(value: Any) -> str:
    value_s = clean_str(value)
    if not value_s:
        return ""

    try:
        n = int(float(value_s))
        return f"{n // 60:02d}:{n % 60:02d}"
    except Exception:
        return value_s


def lot_key_from_values(codemp: Any, codlot: Any) -> str:
    codemp_s = clean_str(codemp)
    codlot_s = clean_str(codlot)
    if USE_MULTIEMPRESA_LOT_KEY:
        return f"{codemp_s}-{codlot_s}"
    return codlot_s


def is_certificado_pendente(value: Any) -> bool:
    valor = clean_str(value).upper()
    return valor in ("", "PENDENTE", "CERTIFICADO PENDENTE")


def build_final_file_name(
    numnfc: Any,
    certificate_code: str,
    codlot: Any,
    pagina: int = 1,
) -> str:
    nf = clean_str(numnfc) or "SEMNF"
    cert = clean_str(certificate_code)
    cert = cert.replace("/", "-").replace("\\", "-").replace(" ", "-")
    lot = clean_str(codlot)
    return f"{nf}-{cert}-{lot}-{pagina:02d}.JPG"


def ensure_parent_dir(file_path: str) -> None:
    parent = os.path.dirname(file_path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def copy_document_to_final_path(source_path: str, final_path: str) -> None:
    ensure_parent_dir(final_path)
    shutil.copy2(source_path, final_path)


def convert_document_to_jpg_pages(
    source_path: str,
    lot: dict[str, Any],
    certificate_code: str,
    start_page: int = 1,
) -> list[dict[str, str]]:
    if not source_path or not os.path.exists(source_path):
        raise HTTPException(status_code=404, detail="Arquivo original não encontrado para conversão.")

    ext = os.path.splitext(source_path)[1].lower()
    result = []

    if ext == ".pdf":
        pdf = fitz.open(source_path)

        for page_index in range(len(pdf)):
            pagina = start_page + page_index
            final_file_name = build_final_file_name(
                lot.get("erp_numnfc"),
                certificate_code,
                lot.get("erp_codlot"),
                pagina=pagina,
            )

            final_path = os.path.join(FINAL_SERVER_BASE_PATH, final_file_name)
            ensure_parent_dir(final_path)

            page = pdf[page_index]
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            pix.save(final_path)

            result.append({
                "page": pagina,
                "file_name": final_file_name,
                "server_path": final_path,
            })

        pdf.close()
        return result

    if ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"]:
        final_file_name = build_final_file_name(
            lot.get("erp_numnfc"),
            certificate_code,
            lot.get("erp_codlot"),
            pagina=start_page,
        )

        final_path = os.path.join(FINAL_SERVER_BASE_PATH, final_file_name)
        ensure_parent_dir(final_path)

        image = Image.open(source_path).convert("RGB")
        image.save(final_path, "JPEG", quality=95)

        return [{
            "page": start_page,
            "file_name": final_file_name,
            "server_path": final_path,
        }]

    raise HTTPException(status_code=400, detail=f"Formato de arquivo não suportado para conversão: {ext}")


def erp_camdoc_from_file_name(file_name: str) -> str:
    return os.path.splitext(os.path.basename(file_name))[0]


def safe_camdoc(value: Any) -> str:
    camdoc = clean_str(value)

    # Remove extensão se vier com .JPG/.JPEG.
    camdoc = os.path.splitext(camdoc)[0]

    # Evita path traversal ou tentativa de buscar fora da pasta padrão.
    camdoc = os.path.basename(camdoc)
    camdoc = camdoc.replace("/", "").replace("\\", "")

    if not camdoc:
        raise HTTPException(status_code=400, detail="Nome do arquivo/camdoc inválido.")

    if ".." in camdoc:
        raise HTTPException(status_code=400, detail="Nome do arquivo/camdoc inválido.")

    return camdoc


def resolve_certifq_jpg_path(camdoc: Any, must_exist: bool = True) -> str:
    camdoc_safe = safe_camdoc(camdoc)
    file_name = f"{camdoc_safe}.JPG"
    file_path = os.path.join(FINAL_SERVER_BASE_PATH, file_name)

    if must_exist and not os.path.exists(file_path):
        raise HTTPException(
            status_code=404,
            detail=f"Arquivo não encontrado em {file_path}"
        )

    return file_path


def lovable_headers() -> dict[str, str]:
    if not LOVABLE_DB_URL or not LOVABLE_DB_KEY:
        raise HTTPException(
            status_code=500,
            detail="LOVABLE_DB_URL/LOVABLE_DB_KEY não configurados."
        )

    headers = {
        "apikey": LOVABLE_DB_KEY,
        "Content-Type": "application/json",
    }

    # Chaves novas do Supabase, como sb_secret_ e sb_publishable_,
    # não são JWT e não devem ser enviadas como Authorization Bearer.
    # Elas devem ser usadas no header apikey.
    if not (
        LOVABLE_DB_KEY.startswith("sb_secret_")
        or LOVABLE_DB_KEY.startswith("sb_publishable_")
    ):
        headers["Authorization"] = f"Bearer {LOVABLE_DB_KEY}"

    return headers


# =============================================================================
# AUTH
# =============================================================================

def create_token(usuario: str, nome: str, role: str) -> str:
    payload = {
        "sub": usuario,
        "name": nome,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=8),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict[str, Any]:
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido")


def require_admin(user: dict[str, Any]) -> None:
    if clean_str(user.get("role")).lower() != "admin":
        raise HTTPException(status_code=403, detail="Apenas administradores podem executar esta ação")


@app.post("/login")
def login(payload: LoginRequest):
    usuario = clean_str(payload.usuario).upper()
    senha = clean_str(payload.senha)

    user_data = USERS.get(usuario)
    if not user_data or user_data["senha"] != senha:
        raise HTTPException(status_code=401, detail="Login inválido")

    token = create_token(usuario, user_data["nome"], user_data["role"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "usuario": usuario,
        "nome": user_data["nome"],
        "role": user_data["role"],
    }


# =============================================================================
# SQL SERVER
# =============================================================================

def get_connection():
    if not SQL_SERVER or not SQL_DATABASE or not SQL_USER or not SQL_PASSWORD:
        raise HTTPException(status_code=500, detail="Credenciais do SQL Server não configuradas.")

    try:
        conn = pyodbc.connect(
            (
                f"DRIVER={{{SQL_DRIVER}}};"
                f"SERVER={SQL_SERVER};"
                f"DATABASE={SQL_DATABASE};"
                f"UID={SQL_USER};"
                f"PWD={SQL_PASSWORD};"
                "Encrypt=no;"
                "TrustServerCertificate=yes;"
            ),
            timeout=30,
        )
        return conn
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao conectar no SQL Server: {str(e)}")


def fetch_rows_dict(cursor) -> list[dict[str, Any]]:
    cols = [c[0] for c in cursor.description]
    out = []
    for row in cursor.fetchall():
        item = {}
        for i, c in enumerate(cols):
            val = row[i]
            if isinstance(val, str):
                val = val.strip()
            item[c] = val
        out.append(item)
    return out


# =============================================================================
# LOVABLE DB REST
# =============================================================================

# Sessão HTTP compartilhada com pool de conexões (keep-alive). Reusa sockets
# em vez de abrir um novo por chamada — reduz pressão de file descriptors no
# Windows quando o front-end dispara muitas requisições simultâneas.
_http_session = requests.Session()
_http_session.mount(
    "https://",
    requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=0),
)
_http_session.mount(
    "http://",
    requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=0),
)


def lovable_rest_url(table_name: str) -> str:
    return f"{LOVABLE_DB_URL}/rest/v1/{table_name}"


# O PostgREST/Supabase limita cada resposta a ~1000 linhas (max-rows). Para
# trazer mais que isso, paginamos via offset.
LOVABLE_PAGE_SIZE = 1000


def _lovable_get_page(table_name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        resp = _http_session.get(
            lovable_rest_url(table_name),
            headers=lovable_headers(),
            params=params,
            timeout=60,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha de conexão com Lovable/Supabase em SELECT {table_name}: {str(e)}")

    if resp.status_code >= 300:
        raise HTTPException(
            status_code=500,
            detail=f"Erro lendo {table_name} | status={resp.status_code} | body={resp.text}"
        )
    return resp.json()


def lovable_select(
    table_name: str,
    filters: Optional[dict[str, Any]] = None,
    select_cols: str = "*",
    limit: Optional[int] = None,
    order: Optional[str] = None,
) -> list[dict[str, Any]]:
    base_params: dict[str, Any] = {"select": select_cols}
    if filters:
        for key, value in filters.items():
            if value is None:
                continue
            base_params[key] = f"eq.{value}"
    if order:
        base_params["order"] = order

    # Caso simples: limite pequeno (<= 1 página) ou sem limite -> 1 request.
    if not limit or limit <= LOVABLE_PAGE_SIZE:
        params = dict(base_params)
        if limit:
            params["limit"] = str(limit)
        return _lovable_get_page(table_name, params)

    # Limite grande -> pagina via offset até atingir o alvo ou acabar.
    all_rows: list[dict[str, Any]] = []
    offset = 0
    while len(all_rows) < limit:
        page_size = min(LOVABLE_PAGE_SIZE, limit - len(all_rows))
        params = dict(base_params)
        params["limit"] = str(page_size)
        params["offset"] = str(offset)

        batch = _lovable_get_page(table_name, params)
        all_rows.extend(batch)

        if len(batch) < page_size:
            break  # última página
        offset += len(batch)

    return all_rows


def lovable_insert(table_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    headers = lovable_headers()
    headers["Prefer"] = "return=representation"
    resp = _http_session.post(
        lovable_rest_url(table_name),
        headers=headers,
        data=json.dumps(payload),
        timeout=60,
    )
    if resp.status_code >= 300:
        raise HTTPException(status_code=500, detail=f"Erro inserindo em {table_name}: {resp.text}")
    data = resp.json()
    return data[0] if isinstance(data, list) and data else {}


def lovable_patch(table_name: str, filters: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    headers = lovable_headers()
    headers["Prefer"] = "return=representation"
    params = {k: f"eq.{v}" for k, v in filters.items()}

    resp = _http_session.patch(
        lovable_rest_url(table_name),
        headers=headers,
        params=params,
        data=json.dumps(payload),
        timeout=60,
    )
    if resp.status_code >= 300:
        raise HTTPException(status_code=500, detail=f"Erro atualizando {table_name}: {resp.text}")
    return resp.json()


def lovable_upsert_many(table_name: str, payload: list[dict[str, Any]], on_conflict: str) -> list[dict[str, Any]]:
    if not payload:
        return []

    headers = lovable_headers()
    headers["Prefer"] = "resolution=merge-duplicates,return=representation"

    # Envia em lotes para não estourar um único POST gigante (ex.: sync de 10k+ lotes).
    CHUNK = 500
    saved: list[dict[str, Any]] = []

    for i in range(0, len(payload), CHUNK):
        lote = payload[i:i + CHUNK]
        try:
            resp = _http_session.post(
                f"{lovable_rest_url(table_name)}?on_conflict={on_conflict}",
                headers=headers,
                data=json.dumps(lote),
                timeout=120,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Falha de conexão com Lovable/Supabase em UPSERT {table_name}: {str(e)}")

        if resp.status_code >= 300:
            raise HTTPException(
                status_code=500,
                detail=f"Erro upsert em {table_name} | status={resp.status_code} | body={resp.text}"
            )

        data = resp.json()
        if isinstance(data, list):
            saved.extend(data)

    return saved


def lovable_find_one(table_name: str, filters: dict[str, Any]) -> Optional[dict[str, Any]]:
    rows = lovable_select(table_name, filters=filters, limit=1)
    return rows[0] if rows else None


# =============================================================================
# HEALTH
# =============================================================================

@app.get("/health")
def health():
    db_status = "ok"
    lovable_status = "ok"
    db_error = None
    lovable_error = None

    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        conn.close()
    except Exception as e:
        db_status = "erro"
        db_error = str(e)

    rows = []
    try:
        rows = lovable_select(TABLE_LOTS, limit=1)
    except Exception as e:
        lovable_status = "erro"
        lovable_error = str(e)

    return {
        "status": "ok",
        "erp_sql_server": db_status,
        "erp_sql_server_error": db_error,
        "lovable_db": lovable_status,
        "lovable_db_error": lovable_error,
        "lovable_rows_test": len(rows),
        "sql_driver": SQL_DRIVER,
        "empresa_padrao": EMPRESA_PADRAO,
        "upload_dir": UPLOAD_DIR,
        "upload_dir_exists": os.path.isdir(UPLOAD_DIR),
        "final_server_base_path": FINAL_SERVER_BASE_PATH,
        "final_server_base_path_exists": os.path.isdir(FINAL_SERVER_BASE_PATH),
    }


# =============================================================================
# ERP LOTES -> LOVABLE
# =============================================================================

def query_erp_lotes_exicer(
    codemp: int = 1,
    codfil: Optional[int] = None,
    codfor: Optional[int] = None,
    numnfc: Optional[int] = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    sql = f"""
    SELECT TOP {limit}
        CAB.USU_CODEMP                           AS ERP_CODEMP,
        CAB.USU_CODLOT                           AS ERP_CODLOT,
        CAB.USU_CODFIL                           AS ERP_CODFIL,
        CAB.USU_CODFOR                           AS ERP_CODFOR,
        CAB.USU_NUMNFC                           AS ERP_NUMNFC,
        CAB.USU_CODSNF                           AS ERP_CODSNF,
        NFC.DATENT                               AS ERP_DATENT,
        CAB.USU_CODCER                           AS ERP_CODCER,
        ANE.USU_CODCER                           AS ERP_ANE_CODCER,
        ANE.USU_CAMDOC                           AS ERP_ANE_CAMDOC,
        (SELECT COUNT(DISTINCT AC.USU_CODCER)
           FROM USU_TLOTANE AC
          WHERE AC.USU_CODEMP = CAB.USU_CODEMP
            AND AC.USU_CODLOT = CAB.USU_CODLOT
            AND ISNULL(LTRIM(RTRIM(AC.USU_CODCER)), '') <> '') AS ERP_CERT_COUNT,
        (SELECT COUNT(*)
           FROM USU_TLOTANE AP
          WHERE AP.USU_CODEMP = CAB.USU_CODEMP
            AND AP.USU_CODLOT = CAB.USU_CODLOT) AS ERP_PAGES_COUNT,
        ITE.USU_SEQIPC                           AS ERP_SEQIPC,

        COALESCE(IPC.CODPRO, '')                 AS ERP_CODPRO,
        COALESCE(IPC.CODDER, '')                 AS ERP_CODDER,
        COALESCE(PRO.DESPRO, '')                 AS PRODUCT_DESCRIPTION,
        COALESCE(IPC.CPLIPC, '')                 AS INVOICE_ITEM_DESCRIPTION,
        COALESCE(PRO.CODFAM, '')                 AS ERP_CODFAM,
        COALESCE(FAM.DESFAM, '')                 AS FAMILY_DESCRIPTION,
        COALESCE(PRO.CODORI, '')                 AS ERP_CODORI,
        COALESCE(PRO.USU_EXICER, '')             AS ERP_PRO_EXICER,

        FORN.NOMFOR                              AS SUPPLIER_NAME,
        OCP.NUMOCP                               AS PURCHASE_ORDER,
        OCP.USU_EXICER                           AS ERP_EXICER,

        CASE
            WHEN ISNULL(PRO.USU_EXICER, 'N') = 'S' THEN 'PRODUTO_EXIGE_CERTIFICADO'
            WHEN ISNULL(OCP.USU_EXICER, 'N') = 'S' THEN 'OC_EXIGE_CERTIFICADO'
            WHEN PRO.CODFAM = 'TINTAS' THEN 'FAMILIA_TINTAS'
            WHEN CAB.USU_CODCER IS NULL
              OR LTRIM(RTRIM(CAB.USU_CODCER)) = ''
              OR UPPER(LTRIM(RTRIM(CAB.USU_CODCER))) IN ('PENDENTE', 'CERTIFICADO PENDENTE')
            THEN 'CERTIFICADO_PENDENTE_ERP'
            ELSE 'OUTRO'
        END                                      AS ERP_SYNC_REASON

    FROM USU_TLOTCAB CAB
    INNER JOIN USU_TLOTITE ITE
        ON ITE.USU_CODEMP = CAB.USU_CODEMP
       AND ITE.USU_CODLOT = CAB.USU_CODLOT

    LEFT JOIN E440NFC NFC
        ON NFC.CODEMP = CAB.USU_CODEMP
       AND NFC.CODFIL = CAB.USU_CODFIL
       AND NFC.CODFOR = CAB.USU_CODFOR
       AND NFC.NUMNFC = CAB.USU_NUMNFC
       AND NFC.CODSNF = CAB.USU_CODSNF

    LEFT JOIN E440IPC IPC
        ON IPC.CODEMP = ITE.USU_CODEMP
       AND IPC.CODFIL = ITE.USU_CODFIL
       AND IPC.CODFOR = ITE.USU_CODFOR
       AND IPC.NUMNFC = ITE.USU_NUMNFC
       AND IPC.CODSNF = ITE.USU_CODSNF
       AND IPC.SEQIPC = ITE.USU_SEQIPC

    LEFT JOIN E420OCP OCP
        ON OCP.CODEMP = IPC.CODEMP
       AND OCP.CODFIL = IPC.CODFIL
       AND OCP.NUMOCP = IPC.NUMOCP

    LEFT JOIN E075PRO PRO
        ON PRO.CODEMP = IPC.CODEMP
       AND PRO.CODPRO = IPC.CODPRO

    LEFT JOIN E012FAM FAM
        ON FAM.CODEMP = PRO.CODEMP
       AND FAM.CODFAM = PRO.CODFAM

    LEFT JOIN E095FOR FORN
        ON FORN.CODFOR = CAB.USU_CODFOR

    OUTER APPLY (
        SELECT TOP 1
               A.USU_CODCER,
               A.USU_CAMDOC
          FROM USU_TLOTANE A
         WHERE A.USU_CODEMP = CAB.USU_CODEMP
           AND A.USU_CODLOT = CAB.USU_CODLOT
           AND ISNULL(LTRIM(RTRIM(A.USU_CODCER)), '') <> ''
         ORDER BY A.USU_SEQANE
    ) ANE

    WHERE CAB.USU_CODEMP = ?
      AND (? IS NULL OR CAB.USU_CODFIL = ?)
      AND (? IS NULL OR CAB.USU_CODFOR = ?)
      AND (? IS NULL OR CAB.USU_NUMNFC = ?)
      AND (
            ISNULL(PRO.USU_EXICER, 'N') = 'S'
         OR ISNULL(OCP.USU_EXICER, 'N') = 'S'
         OR PRO.CODFAM = 'TINTAS'
         OR CAB.USU_CODCER IS NULL
         OR LTRIM(RTRIM(CAB.USU_CODCER)) = ''
         OR UPPER(LTRIM(RTRIM(CAB.USU_CODCER))) = 'PENDENTE'
         OR UPPER(LTRIM(RTRIM(CAB.USU_CODCER))) = 'CERTIFICADO PENDENTE'
      )

    ORDER BY CAB.USU_CODLOT DESC, ITE.USU_SEQIPC ASC
    """

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, [codemp, codfil, codfil, codfor, codfor, numnfc, numnfc])
        return fetch_rows_dict(cur)
    finally:
        conn.close()


def build_lots_payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}

    for row in rows:
        lot_key = lot_key_from_values(row["ERP_CODEMP"], row["ERP_CODLOT"])

        if lot_key not in grouped:
            codcer_cab = clean_str(row.get("ERP_CODCER"))
            codcer_ane = clean_str(row.get("ERP_ANE_CODCER"))

            if is_certificado_pendente(codcer_cab) and codcer_ane:
                codcer = codcer_ane
            else:
                codcer = codcer_cab

            pendente = is_certificado_pendente(codcer)

            grouped[lot_key] = {
                "lot_key": lot_key,
                "lot_number": clean_str(row["ERP_CODLOT"]),
                "erp_codemp": row["ERP_CODEMP"],
                "erp_codlot": row["ERP_CODLOT"],
                "erp_codfil": row["ERP_CODFIL"],
                "erp_codfor": row["ERP_CODFOR"],
                "supplier_name": clean_str(row["SUPPLIER_NAME"]),
                "erp_numnfc": row["ERP_NUMNFC"],
                "erp_codsnf": clean_str(row["ERP_CODSNF"]),
                "erp_datent": to_iso_date(row.get("ERP_DATENT")),
                "invoice_entry_date": to_iso_date(row.get("ERP_DATENT")),
                "purchase_order": clean_str(row["PURCHASE_ORDER"]),
                "erp_codpro": clean_str(row.get("ERP_CODPRO")),
                "product_code": clean_str(row.get("ERP_CODPRO")),
                "erp_codder": clean_str(row.get("ERP_CODDER")),
                "derivation_code": clean_str(row.get("ERP_CODDER")),
                "product_description": clean_str(row.get("PRODUCT_DESCRIPTION")),
                "invoice_item_description": clean_str(row.get("INVOICE_ITEM_DESCRIPTION")),
                "erp_codfam": clean_str(row.get("ERP_CODFAM")),
                "family_code": clean_str(row.get("ERP_CODFAM")),
                "family_description": clean_str(row.get("FAMILY_DESCRIPTION")),
                "erp_codori": clean_str(row.get("ERP_CODORI")),
                "origin_code": clean_str(row.get("ERP_CODORI")),
                "erp_pro_exicer": clean_str(row.get("ERP_PRO_EXICER")),
                "sync_reason": clean_str(row.get("ERP_SYNC_REASON")),
                "requires_certificate": True,
                "erp_codcer": "" if pendente else codcer,
                "certificate_code": "" if pendente else codcer,
                "erp_anexo_codcer": codcer_ane,
                "erp_camdoc": clean_str(row.get("ERP_ANE_CAMDOC")),
                "certificate_count": int(row.get("ERP_CERT_COUNT") or 0),
                "certificate_pages_count": int(row.get("ERP_PAGES_COUNT") or 0),
                "status_app": "PENDENTE" if pendente else "APROVADO",
                "status_erp": "PENDENTE" if pendente else "APROVADO",
                "synced_from_erp_at": now_iso(),
                "updated_at": now_iso(),
            }

    return list(grouped.values())


# Status_app que representam progresso no app e NÃO devem ser revertidos para
# PENDENTE pela sincronização quando o ERP ainda estiver pendente.
STATUS_APP_PROGRESSO = {
    "EM_ANALISE", "EM_REVISAO", "EXPORTADO", "APROVADO",
    "REPROVADO", "DESVIO_JUSTIFICADO", "ANALYZED", "APPROVED",
    "APPROVED_WITH_DEVIATION", "REJECTED", "UPLOADED",
}


def _fetch_existing_lots_map(lot_keys: list[str]) -> dict[str, dict[str, Any]]:
    """Lê do Supabase os campos de certificado/status dos lotes informados."""
    cols = (
        "lot_key,certificate_code,erp_codcer,erp_anexo_codcer,erp_camdoc,"
        "certificate_count,certificate_pages_count,status_app"
    )
    out: dict[str, dict[str, Any]] = {}
    CHUNK = 150
    for i in range(0, len(lot_keys), CHUNK):
        chunk = [k for k in lot_keys[i:i + CHUNK] if k]
        if not chunk:
            continue
        in_list = ",".join('"' + str(k).replace('"', '') + '"' for k in chunk)
        params = {"select": cols, "lot_key": f"in.({in_list})", "limit": str(len(chunk))}
        for r in _lovable_get_page(TABLE_LOTS, params):
            out[clean_str(r.get("lot_key"))] = r
    return out


def _preservar_aprovacao_local(lot_payload: dict[str, Any], existente: dict[str, Any]) -> None:
    """Quando o ERP vem pendente, não apaga o certificado/status que o app já
    gravou no Supabase. status_erp segue refletindo o ERP; status_app não regride."""
    if clean_str(existente.get("certificate_code")):
        lot_payload["certificate_code"] = clean_str(existente.get("certificate_code"))
    if clean_str(existente.get("erp_codcer")):
        lot_payload["erp_codcer"] = clean_str(existente.get("erp_codcer"))
    if clean_str(existente.get("erp_anexo_codcer")):
        lot_payload["erp_anexo_codcer"] = clean_str(existente.get("erp_anexo_codcer"))
    if clean_str(existente.get("erp_camdoc")):
        lot_payload["erp_camdoc"] = clean_str(existente.get("erp_camdoc"))
    if int(existente.get("certificate_count") or 0) > 0:
        lot_payload["certificate_count"] = existente.get("certificate_count")
    if int(existente.get("certificate_pages_count") or 0) > 0:
        lot_payload["certificate_pages_count"] = existente.get("certificate_pages_count")

    status_local = clean_str(existente.get("status_app")).upper()
    if status_local and status_local != "PENDENTE":
        lot_payload["status_app"] = existente.get("status_app")


def run_lotes_sync(
    codemp: int = EMPRESA_PADRAO,
    codfil: Optional[int] = None,
    codfor: Optional[int] = None,
    numnfc: Optional[int] = None,
    limit: int = 500,
    origem: str = "MANUAL",
) -> dict[str, Any]:
    started_at = now_iso()

    erp_rows = query_erp_lotes_exicer(
        codemp=codemp,
        codfil=codfil,
        codfor=codfor,
        numnfc=numnfc,
        limit=limit,
    )

    payload = build_lots_payload(erp_rows)

    # Proteção contra perda de dados: para lotes que o ERP traz PENDENTES, não
    # sobrescrever o certificado/status que o app já gravou no Supabase.
    pendentes = [p for p in payload if clean_str(p.get("status_erp")).upper() == "PENDENTE"]
    preservados = 0
    if pendentes:
        existentes = _fetch_existing_lots_map([p["lot_key"] for p in pendentes])
        for p in pendentes:
            ex = existentes.get(clean_str(p.get("lot_key")))
            if ex:
                _preservar_aprovacao_local(p, ex)
                if clean_str(ex.get("certificate_code")) or clean_str(ex.get("status_app")).upper() not in ("", "PENDENTE"):
                    preservados += 1

    saved = lovable_upsert_many(TABLE_LOTS, payload, on_conflict="lot_key")

    return {
        "ok": True,
        "origem": origem,
        "started_at": started_at,
        "finished_at": now_iso(),
        "lotes_lidos_erp": len(erp_rows),
        "lotes_upsert_lovable": len(payload),
        "lotes_pendentes_preservados": preservados,
        "saved_preview": saved[:3] if isinstance(saved, list) else saved,
    }


@app.post("/api/erp/lotes/sync")
def sync_lotes_from_erp(
    codemp: int = Query(default=EMPRESA_PADRAO),
    codfil: Optional[int] = Query(default=None),
    codfor: Optional[int] = Query(default=None),
    numnfc: Optional[int] = Query(default=None),
    limit: int = Query(default=30000, ge=1, le=50000),
    user=Depends(get_current_user),
):
    return run_lotes_sync(
        codemp=codemp,
        codfil=codfil,
        codfor=codfor,
        numnfc=numnfc,
        limit=limit,
        origem=f"MANUAL:{user.get('sub')}",
    )


# =============================================================================
# AGENDAMENTO (APScheduler)
# =============================================================================

scheduler = BackgroundScheduler(timezone=SYNC_TIMEZONE)


def scheduled_lotes_sync():
    try:
        result = run_lotes_sync(
            codemp=EMPRESA_PADRAO,
            limit=30000,
            origem="AGENDADO_20H",
        )
        print(f"[SYNC AGENDADO] OK: {result}")
    except Exception as e:
        print(f"[SYNC AGENDADO] ERRO: {str(e)}")


@app.on_event("startup")
def start_scheduler():
    if not SYNC_SCHEDULE_ENABLED:
        print("[SYNC AGENDADO] Desativado por configuração.")
        return

    scheduler.add_job(
        scheduled_lotes_sync,
        CronTrigger(
            hour=SYNC_HOUR,
            minute=SYNC_MINUTE,
            timezone=SYNC_TIMEZONE,
        ),
        id="sync_lotes_diario_20h",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    scheduler.start()

    print(
        f"[SYNC AGENDADO] Ativo. Rodará todos os dias às "
        f"{SYNC_HOUR:02d}:{SYNC_MINUTE:02d} ({SYNC_TIMEZONE})."
    )


@app.on_event("shutdown")
def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()


@app.get("/api/erp/lotes/sync/status")
def get_sync_status(user=Depends(get_current_user)):
    jobs = []

    if scheduler.running:
        for job in scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            })

    return {
        "enabled": SYNC_SCHEDULE_ENABLED,
        "hour": SYNC_HOUR,
        "minute": SYNC_MINUTE,
        "timezone": SYNC_TIMEZONE,
        "scheduler_running": scheduler.running,
        "jobs": jobs,
    }


def _filtrar_lotes_por_q(rows: list[dict[str, Any]], q: Optional[str]) -> list[dict[str, Any]]:
    """Busca livre nos lotes. Normaliza dígitos para que '650.066' ache '650066'."""
    q_s = clean_str(q)
    if not q_s:
        return rows

    q_lower = q_s.lower()
    q_digits = re.sub(r"\D", "", q_s)

    filtered = []
    for row in rows:
        haystack = " ".join([
            clean_str(row.get("lot_key")),
            clean_str(row.get("lot_number")),
            clean_str(row.get("erp_codlot")),
            clean_str(row.get("erp_codfor")),
            clean_str(row.get("supplier_name")),
            clean_str(row.get("erp_numnfc")),
            clean_str(row.get("erp_codsnf")),
            clean_str(row.get("erp_codcer")),
            clean_str(row.get("certificate_code")),
            clean_str(row.get("erp_codpro")),
            clean_str(row.get("product_code")),
            clean_str(row.get("erp_codder")),
            clean_str(row.get("derivation_code")),
            clean_str(row.get("product_description")),
            clean_str(row.get("invoice_item_description")),
            clean_str(row.get("erp_codfam")),
            clean_str(row.get("family_code")),
            clean_str(row.get("family_description")),
            clean_str(row.get("erp_codori")),
            clean_str(row.get("origin_code")),
            clean_str(row.get("purchase_order")),
            clean_str(row.get("sync_reason")),
            clean_str(row.get("status_app")),
            clean_str(row.get("status_erp")),
        ]).lower()

        haystack_digits = re.sub(r"\D", "", haystack)

        if q_lower in haystack or (q_digits and q_digits in haystack_digits):
            filtered.append(row)

    return filtered


@app.get("/api/erp/lotes")
def get_lotes(
    status_app: Optional[str] = Query(default=None),
    status_erp: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=50000, ge=1, le=50000),
    user=Depends(get_current_user),
):
    filters = {}
    if status_app:
        filters["status_app"] = status_app
    if status_erp:
        filters["status_erp"] = status_erp

    rows = lovable_select(TABLE_LOTS, filters=filters, order="updated_at.desc", limit=limit)
    rows = _filtrar_lotes_por_q(rows, q)
    return {"total": len(rows), "dados": rows}


@app.get("/api/erp/certificados-qualidade")
def get_certificados_qualidade(
    status_app: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=50000, ge=1, le=50000),
    user=Depends(get_current_user),
):
    return get_lotes(status_app=status_app, status_erp=None, q=q, limit=limit, user=user)


@app.get("/api/erp/lotes/pendentes")
def get_lotes_pendentes(
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=50000, ge=1, le=50000),
    user=Depends(get_current_user),
):
    return get_lotes(status_app="PENDENTE", status_erp=None, q=q, limit=limit, user=user)


@app.get("/api/erp/lotes/{lot_key}")
def get_lote_detail(lot_key: str, user=Depends(get_current_user)):
    lot = lovable_find_one(TABLE_LOTS, {"lot_key": lot_key})
    if not lot:
        raise HTTPException(status_code=404, detail="Lote não encontrado")

    documents = lovable_select(TABLE_DOCUMENTS, filters={"lot_key": lot_key}, order="uploaded_at.desc")
    for d in documents:
        ai = lovable_select(TABLE_CERTIFICATES_AI, filters={"document_id": d.get("id")}, limit=1)
        d["ai"] = ai[0] if ai else None

    return {
        "lote": lot,
        "documentos": documents,
    }


# =============================================================================
# DOCUMENTOS / UPLOAD
# =============================================================================

@app.get("/api/erp/lotes/{lot_key}/documentos")
def get_lote_documents(lot_key: str, user=Depends(get_current_user)):
    docs = lovable_select(TABLE_DOCUMENTS, filters={"lot_key": lot_key}, order="uploaded_at.desc")
    return {"total": len(docs), "dados": docs}


@app.get("/api/erp/lotes/{lot_key}/anexos-erp")
def get_lote_anexos_erp(lot_key: str, user=Depends(get_current_user)):
    lot = lovable_find_one(TABLE_LOTS, {"lot_key": lot_key})
    if not lot:
        raise HTTPException(status_code=404, detail="Lote não encontrado")

    codemp = int(lot["erp_codemp"])
    codlot = int(lot["erp_codlot"])

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                USU_SEQANE AS seqane,
                USU_CODCER AS certificate_code,
                USU_CAMDOC AS camdoc
            FROM USU_TLOTANE
            WHERE USU_CODEMP = ?
              AND USU_CODLOT = ?
            ORDER BY USU_CODCER, USU_SEQANE
            """,
            [codemp, codlot],
        )

        rows = fetch_rows_dict(cur)

        dados = []
        for row in rows:
            camdoc = safe_camdoc(row.get("camdoc"))
            cert = clean_str(row.get("certificate_code"))
            file_path = resolve_certifq_jpg_path(camdoc, must_exist=False)

            dados.append({
                "seqane": row.get("seqane"),
                "certificate_code": cert,
                "camdoc": camdoc,
                "file_name": f"{camdoc}.JPG",
                "server_path": file_path,
                "exists": os.path.exists(file_path),
                "arquivo_url": f"/api/erp/certificados/arquivo/{quote(camdoc)}",
            })

        # Agrupamento por certificado para facilitar o Lovable
        grupos: dict[str, dict[str, Any]] = {}
        for item in dados:
            cert = item["certificate_code"] or "SEM_CERTIFICADO"
            if cert not in grupos:
                grupos[cert] = {
                    "certificate_code": cert,
                    "pages_count": 0,
                    "pages": [],
                }

            grupos[cert]["pages_count"] += 1
            grupos[cert]["pages"].append(item)

        return {
            "total": len(dados),
            "certificates_count": len(grupos),
            "dados": dados,
            "certificados": list(grupos.values()),
        }

    finally:
        conn.close()


@app.post("/api/erp/lotes/{lot_key}/documentos/upload")
def upload_document(
    lot_key: str,
    file: UploadFile = File(...),
    uploaded_by: Optional[str] = Query(default=None),
    user=Depends(get_current_user),
):
    lot = lovable_find_one(TABLE_LOTS, {"lot_key": lot_key})
    if not lot:
        raise HTTPException(status_code=404, detail="Lote não encontrado")

    ext = os.path.splitext(file.filename or "")[1] or ".bin"
    safe_name = f"{uuid.uuid4().hex}{ext}"
    disk_path = os.path.join(UPLOAD_DIR, safe_name)

    with open(disk_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    file_size = os.path.getsize(disk_path)

    doc_payload = {
        "lot_key": lot_key,
        "lot_id": lot.get("id"),
        "erp_seqane": None,
        "original_file_name": file.filename,
        "file_name": file.filename,
        "storage_path": disk_path,
        "public_url": None,
        "mime_type": file.content_type,
        "file_size": file_size,
        "page_count": None,
        "certificate_code": None,
        "document_status": "UPLOADED",
        "final_server_path": None,
        "final_file_name": None,
        "upload_source": "API",
        "uploaded_by": uploaded_by or user.get("sub"),
        "uploaded_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    created_doc = lovable_insert(TABLE_DOCUMENTS, doc_payload)

    lovable_insert(TABLE_ANALYSIS_JOBS, {
        "document_id": created_doc.get("id"),
        "lot_key": lot_key,
        "job_type": "CERTIFICATE_EXTRACTION",
        "job_status": "QUEUED",
        "attempts": 0,
        "error_message": None,
        "started_at": None,
        "finished_at": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    })

    lovable_patch(TABLE_LOTS, {"lot_key": lot_key}, {
        "status_app": "EM_ANALISE",
        "updated_at": now_iso(),
    })

    return {
        "ok": True,
        "documento": created_doc,
    }


@app.post("/api/erp/certificados/upload")
def upload_document_alias(
    lot_key: str = Query(...),
    file: UploadFile = File(...),
    uploaded_by: Optional[str] = Query(default=None),
    user=Depends(get_current_user),
):
    # Alias para compatibilidade com o front-end/Lovable.
    return upload_document(lot_key=lot_key, file=file, uploaded_by=uploaded_by, user=user)


IMPORT_EXTS_PERMITIDAS = {".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
IMPORT_MAX_BYTES = 60 * 1024 * 1024  # 60 MB
_MIME_POR_EXT = {
    ".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".bmp": "image/bmp", ".webp": "image/webp",
    ".tif": "image/tiff", ".tiff": "image/tiff",
}


def _validar_source_url(source_url: str) -> None:
    """Bloqueia SSRF: só permite https para o host do Supabase do projeto."""
    parsed = urlparse(source_url)
    if parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="source_url deve ser https.")

    host = (parsed.hostname or "").lower()
    lov_host = (urlparse(LOVABLE_DB_URL).hostname or "").lower()
    if not (host.endswith(".supabase.co") or (lov_host and host == lov_host)):
        raise HTTPException(
            status_code=400,
            detail=f"Host não permitido para importação: {host or '(vazio)'}. "
                   "Use uma URL do Supabase Storage do projeto."
        )


@app.post("/api/erp/lotes/{lot_key}/documentos/importar")
def importar_documento(
    lot_key: str,
    payload: ImportDocumentRequest,
    user=Depends(get_current_user),
):
    lot = lovable_find_one(TABLE_LOTS, {"lot_key": lot_key})
    if not lot:
        raise HTTPException(status_code=404, detail="Lote não encontrado")

    source_url = clean_str(payload.source_url)
    if not source_url:
        raise HTTPException(status_code=400, detail="source_url é obrigatório.")
    _validar_source_url(source_url)

    original_name = clean_str(payload.original_file_name) or "documento.pdf"
    ext = os.path.splitext(original_name)[1].lower() or ".pdf"
    if ext not in IMPORT_EXTS_PERMITIDAS:
        raise HTTPException(status_code=400, detail=f"Extensão não suportada: {ext}")

    safe_name = f"{uuid.uuid4().hex}{ext}"
    disk_path = os.path.join(UPLOAD_DIR, safe_name)

    # Baixa o arquivo do Supabase Storage para o UPLOAD_DIR (com limite de tamanho).
    try:
        with _http_session.get(source_url, stream=True, timeout=120) as resp:
            if resp.status_code >= 300:
                raise HTTPException(
                    status_code=502,
                    detail=f"Falha ao baixar o arquivo (status {resp.status_code})."
                )
            total = 0
            with open(disk_path, "wb") as out:
                for chunk in resp.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > IMPORT_MAX_BYTES:
                        out.close()
                        if os.path.exists(disk_path):
                            os.remove(disk_path)
                        raise HTTPException(status_code=400, detail="Arquivo excede o limite de 60 MB.")
                    out.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        if os.path.exists(disk_path):
            os.remove(disk_path)
        raise HTTPException(status_code=502, detail=f"Erro ao baixar o arquivo: {str(e)}")

    file_size = os.path.getsize(disk_path)

    doc_payload = {
        "lot_key": lot_key,
        "lot_id": lot.get("id"),
        "erp_seqane": None,
        "original_file_name": original_name,
        "file_name": original_name,
        "storage_path": disk_path,
        "public_url": source_url,
        "mime_type": _MIME_POR_EXT.get(ext, "application/octet-stream"),
        "file_size": file_size,
        "page_count": None,
        "certificate_code": None,
        "document_status": "UPLOADED",
        "final_server_path": None,
        "final_file_name": None,
        "upload_source": "IMPORT",
        "uploaded_by": clean_str(payload.uploaded_by) or user.get("sub"),
        "uploaded_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    created_doc = lovable_insert(TABLE_DOCUMENTS, doc_payload)

    lovable_insert(TABLE_ANALYSIS_JOBS, {
        "document_id": created_doc.get("id"),
        "lot_key": lot_key,
        "job_type": "CERTIFICATE_EXTRACTION",
        "job_status": "QUEUED",
        "attempts": 0,
        "error_message": None,
        "started_at": None,
        "finished_at": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    })

    lovable_patch(TABLE_LOTS, {"lot_key": lot_key}, {
        "status_app": "EM_ANALISE",
        "updated_at": now_iso(),
    })

    return {
        "ok": True,
        "document_id": created_doc.get("id"),
        "file_size": file_size,
        "documento": created_doc,
    }


# =============================================================================
# ANÁLISE IA SIMPLES
# =============================================================================

def detect_certificate_code(text: str) -> Optional[str]:
    patterns = [
        r"\bCERT[-_/ ]?\d{2,}[A-Z0-9\-_/]*\b",
        r"\bCERTIFICADO[-_/ ]?\d{2,}[A-Z0-9\-_/]*\b",
    ]
    upper_text = text.upper()
    for p in patterns:
        m = re.search(p, upper_text)
        if m:
            return m.group(0).replace(" ", "-")
    return None


@app.post("/api/erp/documentos/{document_id}/analisar")
def analyze_document(
    document_id: str,
    payload: AnalyzeRequest = Body(default=AnalyzeRequest()),
    user=Depends(get_current_user),
):
    doc = lovable_find_one(TABLE_DOCUMENTS, {"id": document_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    lot = lovable_find_one(TABLE_LOTS, {"lot_key": doc["lot_key"]})
    if not lot:
        raise HTTPException(status_code=404, detail="Lote não encontrado")

    text_base = (payload.texto_extraido or "") + " " + clean_str(doc.get("original_file_name"))
    text_upper = text_base.upper()

    extracted_certificate_code = payload.forcar_codigo_certificado or detect_certificate_code(text_upper)
    extracted_lot = None
    extracted_nf = None
    code_found_by = "NAO_ENCONTRADO"
    confidence = 0.30

    if extracted_certificate_code:
        code_found_by = "DOCUMENTO"
        confidence = 0.95
    else:
        lot_number = clean_str(lot.get("lot_number"))
        if lot_number and lot_number in text_upper:
            extracted_lot = lot_number
            code_found_by = "LOTE_PRODUCAO"
            confidence = 0.75

        invoice = clean_str(lot.get("erp_numnfc"))
        if invoice and invoice in text_upper:
            extracted_nf = invoice
            if code_found_by == "NAO_ENCONTRADO":
                code_found_by = "NOTA_FISCAL"
                confidence = 0.60

    ai_payload = {
        "document_id": document_id,
        "lot_key": doc["lot_key"],
        "analysis_status": "REVIEW_REQUIRED" if code_found_by != "NAO_ENCONTRADO" else "ERROR",
        "confidence": confidence,
        "code_found_by": code_found_by,
        "extracted_certificate_code": extracted_certificate_code,
        "extracted_production_lot": extracted_lot,
        "extracted_invoice_number": extracted_nf,
        "supplier_detected": lot.get("supplier_name"),
        "product_detected": lot.get("product_description"),
        "ai_summary": f"Análise concluída. Estratégia: {code_found_by}",
        "ai_raw_json": {
            "source_text": text_base[:4000],
            "lot_key": doc["lot_key"],
            "document_id": document_id,
        },
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    existing_ai = lovable_find_one(TABLE_CERTIFICATES_AI, {"document_id": document_id})
    if existing_ai:
        lovable_patch(TABLE_CERTIFICATES_AI, {"document_id": document_id}, ai_payload)
    else:
        lovable_insert(TABLE_CERTIFICATES_AI, ai_payload)

    lovable_patch(TABLE_DOCUMENTS, {"id": document_id}, {
        "document_status": "ANALYZED" if code_found_by != "NAO_ENCONTRADO" else "REJECTED",
        "certificate_code": extracted_certificate_code,
        "updated_at": now_iso(),
    })

    lovable_patch(TABLE_ANALYSIS_JOBS, {"document_id": document_id}, {
        "job_status": "DONE" if code_found_by != "NAO_ENCONTRADO" else "ERROR",
        "attempts": 1,
        "started_at": now_iso(),
        "finished_at": now_iso(),
        "updated_at": now_iso(),
    })

    lovable_patch(TABLE_LOTS, {"lot_key": doc["lot_key"]}, {
        "status_app": "EM_REVISAO",
        "updated_at": now_iso(),
    })

    return {
        "ok": True,
        "document_id": document_id,
        "code_found_by": code_found_by,
        "extracted_certificate_code": extracted_certificate_code,
        "confidence": confidence,
    }


@app.post("/api/erp/certificados/analisar/{document_id}")
def analyze_document_alias(
    document_id: str,
    payload: AnalyzeRequest = Body(default=AnalyzeRequest()),
    user=Depends(get_current_user),
):
    # Alias para compatibilidade com o front-end/Lovable.
    return analyze_document(document_id=document_id, payload=payload, user=user)


# =============================================================================
# RETORNO AO ERP
# =============================================================================

def get_next_seqane(conn, codemp: int, codlot: int) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT ISNULL(MAX(USU_SEQANE), 0) + 1
        FROM USU_TLOTANE
        WHERE USU_CODEMP = ? AND USU_CODLOT = ?
        """,
        [codemp, codlot],
    )
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 1


def upsert_erp_certificate_pages(
    conn,
    lot: dict[str, Any],
    certificate_code: str,
    page_files: list[dict[str, str]],
) -> dict[str, Any]:
    codemp = int(lot["erp_codemp"])
    codlot = int(lot["erp_codlot"])

    cur = conn.cursor()

    try:
        cur.execute(
            """
            UPDATE USU_TLOTCAB
               SET USU_CODCER = ?
             WHERE USU_CODEMP = ?
               AND USU_CODLOT = ?
               AND (
                      USU_CODCER IS NULL
                   OR LTRIM(RTRIM(USU_CODCER)) = ''
                   OR UPPER(LTRIM(RTRIM(USU_CODCER))) = 'PENDENTE'
                   OR UPPER(LTRIM(RTRIM(USU_CODCER))) = 'CERTIFICADO PENDENTE'
               )
            """,
            [certificate_code, codemp, codlot],
        )

        cur.execute(
            """
            DELETE FROM USU_TLOTANE
             WHERE USU_CODEMP = ?
               AND USU_CODLOT = ?
               AND USU_CODCER = ?
            """,
            [codemp, codlot, certificate_code],
        )

        inserted = []

        for page_file in page_files:
            seqane = get_next_seqane(conn, codemp, codlot)
            camdoc = erp_camdoc_from_file_name(page_file["file_name"])

            cur.execute(
                """
                INSERT INTO USU_TLOTANE
                (
                    USU_CODEMP,
                    USU_CODLOT,
                    USU_SEQANE,
                    USU_CAMDOC,
                    USU_CODCER
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [codemp, codlot, seqane, camdoc, certificate_code],
            )

            inserted.append({
                "seqane": seqane,
                "camdoc": camdoc,
                "file_name": page_file["file_name"],
                "server_path": page_file["server_path"],
            })

        conn.commit()

        return {
            "codemp": codemp,
            "codlot": codlot,
            "certificate_code": certificate_code,
            "pages": inserted,
        }

    except Exception:
        conn.rollback()
        raise


def summarize_lot_certificates(conn, lot: dict[str, Any]) -> dict[str, Any]:
    """Lê USU_TLOTANE e devolve um resumo real dos certificados do lote.

    Usado para gravar campos-resumo em lots (Supabase) sempre coerentes com a
    verdade do ERP, mesmo em lote com múltiplos certificados/páginas.
    """
    codemp = int(lot["erp_codemp"])
    codlot = int(lot["erp_codlot"])

    cur = conn.cursor()
    cur.execute(
        """
        SELECT USU_SEQANE, USU_CODCER, USU_CAMDOC
        FROM USU_TLOTANE
        WHERE USU_CODEMP = ?
          AND USU_CODLOT = ?
        ORDER BY USU_CODCER, USU_SEQANE
        """,
        [codemp, codlot],
    )
    rows = fetch_rows_dict(cur)

    codes: list[str] = []
    first_camdoc: Optional[str] = None
    pages_count = 0

    for r in rows:
        cert = clean_str(r.get("USU_CODCER"))
        camdoc_raw = clean_str(r.get("USU_CAMDOC"))
        if camdoc_raw:
            pages_count += 1
            if first_camdoc is None:
                first_camdoc = safe_camdoc(camdoc_raw)
        if cert and cert not in codes:
            codes.append(cert)

    return {
        "codes": codes,
        "count": len(codes),
        "pages_count": pages_count,
        "first_camdoc": first_camdoc,
    }


@app.post("/api/erp/certificados/aprovar")
def approve_certificate(payload: ApproveRequest, user=Depends(get_current_user)):
    require_admin(user)

    lot = lovable_find_one(TABLE_LOTS, {"lot_key": payload.lot_key})
    if not lot:
        raise HTTPException(status_code=404, detail="Lote não encontrado")

    doc = lovable_find_one(TABLE_DOCUMENTS, {"id": payload.document_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    source_path = clean_str(doc.get("storage_path"))

    page_files = convert_document_to_jpg_pages(
        source_path=source_path,
        lot=lot,
        certificate_code=payload.certificate_code,
    )

    # ETAPA PRINCIPAL: gravar no ERP. Se isto falhar, é erro real (500).
    conn = get_connection()
    try:
        erp_result = upsert_erp_certificate_pages(
            conn,
            lot,
            payload.certificate_code,
            page_files,
        )
        erp_summary = summarize_lot_certificates(conn, lot)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao gravar no ERP: {str(e)}")
    finally:
        conn.close()

    # ETAPA SECUNDÁRIA: refletir no Supabase. Se falhar, o ERP já está OK,
    # então retornamos um aviso em vez de erro 500.
    first_file = page_files[0] if page_files else {}
    supabase_warning = None

    try:
        lovable_patch(TABLE_DOCUMENTS, {"id": payload.document_id}, {
            "certificate_code": payload.certificate_code,
            "document_status": "APPROVED",
            "final_server_path": first_file.get("server_path"),
            "final_file_name": first_file.get("file_name"),
            "page_count": len(page_files),
            "updated_at": now_iso(),
        })

        lot_patch = {
            # Resumo do lote (campo amigável). lots.certificate_code = último aprovado.
            "certificate_code": payload.certificate_code,
            "erp_anexo_codcer": payload.certificate_code,
            "erp_camdoc": erp_summary.get("first_camdoc"),
            "certificate_codes": erp_summary.get("codes", []),
            "certificate_count": erp_summary.get("count", 0),
            "certificate_pages_count": erp_summary.get("pages_count", 0),
            "status_app": "EXPORTADO",
            "status_erp": "APROVADO",
            "synced_to_erp_at": now_iso(),
            "updated_at": now_iso(),
        }
        # erp_codcer espelha o cabeçalho do ERP: só preenche se vazio/pendente.
        if is_certificado_pendente(lot.get("erp_codcer")):
            lot_patch["erp_codcer"] = payload.certificate_code

        lovable_patch(TABLE_LOTS, {"lot_key": payload.lot_key}, lot_patch)

        lovable_insert(TABLE_DOCUMENT_COMMENTS, {
            "lot_key": payload.lot_key,
            "document_id": payload.document_id,
            "comment_type": "REVIEW",
            "comment_text": payload.notes or "Certificado aprovado e exportado para o ERP",
            "user_id": payload.approved_by or user.get("sub"),
            "created_at": now_iso(),
        })
    except Exception as e:
        supabase_warning = f"ERP atualizado, mas falhou ao atualizar Supabase: {str(e)}"

    return {
        "ok": True,
        "mensagem": "Certificado aprovado, convertido em JPG e retornado ao ERP",
        "warning": supabase_warning,
        "erp_result": erp_result,
        "certificate_code": payload.certificate_code,
        "page_files": page_files,
    }


@app.post("/api/erp/certificados/aprovar-multiplos")
def approve_certificate_multiple(payload: ApproveMultipleRequest, user=Depends(get_current_user)):
    require_admin(user)

    certificate_code = clean_str(payload.certificate_code)
    if not certificate_code:
        raise HTTPException(status_code=400, detail="certificate_code obrigatório")

    if not payload.document_ids:
        raise HTTPException(status_code=400, detail="document_ids não pode ser vazio")

    lot = lovable_find_one(TABLE_LOTS, {"lot_key": payload.lot_key})
    if not lot:
        raise HTTPException(status_code=404, detail="Lote não encontrado")

    # Busca os documentos preservando a ordem informada pelo front-end.
    docs: list[dict[str, Any]] = []
    for document_id in payload.document_ids:
        doc = lovable_find_one(TABLE_DOCUMENTS, {"id": document_id})
        if not doc:
            raise HTTPException(status_code=404, detail=f"Documento não encontrado: {document_id}")
        docs.append(doc)

    # Converte cada documento para JPG com numeração de página CONTÍNUA
    # entre todos os documentos do mesmo certificado (01, 02, 03...).
    all_page_files: list[dict[str, str]] = []
    doc_pages_map: dict[str, list[dict[str, str]]] = {}
    next_page = 1

    for doc in docs:
        source_path = clean_str(doc.get("storage_path"))
        pages = convert_document_to_jpg_pages(
            source_path=source_path,
            lot=lot,
            certificate_code=certificate_code,
            start_page=next_page,
        )
        doc_pages_map[doc["id"]] = pages
        all_page_files.extend(pages)
        next_page += len(pages)

    # ETAPA PRINCIPAL: gravar no ERP. Se isto falhar, é erro real (500).
    conn = get_connection()
    try:
        # Apaga somente os anexos deste mesmo certificado e recria todas as
        # páginas de uma vez (evita que uma 2ª aprovação apague a 1ª).
        erp_result = upsert_erp_certificate_pages(
            conn,
            lot,
            certificate_code,
            all_page_files,
        )
        erp_summary = summarize_lot_certificates(conn, lot)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao gravar no ERP: {str(e)}")
    finally:
        conn.close()

    # ETAPA SECUNDÁRIA: refletir no Supabase. Se falhar, o ERP já está OK,
    # então retornamos um aviso em vez de erro 500.
    supabase_warning = None

    try:
        for doc in docs:
            pages = doc_pages_map.get(doc["id"], [])
            first_file = pages[0] if pages else {}

            lovable_patch(TABLE_DOCUMENTS, {"id": doc["id"]}, {
                "certificate_code": certificate_code,
                "document_status": "APPROVED",
                "final_server_path": first_file.get("server_path"),
                "final_file_name": first_file.get("file_name"),
                "page_count": len(pages),
                "updated_at": now_iso(),
            })

        lot_patch = {
            # Resumo do lote (campo amigável). lots.certificate_code = último aprovado.
            "certificate_code": certificate_code,
            "erp_anexo_codcer": certificate_code,
            "erp_camdoc": erp_summary.get("first_camdoc"),
            "certificate_codes": erp_summary.get("codes", []),
            "certificate_count": erp_summary.get("count", 0),
            "certificate_pages_count": erp_summary.get("pages_count", 0),
            "status_app": "EXPORTADO",
            "status_erp": "APROVADO",
            "synced_to_erp_at": now_iso(),
            "updated_at": now_iso(),
        }
        # erp_codcer espelha o cabeçalho do ERP: só preenche se vazio/pendente.
        if is_certificado_pendente(lot.get("erp_codcer")):
            lot_patch["erp_codcer"] = certificate_code

        lovable_patch(TABLE_LOTS, {"lot_key": payload.lot_key}, lot_patch)

        lovable_insert(TABLE_DOCUMENT_COMMENTS, {
            "lot_key": payload.lot_key,
            "document_id": payload.document_ids[0],
            "comment_type": "REVIEW",
            "comment_text": payload.notes or (
                f"Certificado aprovado com {len(all_page_files)} página(s) "
                f"em {len(docs)} documento(s) e exportado para o ERP"
            ),
            "user_id": payload.approved_by or user.get("sub"),
            "created_at": now_iso(),
        })
    except Exception as e:
        supabase_warning = f"ERP atualizado, mas falhou ao atualizar Supabase: {str(e)}"

    return {
        "ok": True,
        "mensagem": "Certificado aprovado com múltiplas imagens, convertido em JPG e retornado ao ERP",
        "warning": supabase_warning,
        "erp_result": erp_result,
        "certificate_code": certificate_code,
        "total_documentos": len(docs),
        "total_paginas": len(all_page_files),
        "page_files": all_page_files,
    }


@app.post("/api/erp/certificados/alterar")
def alter_certificate(payload: AlterRequest, user=Depends(get_current_user)):
    require_admin(user)

    lot = lovable_find_one(TABLE_LOTS, {"lot_key": payload.lot_key})
    if not lot:
        raise HTTPException(status_code=404, detail="Lote não encontrado")

    doc = lovable_find_one(TABLE_DOCUMENTS, {"id": payload.document_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    new_certificate_code = clean_str(payload.certificate_code) or clean_str(doc.get("certificate_code"))
    if not new_certificate_code:
        raise HTTPException(status_code=400, detail="certificate_code obrigatório (no payload ou no documento existente)")

    source_path = clean_str(doc.get("storage_path"))

    page_files = convert_document_to_jpg_pages(
        source_path=source_path,
        lot=lot,
        certificate_code=new_certificate_code,
    )

    conn = get_connection()
    try:
        erp_result = upsert_erp_certificate_pages(
            conn,
            lot,
            new_certificate_code,
            page_files,
        )

        first_file = page_files[0] if page_files else {}

        lovable_patch(TABLE_DOCUMENTS, {"id": payload.document_id}, {
            "certificate_code": new_certificate_code,
            "final_server_path": first_file.get("server_path"),
            "final_file_name": first_file.get("file_name"),
            "page_count": len(page_files),
            "updated_at": now_iso(),
        })

        lovable_patch(TABLE_LOTS, {"lot_key": payload.lot_key}, {
            "erp_codcer": new_certificate_code,
            "synced_to_erp_at": now_iso(),
            "updated_at": now_iso(),
        })

        lovable_insert(TABLE_DOCUMENT_COMMENTS, {
            "lot_key": payload.lot_key,
            "document_id": payload.document_id,
            "comment_type": "ALTERATION",
            "comment_text": payload.notes or "Certificado alterado",
            "user_id": payload.altered_by or user.get("sub"),
            "created_at": now_iso(),
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao sincronizar ERP/Supabase: {str(e)}")
    finally:
        conn.close()

    return {
        "ok": True,
        "mensagem": "Certificado alterado, convertido em JPG e atualizado no ERP",
        "erp_result": erp_result,
        "certificate_code": new_certificate_code,
        "page_files": page_files,
    }


@app.post("/api/erp/certificados/reprovar")
def reject_certificate(payload: RejectRequest, user=Depends(get_current_user)):
    lot = lovable_find_one(TABLE_LOTS, {"lot_key": payload.lot_key})
    if not lot:
        raise HTTPException(status_code=404, detail="Lote não encontrado")

    doc = lovable_find_one(TABLE_DOCUMENTS, {"id": payload.document_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    lovable_patch(TABLE_DOCUMENTS, {"id": payload.document_id}, {
        "document_status": "REJECTED",
        "updated_at": now_iso(),
    })

    lovable_patch(TABLE_LOTS, {"lot_key": payload.lot_key}, {
        "status_app": "REPROVADO",
        "updated_at": now_iso(),
    })

    lovable_insert(TABLE_DOCUMENT_COMMENTS, {
        "lot_key": payload.lot_key,
        "document_id": payload.document_id,
        "comment_type": "REJECTION_REASON",
        "comment_text": payload.reason,
        "user_id": payload.rejected_by or user.get("sub"),
        "created_at": now_iso(),
    })

    return {"ok": True, "mensagem": "Certificado reprovado"}


@app.post("/api/erp/certificados/retornar-analise")
def return_to_analysis(payload: ReturnToAnalysisRequest, user=Depends(get_current_user)):
    lot = lovable_find_one(TABLE_LOTS, {"lot_key": payload.lot_key})
    if not lot:
        raise HTTPException(status_code=404, detail="Lote não encontrado")

    doc = lovable_find_one(TABLE_DOCUMENTS, {"id": payload.document_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    lovable_patch(TABLE_DOCUMENTS, {"id": payload.document_id}, {
        "document_status": "UPLOADED",
        "updated_at": now_iso(),
    })

    lovable_patch(TABLE_LOTS, {"lot_key": payload.lot_key}, {
        "status_app": "EM_ANALISE",
        "updated_at": now_iso(),
    })

    lovable_insert(TABLE_DOCUMENT_COMMENTS, {
        "lot_key": payload.lot_key,
        "document_id": payload.document_id,
        "comment_type": "NOTE",
        "comment_text": payload.reason or "Retornado para nova análise",
        "user_id": payload.returned_by or user.get("sub"),
        "created_at": now_iso(),
    })

    return {"ok": True, "mensagem": "Documento retornado para análise"}


@app.post("/api/erp/certificados/justificar-desvio")
def justify_certificate_deviation(payload: JustifyDeviationRequest, user=Depends(get_current_user)):
    lot = lovable_find_one(TABLE_LOTS, {"lot_key": payload.lot_key})
    if not lot:
        raise HTTPException(status_code=404, detail="Lote não encontrado")

    doc = lovable_find_one(TABLE_DOCUMENTS, {"id": payload.document_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")

    lovable_patch(TABLE_DOCUMENTS, {"id": payload.document_id}, {
        "document_status": "APPROVED_WITH_DEVIATION",
        "updated_at": now_iso(),
    })

    lovable_patch(TABLE_LOTS, {"lot_key": payload.lot_key}, {
        "status_app": "DESVIO_JUSTIFICADO",
        "updated_at": now_iso(),
    })

    lovable_insert(TABLE_DOCUMENT_COMMENTS, {
        "lot_key": payload.lot_key,
        "document_id": payload.document_id,
        "comment_type": "DEVIATION_JUSTIFICATION",
        "comment_text": payload.reason,
        "user_id": payload.justified_by or user.get("sub"),
        "created_at": now_iso(),
    })

    return {
        "ok": True,
        "mensagem": "Certificado justificado com desvio",
    }


# =============================================================================
# PESQUISA / APROVADOS
# =============================================================================

@app.get("/api/erp/certificados/aprovados")
def get_approved_certificates(user=Depends(get_current_user)):
    docs = lovable_select(TABLE_DOCUMENTS, filters={"document_status": "APPROVED"}, order="updated_at.desc")
    return {"total": len(docs), "dados": docs}


@app.get("/api/erp/certificados/search")
def search_certificates(q: str = Query(default=""), user=Depends(get_current_user)):
    q = clean_str(q).lower()
    docs = lovable_select(TABLE_DOCUMENTS, limit=500, order="updated_at.desc")
    lots_map = {lot["lot_key"]: lot for lot in lovable_select(TABLE_LOTS, limit=500)}

    if not q:
        return {"total": len(docs), "dados": docs}

    results = []
    for doc in docs:
        lot = lots_map.get(doc.get("lot_key"))
        haystack = " ".join([
            clean_str(doc.get("original_file_name")),
            clean_str(doc.get("certificate_code")),
            clean_str(doc.get("final_file_name")),
            clean_str(doc.get("lot_key")),
            clean_str(lot.get("supplier_name") if lot else ""),
            clean_str(lot.get("product_description") if lot else ""),
            clean_str(lot.get("invoice_item_description") if lot else ""),
            clean_str(lot.get("erp_numnfc") if lot else ""),
        ]).lower()

        if q in haystack:
            item = dict(doc)
            item["lot"] = lot
            results.append(item)

    return {"total": len(results), "dados": results}


@app.get("/api/erp/certificados/arquivo/{camdoc}")
def get_certificado_arquivo(camdoc: str, user=Depends(get_current_user)):
    file_path = resolve_certifq_jpg_path(camdoc, must_exist=True)

    return FileResponse(
        path=file_path,
        media_type="image/jpeg",
        filename=os.path.basename(file_path),
    )


# =============================================================================
# RASTREABILIDADE DE MATÉRIA PRIMA (USU_T900LCM)
# =============================================================================

def get_titulo_rastreabilidade(relatorio: int) -> str:
    if relatorio == 1:
        return "RASTREABILIDADE DE MATÉRIAS PRIMAS UTILIZADAS"
    if relatorio == 2:
        return "LOTES DE MATÉRIA PRIMA"
    if relatorio == 3:
        return "CERTIFICADOS DE QUALIDADE DE MATÉRIA PRIMA"
    return "RASTREABILIDADE DE MATÉRIA PRIMA"


def validar_ordenacao_rastreabilidade(relatorio: int, ordenacao: str) -> None:
    ordenacao = clean_str(ordenacao).upper()

    if relatorio == 1 and ordenacao != "PROJETO":
        raise HTTPException(
            status_code=400,
            detail="Para relatório 1, escolha ordenação PROJETO."
        )

    if relatorio in (2, 3) and ordenacao != "FAMILIA":
        raise HTTPException(
            status_code=400,
            detail="Para relatórios 2 e 3, escolha ordenação FAMILIA."
        )


def query_rastreabilidade_materia_prima(payload: RastreabilidadeMateriaPrimaRequest) -> list[dict[str, Any]]:
    validar_ordenacao_rastreabilidade(payload.relatorio, payload.ordenacao or "")

    # Obra e Projeto apontam para o mesmo USU_NUMPRJ (modelo do relatório).
    # USU_T900LCM não tem coluna de filial, então filial não é filtrada.
    numprj = payload.projeto or payload.obra

    # Normaliza filtros vazios -> None (0 e "" não devem filtrar).
    f_numdes = payload.numdes or None
    f_numorp = payload.numorp or None
    f_codori = clean_str(payload.codori) or None

    limit = int(payload.limit or 1000)
    limit = max(1, min(limit, 5000))

    if clean_str(payload.ordenacao).upper() == "FAMILIA":
        order_by = """
            PRO.CODFAM,
            LCM.USU_CODCMP,
            LCM.USU_NUMPRJ,
            LCM.USU_CODLOT
        """
    else:
        order_by = """
            LCM.USU_NUMPRJ,
            LCM.USU_NUMDES,
            LCM.USU_REVDES,
            LCM.USU_CODORI,
            LCM.USU_NUMORP,
            LCM.USU_CODCMP,
            LCM.USU_CODLOT
        """

    sql = f"""
        SELECT TOP {limit}
            LCM.USU_CODEMP AS empresa,

            LCM.USU_NUMPRJ AS projeto,
            PRJ.NOMPRJ     AS nome_projeto,

            LCM.USU_NUMDES AS desenho,
            LCM.USU_REVDES AS revisao_desenho,
            LCM.USU_CODORI AS origem,
            LCM.USU_NUMORP AS op,

            LCM.USU_CODCMP AS codigo_componente,
            LCM.USU_DERCMP AS derivacao,
            PRO.DESPRO     AS descricao_produto,
            PRO.CODFAM     AS familia,
            FAM.DESFAM     AS descricao_familia,

            CAST(LCM.USU_CODLOT AS varchar(50)) AS lote,

            CERT.certificados      AS certificados,
            CERT.qtd_certificados  AS qtd_certificados,

            POS.posicao_desenho    AS posicao_desenho,

            CASE
                WHEN SUBST.codcmp_substituido IS NOT NULL THEN 'S'
                ELSE 'N'
            END AS componente_substituido,
            SUBST.codcmp_substituido AS codigo_componente_original,
            SUBST.dercmp_substituido AS derivacao_original,
            SUBST.despro_substituido AS descricao_produto_original

        FROM USU_T900LCM LCM

        LEFT JOIN E615PRJ PRJ
            ON PRJ.CODEMP = LCM.USU_CODEMP
           AND PRJ.NUMPRJ = LCM.USU_NUMPRJ

        LEFT JOIN E075PRO PRO
            ON PRO.CODEMP = LCM.USU_CODEMP
           AND PRO.CODPRO = LCM.USU_CODCMP

        LEFT JOIN E012FAM FAM
            ON FAM.CODEMP = PRO.CODEMP
           AND FAM.CODFAM = PRO.CODFAM

        OUTER APPLY (
            SELECT
                STRING_AGG(CONVERT(varchar(100), A.USU_CODCER), ';') AS certificados,
                COUNT(DISTINCT A.USU_CODCER) AS qtd_certificados
            FROM USU_TLOTANE A
            WHERE A.USU_CODEMP = LCM.USU_CODEMP
              AND A.USU_CODLOT = TRY_CONVERT(bigint, LCM.USU_CODLOT)
              AND ISNULL(LTRIM(RTRIM(A.USU_CODCER)), '') <> ''
        ) CERT

        -- Componente substituído: outra linha do MESMO lote (real, não vazio)
        -- com componente diferente. Espelha ComponenteSubstituido() do relatório.
        OUTER APPLY (
            SELECT TOP 1
                SUB.USU_CODCMP AS codcmp_substituido,
                SUB.USU_DERCMP AS dercmp_substituido,
                PROSUB.DESPRO  AS despro_substituido
            FROM USU_T900LCM SUB
            LEFT JOIN E075PRO PROSUB
                ON PROSUB.CODEMP = SUB.USU_CODEMP
               AND PROSUB.CODPRO = SUB.USU_CODCMP
            WHERE SUB.USU_CODEMP = LCM.USU_CODEMP
              AND SUB.USU_NUMPRJ = LCM.USU_NUMPRJ
              AND SUB.USU_NUMDES = LCM.USU_NUMDES
              AND SUB.USU_REVDES = LCM.USU_REVDES
              AND SUB.USU_CODORI = LCM.USU_CODORI
              AND SUB.USU_NUMORP = LCM.USU_NUMORP
              AND SUB.USU_CODLOT = LCM.USU_CODLOT
              AND SUB.USU_CODCMP <> LCM.USU_CODCMP
              AND ISNULL(LTRIM(RTRIM(LCM.USU_CODLOT)), '') <> ''
            ORDER BY SUB.USU_CODCMP
        ) SUBST

        -- Posição no desenho = marca da peça (USU_T900QDO.USU_DESPEC). É dado
        -- do DESENHO (projeto/desenho/revisão + matéria-prima), não da OP: em
        -- USU_T900QDO o USU_NUMORP é apenas uma OP representativa (TOP 1 na
        -- geração), então filtrar por CODORI/NUMORP zerava a posição das OPs
        -- de origem Tekla (TKE/TKS/TKC). Resolve por QDO->MPR pelo desenho.
        OUTER APPLY (
            SELECT STRING_AGG(P.USU_DESPEC, ', ')
                       WITHIN GROUP (ORDER BY P.USU_DESPEC) AS posicao_desenho
            FROM (
                SELECT DISTINCT CONVERT(varchar(200), QDO.USU_DESPEC) AS USU_DESPEC
                FROM USU_T900QDO QDO
                INNER JOIN USU_T900MPR MPR
                    ON MPR.USU_CODEMP = QDO.USU_CODEMP
                   AND MPR.USU_CODMPR = QDO.USU_CODMPR
                   AND MPR.USU_DERMPR = QDO.USU_DERMPR
                   AND MPR.USU_CODSEN = COALESCE(SUBST.codcmp_substituido, LCM.USU_CODCMP)
                   AND MPR.USU_DERSEN = COALESCE(SUBST.dercmp_substituido, LCM.USU_DERCMP)
                WHERE QDO.USU_CODEMP = LCM.USU_CODEMP
                  AND QDO.USU_NUMPRJ = LCM.USU_NUMPRJ
                  AND QDO.USU_NUMDES = LCM.USU_NUMDES
                  AND QDO.USU_REVDES = LCM.USU_REVDES
            ) P
        ) POS

        WHERE LCM.USU_CODEMP = ?
          AND (? IS NULL OR LCM.USU_NUMPRJ = ?)
          AND (? IS NULL OR LCM.USU_NUMDES = ?)
          AND (? IS NULL OR LCM.USU_CODORI = ?)
          AND (? IS NULL OR LCM.USU_NUMORP = ?)

        ORDER BY {order_by}
    """

    params = [
        payload.empresa,
        numprj, numprj,
        f_numdes, f_numdes,
        f_codori, f_codori,
        f_numorp, f_numorp,
    ]

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = fetch_rows_dict(cur)

        # Se usuário não quer posição do desenho, limpa no retorno.
        if clean_str(payload.listar_posicao_desenho).upper() != "S":
            for row in rows:
                row["posicao_desenho"] = ""

        for row in rows:
            # Se houve componente substituído, monta a descrição como no relatório.
            if clean_str(row.get("componente_substituido")) == "S":
                desc_original = clean_str(row.get("descricao_produto_original"))
                cod_original = clean_str(row.get("codigo_componente_original"))
                desc_atual = clean_str(row.get("descricao_produto"))
                cod_atual = clean_str(row.get("codigo_componente"))

                if desc_original or cod_original:
                    row["descricao_produto"] = (
                        f"{desc_original} - {cod_original} "
                        f"*** substituído por *** "
                        f"{desc_atual} - {cod_atual}"
                    ).strip()

            # Espelha em posicoes_desenho (plural) para compatibilidade de nome
            # com o front-end — alguns lugares esperam singular, outros plural.
            row["posicoes_desenho"] = clean_str(row.get("posicao_desenho"))

        return rows
    finally:
        conn.close()


@app.post("/api/erp/rastreabilidade-materia-prima")
def rastreabilidade_materia_prima(
    payload: RastreabilidadeMateriaPrimaRequest,
    user=Depends(get_current_user),
):
    dados = query_rastreabilidade_materia_prima(payload)

    nome_projeto = ""
    if dados:
        nome_projeto = clean_str(dados[0].get("nome_projeto"))

    codigo_relatorio = clean_str(payload.codigo_relatorio)
    numprj = payload.projeto or payload.obra or ""

    cabecalho = {
        "titulo": get_titulo_rastreabilidade(payload.relatorio),
        "relatorio": payload.relatorio,
        "empresa": payload.empresa,
        "filial": payload.filial,
        "obra": payload.obra,
        "projeto": numprj,
        "codigo_relatorio": codigo_relatorio,
        "codigo": f"{numprj}-{codigo_relatorio}" if codigo_relatorio else clean_str(numprj),
        "obra_cliente": f"{numprj} | {nome_projeto}" if nome_projeto else clean_str(numprj),
        "revisao_documento": payload.revisao_documento,
        "listar_posicao_desenho": clean_str(payload.listar_posicao_desenho).upper(),
        "ordenacao": clean_str(payload.ordenacao).upper(),
        "gerado_em": now_iso(),
    }

    return {
        "ok": True,
        "total": len(dados),
        "cabecalho": cabecalho,
        "dados": dados,
    }


@app.post("/api/erp/rastreabilidade-materia-prima/gravar-lotes-op")
def gravar_lotes_materia_prima_op(
    payload: GravarLotesOpRequest,
    user=Depends(get_current_user),
):
    # Ação de ESCRITA em produção (E900EOQ.USU_CODLOT). Modo seguro:
    # - executar=false  -> simula tudo e faz ROLLBACK (não grava nada)
    # - executar=true   -> grava só quando há linha com lote vazio (UPDATE)
    # NUNCA cria apontamento novo (INSERT) automaticamente.
    require_admin(user)

    origem = clean_str(payload.origem)

    if not origem:
        raise HTTPException(status_code=400, detail="Origem da OP é obrigatória.")

    if not payload.op:
        raise HTTPException(status_code=400, detail="Número da OP é obrigatório.")

    conn = get_connection()

    try:
        conn.autocommit = False
        cur = conn.cursor()

        # 1. Lotes já informados na OP (E900EOQ.USU_CODLOT).
        cur.execute(
            """
            SELECT DISTINCT
                   LTRIM(RTRIM(CONVERT(varchar(50), USU_CODLOT))) AS codlot
              FROM E900EOQ
             WHERE CODEMP = ?
               AND CODORI = ?
               AND NUMORP = ?
               AND USU_CODLOT IS NOT NULL
               AND LTRIM(RTRIM(CONVERT(varchar(50), USU_CODLOT))) <> ''
            """,
            [payload.empresa, origem, payload.op],
        )
        lotes_op = [clean_str(r.codlot) for r in cur.fetchall() if clean_str(r.codlot)]

        # 2. Componentes da OP.
        cur.execute(
            """
            SELECT CODETG AS codetg,
                   SEQCMP AS seqcmp,
                   CODCMP AS codcmp,
                   CODDER AS codder
              FROM E900CMO
             WHERE CODEMP = ?
               AND CODORI = ?
               AND NUMORP = ?
             ORDER BY CODETG, SEQCMP, CODCMP, CODDER
            """,
            [payload.empresa, origem, payload.op],
        )
        componentes = fetch_rows_dict(cur)

        resultados = []

        for comp in componentes:
            codcmp = clean_str(comp.get("codcmp"))
            codder = clean_str(comp.get("codder"))

            if not codcmp:
                continue

            lote_ja_encontrado = None

            # 3. Componente já tem lote dentro dos lotes da OP?
            if lotes_op:
                placeholders = ",".join(["?"] * len(lotes_op))
                params = [codcmp, payload.empresa]

                filtro_derivacao = ""
                if payload.usar_derivacao:
                    filtro_derivacao = " AND E440IPC.CODDER = ? "
                    params.insert(1, codder)

                params.extend(lotes_op)

                sql_existente = f"""
                    SELECT TOP 1
                           USU_TLOTITE.USU_CODLOT AS codlot
                      FROM USU_TLOTITE
                     INNER JOIN E440IPC
                        ON USU_TLOTITE.USU_CODEMP = E440IPC.CODEMP
                       AND USU_TLOTITE.USU_CODFIL = E440IPC.CODFIL
                       AND USU_TLOTITE.USU_CODFOR = E440IPC.CODFOR
                       AND USU_TLOTITE.USU_NUMNFC = E440IPC.NUMNFC
                       AND USU_TLOTITE.USU_CODSNF = E440IPC.CODSNF
                       AND USU_TLOTITE.USU_SEQIPC = E440IPC.SEQIPC
                     WHERE E440IPC.CODPRO = ?
                       {filtro_derivacao}
                       AND E440IPC.CODEMP = ?
                       AND USU_TLOTITE.USU_CODLOT IN ({placeholders})
                     ORDER BY USU_TLOTITE.USU_CODLOT DESC
                """
                cur.execute(sql_existente, params)
                row_existente = cur.fetchone()
                if row_existente:
                    lote_ja_encontrado = clean_str(row_existente.codlot)

            if lote_ja_encontrado:
                resultados.append({
                    "codigo_componente": codcmp,
                    "derivacao": codder,
                    "acao": "ja_possui_lote_na_op",
                    "lote": lote_ja_encontrado,
                    "executado": False,
                })
                continue

            # 4. Lote mais novo recebido para o componente.
            params_lote = [payload.empresa, codcmp]

            filtro_derivacao_lote = ""
            if payload.usar_derivacao:
                filtro_derivacao_lote = " AND E440IPC.CODDER = ? "
                params_lote.append(codder)

            sql_lote_mais_novo = f"""
                SELECT TOP 1
                       USU_TLOTITE.USU_CODLOT AS codlot
                  FROM USU_TLOTITE
                 INNER JOIN E440IPC
                    ON E440IPC.CODEMP = USU_TLOTITE.USU_CODEMP
                   AND E440IPC.CODFIL = USU_TLOTITE.USU_CODFIL
                   AND E440IPC.CODFOR = USU_TLOTITE.USU_CODFOR
                   AND E440IPC.NUMNFC = USU_TLOTITE.USU_NUMNFC
                   AND E440IPC.CODSNF = USU_TLOTITE.USU_CODSNF
                   AND E440IPC.SEQIPC = USU_TLOTITE.USU_SEQIPC
                 INNER JOIN E440NFC
                    ON E440NFC.CODEMP = E440IPC.CODEMP
                   AND E440NFC.CODFIL = E440IPC.CODFIL
                   AND E440NFC.CODFOR = E440IPC.CODFOR
                   AND E440NFC.NUMNFC = E440IPC.NUMNFC
                   AND E440NFC.CODSNF = E440IPC.CODSNF
                 WHERE USU_TLOTITE.USU_CODEMP = ?
                   AND E440IPC.CODPRO = ?
                   {filtro_derivacao_lote}
                 ORDER BY E440NFC.DATENT DESC,
                          USU_TLOTITE.USU_CODLOT DESC
            """
            cur.execute(sql_lote_mais_novo, params_lote)
            row_lote = cur.fetchone()
            lote_sugerido = clean_str(row_lote.codlot) if row_lote else "999999999999999"

            # 5. Linha da OP com USU_CODLOT vazio para gravar.
            cur.execute(
                """
                SELECT TOP 1
                       CODETG AS codetg,
                       SEQEOQ AS seqeoq,
                       CODPRO AS codpro,
                       CODDER AS codder
                  FROM E900EOQ
                 WHERE CODEMP = ?
                   AND CODORI = ?
                   AND NUMORP = ?
                   AND (
                          USU_CODLOT IS NULL
                       OR LTRIM(RTRIM(CONVERT(varchar(50), USU_CODLOT))) = ''
                   )
                 ORDER BY SEQEOQ
                """,
                [payload.empresa, origem, payload.op],
            )
            linha_livre = cur.fetchone()

            item_resultado = {
                "codigo_componente": codcmp,
                "derivacao": codder,
                "acao": "gravar_lote_sugerido",
                "lote_sugerido": lote_sugerido,
                "executado": False,
                "mensagem": "",
            }

            if linha_livre:
                item_resultado["codetg"] = linha_livre.codetg
                item_resultado["seqeoq"] = linha_livre.seqeoq

                if payload.executar:
                    cur.execute(
                        """
                        UPDATE E900EOQ
                           SET USU_CODLOT = ?
                         WHERE CODEMP = ?
                           AND CODORI = ?
                           AND NUMORP = ?
                           AND CODETG = ?
                           AND SEQEOQ = ?
                        """,
                        [
                            lote_sugerido,
                            payload.empresa,
                            origem,
                            payload.op,
                            linha_livre.codetg,
                            linha_livre.seqeoq,
                        ],
                    )
                    item_resultado["executado"] = True
                    item_resultado["mensagem"] = "Lote gravado em E900EOQ.USU_CODLOT."
                else:
                    item_resultado["mensagem"] = "Simulação: existe linha livre para gravação."
            else:
                item_resultado["acao"] = "sem_linha_livre"
                item_resultado["mensagem"] = (
                    "Não existe linha em E900EOQ com USU_CODLOT vazio. "
                    "A regra Senior original criaria apontamentos novos, mas a API está em modo seguro."
                )
                if payload.executar and payload.criar_apontamento_se_necessario:
                    item_resultado["mensagem"] = (
                        "Criação automática de apontamento novo ainda não foi implementada na API "
                        "por segurança. Execute a rotina original no ERP ou habilite após validação."
                    )

            resultados.append(item_resultado)

        if payload.executar:
            conn.commit()
        else:
            conn.rollback()

        return {
            "ok": True,
            "modo": "EXECUCAO" if payload.executar else "SIMULACAO",
            "empresa": payload.empresa,
            "origem": origem,
            "op": payload.op,
            "lotes_ja_existentes_na_op": lotes_op,
            "total_componentes": len(componentes),
            "total_resultados": len(resultados),
            "dados": resultados,
        }

    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao gravar lotes da OP: {str(e)}")
    finally:
        conn.close()


# =============================================================================
# RASTREABILIDADE DE TINTAS (MPOP501.GER / E210MVP)
# =============================================================================

def buscar_anexos_certificados_por_lotes(
    cur,
    codemp: int,
    codlots: list[Any],
) -> dict[str, dict[str, Any]]:
    """Busca, em UMA query (com chunking), os anexos/imagens de certificado de
    vários lotes em USU_TLOTANE. Retorna mapa lote -> { dados, certificados, ... }."""
    lotes_limpos: list[str] = []
    for lote in codlots:
        lote_s = clean_str(lote)
        if lote_s and lote_s not in lotes_limpos:
            lotes_limpos.append(lote_s)

    if not lotes_limpos:
        return {}

    rows: list[dict[str, Any]] = []
    CHUNK = 1000  # margem segura sob o limite de ~2100 parâmetros do SQL Server
    for i in range(0, len(lotes_limpos), CHUNK):
        chunk = lotes_limpos[i:i + CHUNK]
        placeholders = ",".join(["?"] * len(chunk))
        cur.execute(
            f"""
            SELECT
                USU_CODLOT AS codlot,
                USU_SEQANE AS seqane,
                USU_CODCER AS certificate_code,
                USU_CAMDOC AS camdoc
            FROM USU_TLOTANE
            WHERE USU_CODEMP = ?
              AND CONVERT(varchar(50), USU_CODLOT) IN ({placeholders})
            ORDER BY USU_CODLOT, USU_CODCER, USU_SEQANE
            """,
            [codemp] + chunk,
        )
        rows.extend(fetch_rows_dict(cur))

    por_lote: dict[str, dict[str, Any]] = {}

    for row in rows:
        codlot = clean_str(row.get("codlot"))
        cert = clean_str(row.get("certificate_code"))
        camdoc_raw = clean_str(row.get("camdoc"))

        if not codlot or not camdoc_raw:
            continue

        camdoc = safe_camdoc(camdoc_raw)
        file_path = resolve_certifq_jpg_path(camdoc, must_exist=False)

        item = {
            "seqane": row.get("seqane"),
            "certificate_code": cert,
            "camdoc": camdoc,
            "file_name": f"{camdoc}.JPG",
            "server_path": file_path,
            "exists": os.path.exists(file_path),
            "arquivo_url": f"/api/erp/certificados/arquivo/{quote(camdoc)}",
        }

        if codlot not in por_lote:
            por_lote[codlot] = {
                "lot_number": codlot,
                "total": 0,
                "certificate_codes": [],
                "dados": [],
                "certificados": [],
            }

        por_lote[codlot]["dados"].append(item)
        por_lote[codlot]["total"] += 1
        if cert and cert not in por_lote[codlot]["certificate_codes"]:
            por_lote[codlot]["certificate_codes"].append(cert)

    # Agrupa páginas por certificado dentro de cada lote.
    for lote_data in por_lote.values():
        grupos: dict[str, dict[str, Any]] = {}
        for item in lote_data["dados"]:
            k = item.get("certificate_code") or "SEM_CERTIFICADO"
            if k not in grupos:
                grupos[k] = {"certificate_code": k, "pages_count": 0, "pages": []}
            grupos[k]["pages_count"] += 1
            grupos[k]["pages"].append(item)
        lote_data["certificados"] = list(grupos.values())

    return por_lote


def query_rastreabilidade_tintas(payload: RastreabilidadeTintasRequest) -> list[dict[str, Any]]:
    data_ini = parse_date_input(payload.data_inicial)
    data_fim = parse_date_input(payload.data_final)

    if not data_ini or not data_fim:
        raise HTTPException(
            status_code=400,
            detail="Informe o período de movimentação de estoque."
        )

    if data_ini > data_fim:
        raise HTTPException(
            status_code=400,
            detail="Data inicial não pode ser maior que a data final."
        )

    # 0 / vazio não filtram.
    f_filial = payload.filial or None
    f_obra = payload.obra or None

    limit = int(payload.limit or 1000)
    limit = max(1, min(limit, 5000))

    ordenacao = clean_str(payload.ordenacao).upper()
    if ordenacao == "LOTE":
        order_by = "MVP.USU_CODLOT, MVP.DATMOV, MVP.CODPRO"
    elif ordenacao == "PRODUTO":
        order_by = "MVP.CODPRO, MVP.CODDER, MVP.DATMOV, MVP.USU_CODLOT"
    else:
        order_by = "MVP.DATMOV, MVP.NUMPRJ, MVP.USU_CODLOT, MVP.CODPRO"

    sql = f"""
        SELECT TOP {limit}
            MVP.CODEMP AS empresa,
            MVP.CODFIL AS filial,

            MVP.DATMOV AS data_movimento,
            MVP.CODTNS AS transacao,
            MVP.CODDEP AS deposito,

            MVP.NUMPRJ AS obra,
            PRJ.NOMPRJ AS nome_obra,

            MVP.CODPRO AS produto,
            MVP.CODDER AS derivacao,
            PRO.DESPRO AS descricao_produto,
            PRO.CODFAM AS familia,

            MVP.QTDMOV AS quantidade_movimento,
            CAST(MVP.USU_CODLOT AS varchar(50)) AS lote,

            MVP.USU_CODOP1 AS operador_1,
            OPE1.NOMOPE AS nome_operador_1,
            MVP.USU_CODOP2 AS operador_2,
            OPE2.NOMOPE AS nome_operador_2,

            CERT.certificados AS certificados,
            CERT.qtd_certificados AS qtd_certificados,
            CERT.qtd_imagens_certificados AS qtd_imagens_certificados

        FROM E210MVP MVP

        INNER JOIN E075PRO PRO
            ON PRO.CODEMP = MVP.CODEMP
           AND PRO.CODPRO = MVP.CODPRO

        LEFT JOIN E615PRJ PRJ
            ON PRJ.CODEMP = MVP.CODEMP
           AND PRJ.NUMPRJ = MVP.NUMPRJ

        LEFT JOIN E906OPE OPE1
            ON OPE1.CODEMP = MVP.CODEMP
           AND OPE1.NUMCAD = MVP.USU_CODOP1

        LEFT JOIN E906OPE OPE2
            ON OPE2.CODEMP = MVP.CODEMP
           AND OPE2.NUMCAD = MVP.USU_CODOP2

        OUTER APPLY (
            SELECT
                STUFF((
                    SELECT ';' + CONVERT(varchar(100), A.USU_CODCER)
                    FROM USU_TLOTANE A
                    WHERE A.USU_CODEMP = MVP.CODEMP
                      AND A.USU_CODLOT = TRY_CONVERT(bigint, MVP.USU_CODLOT)
                      AND ISNULL(LTRIM(RTRIM(A.USU_CODCER)), '') <> ''
                    GROUP BY A.USU_CODCER
                    ORDER BY A.USU_CODCER
                    FOR XML PATH(''), TYPE
                ).value('.', 'varchar(max)'), 1, 1, '') AS certificados,

                (
                    SELECT COUNT(DISTINCT A2.USU_CODCER)
                    FROM USU_TLOTANE A2
                    WHERE A2.USU_CODEMP = MVP.CODEMP
                      AND A2.USU_CODLOT = TRY_CONVERT(bigint, MVP.USU_CODLOT)
                      AND ISNULL(LTRIM(RTRIM(A2.USU_CODCER)), '') <> ''
                ) AS qtd_certificados,

                (
                    SELECT COUNT(*)
                    FROM USU_TLOTANE A3
                    WHERE A3.USU_CODEMP = MVP.CODEMP
                      AND A3.USU_CODLOT = TRY_CONVERT(bigint, MVP.USU_CODLOT)
                      AND ISNULL(LTRIM(RTRIM(A3.USU_CAMDOC)), '') <> ''
                ) AS qtd_imagens_certificados
        ) CERT

        WHERE MVP.CODEMP = ?
          AND (? IS NULL OR MVP.CODFIL = ?)
          AND (? IS NULL OR MVP.NUMPRJ = ?)
          AND MVP.DATMOV >= ?
          AND MVP.DATMOV <= ?
          AND PRO.CODFAM = 'TINTAS'
          AND MVP.CODTNS IN ('90250', '90251')

        ORDER BY {order_by}
    """

    params = [
        payload.empresa,
        f_filial, f_filial,
        f_obra, f_obra,
        data_ini,
        data_fim,
    ]

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = fetch_rows_dict(cur)

        # Imagens dos certificados em LOTE (uma query só, sem N+1).
        listar_imagens = clean_str(payload.listar_imagens_certificados).upper() == "S"
        anexos_por_lote: dict[str, dict[str, Any]] = {}
        if listar_imagens:
            codlots = [clean_str(r.get("lote")) for r in rows if clean_str(r.get("lote"))]
            anexos_por_lote = buscar_anexos_certificados_por_lotes(
                cur=cur, codemp=payload.empresa, codlots=codlots,
            )

        anexos_vazio = {
            "lot_number": "", "total": 0,
            "certificate_codes": [], "dados": [], "certificados": [],
        }

        for row in rows:
            row["data_movimento_br"] = format_date_br(row.get("data_movimento"))

            # Produto / derivação / lote (snake + amigável p/ front)
            row["codigo_produto"] = clean_str(row.get("produto"))
            row["product_code"] = clean_str(row.get("produto"))
            row["codigo_derivacao"] = clean_str(row.get("derivacao"))
            row["derivation_code"] = clean_str(row.get("derivacao"))
            row["codigo_lote"] = clean_str(row.get("lote"))
            row["lot_number"] = clean_str(row.get("lote"))

            # Certificado (todos os nomes possíveis) + imagens do lote
            anexos_lote = anexos_por_lote.get(clean_str(row.get("lote")), anexos_vazio)

            cert = clean_str(row.get("certificados"))
            if not cert and anexos_lote["certificate_codes"]:
                cert = ";".join(anexos_lote["certificate_codes"])

            row["certificados"] = cert
            row["codigo_certificado"] = cert
            row["certificate_code"] = cert
            row["certificado"] = cert

            row["qtd_imagens_certificados"] = anexos_lote["total"]
            row["certificados_imagens"] = anexos_lote["dados"]
            row["certificate_images"] = anexos_lote["dados"]
            row["certificados_detalhe"] = anexos_lote["certificados"]

            # Status do certificado para a tela
            lote_atual = clean_str(row.get("lote"))
            if cert:
                row["certificado_status"] = "COM_CERTIFICADO"
                row["certificado_mensagem"] = ""
            elif lote_atual:
                row["certificado_status"] = "SEM_CERTIFICADO_NO_LOTE"
                row["certificado_mensagem"] = "Sem certificado vinculado ao lote."
            else:
                row["certificado_status"] = "MOVIMENTO_SEM_LOTE"
                row["certificado_mensagem"] = "Movimento sem lote informado."

            # Operadores — operador 2 = 0/vazio vira ""
            op1 = clean_str(row.get("operador_1"))
            op2 = clean_str(row.get("operador_2"))
            nome_op1 = clean_str(row.get("nome_operador_1"))
            nome_op2 = clean_str(row.get("nome_operador_2"))
            op2_vazio = op2 in ("", "0", "0.0")

            row["operador_1"] = op1
            row["operador_2"] = "" if op2_vazio else op2
            row["nome_operador_1"] = nome_op1
            row["nome_operador_2"] = "" if op2_vazio else nome_op2

            # Aliases (snake_en + camelCase) para o front não falhar por nome
            row["operator_1"] = row["operador_1"]
            row["operator_2"] = row["operador_2"]
            row["operator_1_name"] = row["nome_operador_1"]
            row["operator_2_name"] = row["nome_operador_2"]
            row["operador1"] = row["operador_1"]
            row["operador2"] = row["operador_2"]
            row["nomeOperador1"] = row["nome_operador_1"]
            row["nomeOperador2"] = row["nome_operador_2"]

        return rows
    finally:
        conn.close()


@app.post("/api/erp/rastreabilidade-tintas")
def rastreabilidade_tintas(
    payload: RastreabilidadeTintasRequest,
    user=Depends(get_current_user),
):
    dados = query_rastreabilidade_tintas(payload)

    data_ini = parse_date_input(payload.data_inicial)
    data_fim = parse_date_input(payload.data_final)

    nome_obra = ""
    if dados:
        nome_obra = clean_str(dados[0].get("nome_obra"))

    obra = payload.obra or 0
    codigo_relatorio = clean_str(payload.codigo_relatorio)

    cabecalho = {
        "titulo": "CERTIFICADOS DE QUALIDADE DE TINTAS",
        "empresa": payload.empresa,
        "filial": payload.filial,
        "obra": obra,
        "codigo_relatorio": codigo_relatorio,
        "codigo": f"{obra}-{codigo_relatorio}" if codigo_relatorio else clean_str(obra),
        "obra_cliente": f"{obra} | {nome_obra}" if nome_obra else clean_str(obra),
        "revisao_documento": clean_str(payload.revisao_documento),
        "periodo": f"{format_date_br(data_ini)} até {format_date_br(data_fim)}",
        "listar_imagens_certificados": clean_str(payload.listar_imagens_certificados).upper(),
        "ordenacao": clean_str(payload.ordenacao).upper(),
        "gerado_em": now_iso(),
    }

    return {
        "ok": True,
        "total": len(dados),
        "cabecalho": cabecalho,
        "dados": dados,
    }


@app.get("/api/erp/certificados/lote/{codlot}/imagens")
def get_certificados_imagens_por_lote(
    codlot: str,
    codemp: int = Query(default=EMPRESA_PADRAO),
    user=Depends(get_current_user),
):
    # Busca imagens de certificado direto no ERP por lote (não depende da
    # tabela lots do Supabase) — usado pela rastreabilidade de tintas.
    conn = get_connection()
    try:
        cur = conn.cursor()
        anexos_por_lote = buscar_anexos_certificados_por_lotes(
            cur=cur, codemp=codemp, codlots=[codlot],
        )
        lote_data = anexos_por_lote.get(clean_str(codlot), {
            "lot_number": clean_str(codlot),
            "total": 0,
            "certificate_codes": [],
            "dados": [],
            "certificados": [],
        })
        return {"ok": True, **lote_data}
    finally:
        conn.close()


# =============================================================================
# LISTA DE CONJUNTOS (RDCG208.GER / USU_T900ETQ)
# =============================================================================

def query_lista_conjuntos(payload: ListaConjuntosRequest) -> tuple[list[dict[str, Any]], bool]:
    data_ini = parse_date_input(payload.data_inicial)
    data_fim = parse_date_input(payload.data_final)

    if payload.periodo_entrada and (not data_ini or not data_fim):
        data_ini, data_fim = parse_periodo_entrada(payload.periodo_entrada)

    if not data_ini or not data_fim:
        raise HTTPException(
            status_code=400,
            detail="Informe o período de entrada de estoque."
        )

    if data_ini > data_fim:
        raise HTTPException(
            status_code=400,
            detail="Data inicial não pode ser maior que a data final."
        )

    # Teto alto: a sincronização "todas as obras" facilmente passa de 5000
    # etiquetas por período (junho/2026 sozinho tem ~4100). Busca limit+1 para
    # detectar truncamento e avisar o chamador em vez de cortar em silêncio.
    # Períodos ainda maiores que o teto são pagináveis via offset.
    limit = int(payload.limit or 50000)
    limit = max(1, min(limit, 50000))
    offset = max(0, int(payload.offset or 0))

    f_obra = payload.obra or None
    f_desenho = payload.desenho or None
    codigo_barras = clean_str(payload.codigo_barras) or None
    descricao_produto = clean_str(payload.descricao_produto) or None

    # Inclui o dia final inteiro (USU_DATENT é datetime).
    data_fim_excl = data_fim + timedelta(days=1)

    sql = f"""
        SELECT
            ETQ.USU_CODEMP AS empresa,
            ETQ.USU_NUMPRJ AS obra,
            ETQ.USU_NUMDES AS desenho,
            ETQ.USU_REVDES AS revisao,

            ETQ.USU_ITEREE AS item_ree,
            ETQ.USU_ITEETQ AS item_etiqueta,
            ETQ.USU_CODBAR AS codigo_barras,

            ETQ.USU_QTDPRO AS quantidade_produto,
            ETQ.USU_USUENT AS usuario_entrada,
            ETQ.USU_DATENT AS data_entrada,
            ETQ.USU_HORENT AS hora_entrada,

            REE.USU_CODPRO AS codigo_produto,
            REE.USU_CODDER AS derivacao,
            REE.USU_DESPRO AS descricao_produto,
            NULLIF(LTRIM(RTRIM(CONVERT(varchar(100), REE.USU_BITPRO))), '') AS bitola,
            NULLIF(LTRIM(RTRIM(CONVERT(varchar(100), REE.USU_DIMPRO))), '') AS dimensao,

            PRO.DESPRO AS descricao_cadastro_produto,
            PRO.DATGER AS data_geracao_cadastro_produto,
            PRO.HORGER AS hora_geracao_cadastro_produto,
            PRO.USUGER AS usuario_geracao_cadastro_produto,
            DER.DESDER AS descricao_derivacao,

            REE.USU_QTDPRO AS quantidade_prevista,
            REE.USU_QTDEMB AS quantidade_embalagem,
            REE.USU_QTDETQ AS quantidade_etiquetas,
            REE.USU_PESREA AS peso_real,

            CAST((ISNULL(ETQ.USU_QTDPRO, 0) * ISNULL(REE.USU_PESREA, 0)) AS DECIMAL(18, 2)) AS peso_total,

            PRJ.USU_DESPRJ AS descricao_projeto,
            ULT.ultima_revisao AS ultima_revisao

        FROM USU_T900ETQ ETQ

        INNER JOIN USU_T900REE REE
            ON REE.USU_CODEMP = ETQ.USU_CODEMP
           AND REE.USU_NUMPRJ = ETQ.USU_NUMPRJ
           AND REE.USU_NUMDES = ETQ.USU_NUMDES
           AND REE.USU_REVDES = ETQ.USU_REVDES
           AND REE.USU_ITEREE = ETQ.USU_ITEREE

        LEFT JOIN E075PRO PRO
            ON PRO.CODEMP = REE.USU_CODEMP
           AND PRO.CODPRO = REE.USU_CODPRO

        LEFT JOIN E075DER DER
            ON DER.CODEMP = REE.USU_CODEMP
           AND DER.CODPRO = REE.USU_CODPRO
           AND DER.CODDER = REE.USU_CODDER

        LEFT JOIN USU_T900PRJ PRJ
            ON PRJ.USU_CODEMP = ETQ.USU_CODEMP
           AND PRJ.USU_NUMPRJ = ETQ.USU_NUMPRJ
           AND PRJ.USU_NUMDES = ETQ.USU_NUMDES
           AND PRJ.USU_REVDES = ETQ.USU_REVDES

        -- Última revisão = a que TEM etiqueta com entrada de estoque, e não a
        -- última do cadastro (USU_T900PRJ). Quando a engenharia cria uma revisão
        -- nova (ex.: B) mas os conjuntos ainda foram coletados na anterior (A),
        -- usar a revisão do cadastro descartava 100% das etiquetas (desenho some
        -- com Total=0). Aqui fazemos fallback para a revisão realmente coletada.
        OUTER APPLY (
            SELECT MAX(E2.USU_REVDES) AS ultima_revisao
              FROM USU_T900ETQ E2
             WHERE E2.USU_CODEMP = ETQ.USU_CODEMP
               AND E2.USU_NUMPRJ = ETQ.USU_NUMPRJ
               AND E2.USU_NUMDES = ETQ.USU_NUMDES
               AND E2.USU_USUENT > 0
        ) ULT

        WHERE ETQ.USU_CODEMP = ?
          AND (? IS NULL OR ETQ.USU_NUMPRJ = ?)
          AND (? IS NULL OR ETQ.USU_NUMDES = ?)
          AND ETQ.USU_USUENT > 0
          AND ETQ.USU_REVDES = ULT.ultima_revisao
          AND ETQ.USU_DATENT >= ?
          AND ETQ.USU_DATENT < ?
          AND (? IS NULL OR ETQ.USU_CODBAR = ?)
          AND (
                ? IS NULL
             OR UPPER(REE.USU_DESPRO) LIKE '%' + UPPER(?) + '%'
          )

        ORDER BY
            ETQ.USU_NUMPRJ,
            ETQ.USU_NUMDES,
            ETQ.USU_REVDES,
            REE.USU_DESPRO,
            ETQ.USU_CODBAR,
            ETQ.USU_ITEREE,
            ETQ.USU_ITEETQ
        OFFSET {offset} ROWS FETCH NEXT {limit + 1} ROWS ONLY
    """

    params = [
        payload.empresa,
        f_obra, f_obra,
        f_desenho, f_desenho,
        data_ini,
        data_fim_excl,
        codigo_barras, codigo_barras,
        descricao_produto, descricao_produto,
    ]

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = fetch_rows_dict(cur)

        truncado = len(rows) > limit
        if truncado:
            rows = rows[:limit]

        for row in rows:
            row["data_entrada_br"] = format_date_br(row.get("data_entrada"))
            row["hora_entrada_br"] = format_hora_senior(row.get("hora_entrada"))

            # Data/hora de geração do cadastro do produto (E075PRO).
            row["data_geracao_cadastro_produto_br"] = format_date_br(row.get("data_geracao_cadastro_produto"))
            row["hora_geracao_cadastro_produto_br"] = format_hora_senior(row.get("hora_geracao_cadastro_produto"))
            row["product_created_date"] = row["data_geracao_cadastro_produto_br"]
            row["product_created_at"] = row["data_geracao_cadastro_produto_br"]

            # Aliases de compatibilidade para o front-end.
            row["codigo"] = clean_str(row.get("codigo_barras"))
            row["produto"] = clean_str(row.get("codigo_produto"))
            row["descricao"] = clean_str(row.get("descricao_produto"))
            row["qtd_produto"] = row.get("quantidade_produto")
            row["qtd_embalagem"] = row.get("quantidade_embalagem")
            row["qtd_etiquetas"] = row.get("quantidade_etiquetas")

            # Bitola / dimensão — campo REAL do ERP (não inventar a partir de DESPRO).
            bitola = clean_str(row.get("bitola"))
            row["bitola"] = bitola
            row["bitola_produto"] = bitola
            row["product_gauge"] = bitola

            dimensao = clean_str(row.get("dimensao"))
            row["dimensao"] = dimensao
            row["dimensao_produto"] = dimensao
            row["product_dimension"] = dimensao

            row["descricao_cadastro_produto"] = clean_str(row.get("descricao_cadastro_produto"))
            row["descricao_derivacao"] = clean_str(row.get("descricao_derivacao"))

        return rows, truncado
    finally:
        conn.close()


@app.post("/api/erp/lista-conjuntos")
def lista_conjuntos(
    payload: ListaConjuntosRequest,
    user=Depends(get_current_user),
):
    dados, truncado = query_lista_conjuntos(payload)

    data_ini = parse_date_input(payload.data_inicial)
    data_fim = parse_date_input(payload.data_final)

    if payload.periodo_entrada and (not data_ini or not data_fim):
        data_ini, data_fim = parse_periodo_entrada(payload.periodo_entrada)

    cabecalho = {
        "titulo": "Lista de Conjuntos",
        "empresa": payload.empresa,
        "obra": payload.obra,
        "desenho": payload.desenho,
        "periodo_entrada": f"{format_date_br(data_ini)} até {format_date_br(data_fim)}",
        "codigo_barras": clean_str(payload.codigo_barras),
        "descricao_produto": clean_str(payload.descricao_produto),
        "gerado_em": now_iso(),
    }

    offset = max(0, int(payload.offset or 0))
    return {
        "ok": True,
        "total": len(dados),
        "offset": offset,
        "truncado": truncado,
        "proximo_offset": (offset + len(dados)) if truncado else None,
        "cabecalho": cabecalho,
        "dados": dados,
    }


# =============================================================================
# LISTA DE MATÉRIA-PRIMA
# =============================================================================
# A matéria-prima NÃO passa pelo coletor de etiquetas (USU_T900ETQ só tem
# conjuntos, origens 100/TKC). Por isso a lista de MP tem duas visões:
#   1) por obra/desenho (USU_T900LCM): qual MP foi aplicada em cada desenho/OP,
#      com lote e certificados;
#   2) por entrada de estoque (USU_TLOTCAB + E440): qual MP entrou por NF no
#      período, com fornecedor, lote e certificados.

def query_lista_materia_prima_obra(payload: ListaMateriaPrimaObraRequest) -> tuple[list[dict[str, Any]], bool]:
    data_ini = parse_date_input(payload.data_inicial)
    data_fim = parse_date_input(payload.data_final)
    if (data_ini and not data_fim) or (data_fim and not data_ini):
        raise HTTPException(
            status_code=400,
            detail="Informe data inicial e final juntas (ou nenhuma das duas)."
        )
    if data_ini and data_fim and data_ini > data_fim:
        raise HTTPException(
            status_code=400,
            detail="Data inicial não pode ser maior que a data final."
        )
    data_fim_excl = (data_fim + timedelta(days=1)) if data_fim else None

    limit = int(payload.limit or 50000)
    limit = max(1, min(limit, 50000))
    offset = max(0, int(payload.offset or 0))

    f_obra = payload.obra or None
    f_desenho = payload.desenho or None
    f_op = payload.op or None
    f_origem_op = clean_str(payload.origem_op) or None
    f_codcmp = clean_str(payload.codigo_componente) or None
    f_despro = clean_str(payload.descricao_produto) or None

    sql = f"""
        SELECT
            LCM.USU_CODEMP AS empresa,
            LCM.USU_NUMPRJ AS obra,
            PRJ.NOMPRJ     AS nome_projeto,
            LCM.USU_NUMDES AS desenho,
            LCM.USU_REVDES AS revisao,
            LCM.USU_CODORI AS origem_op,
            LCM.USU_NUMORP AS op,

            LCM.USU_CODCMP AS codigo_produto,
            LCM.USU_DERCMP AS derivacao,
            PRO.DESPRO     AS descricao_produto,
            PRO.CODFAM     AS familia,
            FAM.DESFAM     AS descricao_familia,
            PRO.CODORI     AS origem_produto,

            CAST(LCM.USU_CODLOT AS varchar(50)) AS lote,
            LCM.USU_DATGER AS data_lancamento,

            CERT.certificados     AS certificados,
            CERT.qtd_certificados AS qtd_certificados

        FROM USU_T900LCM LCM

        LEFT JOIN E615PRJ PRJ
            ON PRJ.CODEMP = LCM.USU_CODEMP
           AND PRJ.NUMPRJ = LCM.USU_NUMPRJ

        LEFT JOIN E075PRO PRO
            ON PRO.CODEMP = LCM.USU_CODEMP
           AND PRO.CODPRO = LCM.USU_CODCMP

        LEFT JOIN E012FAM FAM
            ON FAM.CODEMP = PRO.CODEMP
           AND FAM.CODFAM = PRO.CODFAM

        OUTER APPLY (
            SELECT
                STRING_AGG(CONVERT(varchar(100), A.USU_CODCER), ';') AS certificados,
                COUNT(DISTINCT A.USU_CODCER) AS qtd_certificados
            FROM USU_TLOTANE A
            WHERE A.USU_CODEMP = LCM.USU_CODEMP
              AND A.USU_CODLOT = TRY_CONVERT(bigint, LCM.USU_CODLOT)
              AND ISNULL(LTRIM(RTRIM(A.USU_CODCER)), '') <> ''
        ) CERT

        WHERE LCM.USU_CODEMP = ?
          AND (? IS NULL OR LCM.USU_NUMPRJ = ?)
          AND (? IS NULL OR LCM.USU_NUMDES = ?)
          AND (? IS NULL OR LCM.USU_NUMORP = ?)
          AND (? IS NULL OR LCM.USU_CODORI = ?)
          AND (? IS NULL OR LCM.USU_CODCMP = ?)
          AND (? IS NULL OR UPPER(PRO.DESPRO) LIKE '%' + UPPER(?) + '%')
          AND (? IS NULL OR LCM.USU_DATGER >= ?)
          AND (? IS NULL OR LCM.USU_DATGER < ?)

        ORDER BY
            LCM.USU_NUMPRJ,
            LCM.USU_NUMDES,
            LCM.USU_REVDES,
            LCM.USU_CODORI,
            LCM.USU_NUMORP,
            LCM.USU_CODCMP,
            LCM.USU_CODLOT
        OFFSET {offset} ROWS FETCH NEXT {limit + 1} ROWS ONLY
    """

    params = [
        payload.empresa,
        f_obra, f_obra,
        f_desenho, f_desenho,
        f_op, f_op,
        f_origem_op, f_origem_op,
        f_codcmp, f_codcmp,
        f_despro, f_despro,
        data_ini, data_ini,
        data_fim_excl, data_fim_excl,
    ]

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = fetch_rows_dict(cur)

        truncado = len(rows) > limit
        if truncado:
            rows = rows[:limit]

        for row in rows:
            row["data_lancamento_br"] = format_date_br(row.get("data_lancamento"))
            row["produto"] = clean_str(row.get("codigo_produto"))
            row["descricao"] = clean_str(row.get("descricao_produto"))
            row["codigo_componente"] = clean_str(row.get("codigo_produto"))

        return rows, truncado
    finally:
        conn.close()


@app.post("/api/erp/lista-materia-prima-obra")
def lista_materia_prima_obra(
    payload: ListaMateriaPrimaObraRequest,
    user=Depends(get_current_user),
):
    dados, truncado = query_lista_materia_prima_obra(payload)

    cabecalho = {
        "titulo": "Lista de Matéria-Prima por Obra",
        "empresa": payload.empresa,
        "obra": payload.obra,
        "desenho": payload.desenho,
        "op": payload.op,
        "origem_op": clean_str(payload.origem_op),
        "codigo_componente": clean_str(payload.codigo_componente),
        "descricao_produto": clean_str(payload.descricao_produto),
        "gerado_em": now_iso(),
    }

    offset = max(0, int(payload.offset or 0))
    return {
        "ok": True,
        "total": len(dados),
        "offset": offset,
        "truncado": truncado,
        "proximo_offset": (offset + len(dados)) if truncado else None,
        "cabecalho": cabecalho,
        "dados": dados,
    }


def query_lista_materia_prima_entrada(payload: ListaMateriaPrimaEntradaRequest) -> tuple[list[dict[str, Any]], bool]:
    data_ini = parse_date_input(payload.data_inicial)
    data_fim = parse_date_input(payload.data_final)

    if payload.periodo_entrada and (not data_ini or not data_fim):
        data_ini, data_fim = parse_periodo_entrada(payload.periodo_entrada)

    if not data_ini or not data_fim:
        raise HTTPException(
            status_code=400,
            detail="Informe o período de entrada da nota fiscal."
        )

    if data_ini > data_fim:
        raise HTTPException(
            status_code=400,
            detail="Data inicial não pode ser maior que a data final."
        )

    data_fim_excl = data_fim + timedelta(days=1)

    limit = int(payload.limit or 50000)
    limit = max(1, min(limit, 50000))
    offset = max(0, int(payload.offset or 0))

    origens = [clean_str(o).upper() for o in (payload.origens or ORIGENS_MATERIA_PRIMA) if clean_str(o)]
    if not origens:
        origens = list(ORIGENS_MATERIA_PRIMA)
    origens_marks = ",".join("?" for _ in origens)

    f_filial = payload.filial or None
    f_fornecedor = payload.fornecedor or None
    f_numnfc = payload.nota_fiscal or None
    f_familia = clean_str(payload.familia) or None
    f_codpro = clean_str(payload.codigo_produto) or None
    f_despro = clean_str(payload.descricao_produto) or None

    sql = f"""
        SELECT
            CAB.USU_CODEMP AS empresa,
            CAB.USU_CODFIL AS filial,
            CAB.USU_CODLOT AS lote,
            CAB.USU_CODFOR AS fornecedor,
            FORN.NOMFOR    AS nome_fornecedor,
            CAB.USU_NUMNFC AS nota_fiscal,
            CAB.USU_CODSNF AS serie,
            NFC.DATENT     AS data_entrada,
            IPC.NUMOCP     AS ordem_compra,

            IPC.CODPRO     AS codigo_produto,
            IPC.CODDER     AS derivacao,
            PRO.DESPRO     AS descricao_produto,
            IPC.CPLIPC     AS complemento_item,
            PRO.CODFAM     AS familia,
            FAM.DESFAM     AS descricao_familia,
            PRO.CODORI     AS origem_produto,

            IPC.QTDREC     AS quantidade_recebida,
            IPC.UNIMED     AS unidade,
            IPC.PESLIQ     AS peso_liquido,
            ITE.USU_QTDITE AS quantidade_lote,

            NULLIF(LTRIM(RTRIM(CONVERT(varchar(100), CAB.USU_CODCER))), '') AS certificado_lote,
            CERT.certificados     AS certificados,
            CERT.qtd_certificados AS qtd_certificados

        FROM USU_TLOTCAB CAB

        INNER JOIN USU_TLOTITE ITE
            ON ITE.USU_CODEMP = CAB.USU_CODEMP
           AND ITE.USU_CODLOT = CAB.USU_CODLOT

        LEFT JOIN E440NFC NFC
            ON NFC.CODEMP = CAB.USU_CODEMP
           AND NFC.CODFIL = CAB.USU_CODFIL
           AND NFC.CODFOR = CAB.USU_CODFOR
           AND NFC.NUMNFC = CAB.USU_NUMNFC
           AND NFC.CODSNF = CAB.USU_CODSNF

        LEFT JOIN E440IPC IPC
            ON IPC.CODEMP = ITE.USU_CODEMP
           AND IPC.CODFIL = ITE.USU_CODFIL
           AND IPC.CODFOR = ITE.USU_CODFOR
           AND IPC.NUMNFC = ITE.USU_NUMNFC
           AND IPC.CODSNF = ITE.USU_CODSNF
           AND IPC.SEQIPC = ITE.USU_SEQIPC

        LEFT JOIN E075PRO PRO
            ON PRO.CODEMP = IPC.CODEMP
           AND PRO.CODPRO = IPC.CODPRO

        LEFT JOIN E012FAM FAM
            ON FAM.CODEMP = PRO.CODEMP
           AND FAM.CODFAM = PRO.CODFAM

        LEFT JOIN E095FOR FORN
            ON FORN.CODFOR = CAB.USU_CODFOR

        OUTER APPLY (
            SELECT
                STRING_AGG(CONVERT(varchar(100), A.USU_CODCER), ';') AS certificados,
                COUNT(DISTINCT A.USU_CODCER) AS qtd_certificados
            FROM USU_TLOTANE A
            WHERE A.USU_CODEMP = CAB.USU_CODEMP
              AND A.USU_CODLOT = CAB.USU_CODLOT
              AND ISNULL(LTRIM(RTRIM(A.USU_CODCER)), '') <> ''
        ) CERT

        WHERE CAB.USU_CODEMP = ?
          AND (? IS NULL OR CAB.USU_CODFIL = ?)
          AND (? IS NULL OR CAB.USU_CODFOR = ?)
          AND (? IS NULL OR CAB.USU_NUMNFC = ?)
          AND NFC.DATENT >= ?
          AND NFC.DATENT < ?
          AND UPPER(ISNULL(PRO.CODORI, '')) IN ({origens_marks})
          AND (? IS NULL OR PRO.CODFAM = ?)
          AND (? IS NULL OR IPC.CODPRO = ?)
          AND (? IS NULL OR UPPER(PRO.DESPRO) LIKE '%' + UPPER(?) + '%')

        ORDER BY
            NFC.DATENT,
            CAB.USU_CODFOR,
            CAB.USU_NUMNFC,
            CAB.USU_CODLOT,
            ITE.USU_SEQIPC
        OFFSET {offset} ROWS FETCH NEXT {limit + 1} ROWS ONLY
    """

    params = [
        payload.empresa,
        f_filial, f_filial,
        f_fornecedor, f_fornecedor,
        f_numnfc, f_numnfc,
        data_ini,
        data_fim_excl,
        *origens,
        f_familia, f_familia,
        f_codpro, f_codpro,
        f_despro, f_despro,
    ]

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = fetch_rows_dict(cur)

        truncado = len(rows) > limit
        if truncado:
            rows = rows[:limit]

        for row in rows:
            row["data_entrada_br"] = format_date_br(row.get("data_entrada"))
            row["produto"] = clean_str(row.get("codigo_produto"))
            row["descricao"] = clean_str(row.get("descricao_produto"))
            row["certificado_pendente"] = is_certificado_pendente(row.get("certificado_lote"))

        return rows, truncado
    finally:
        conn.close()


@app.post("/api/erp/lista-materia-prima-entrada")
def lista_materia_prima_entrada(
    payload: ListaMateriaPrimaEntradaRequest,
    user=Depends(get_current_user),
):
    dados, truncado = query_lista_materia_prima_entrada(payload)

    data_ini = parse_date_input(payload.data_inicial)
    data_fim = parse_date_input(payload.data_final)
    if payload.periodo_entrada and (not data_ini or not data_fim):
        data_ini, data_fim = parse_periodo_entrada(payload.periodo_entrada)

    cabecalho = {
        "titulo": "Lista de Matéria-Prima por Entrada de Estoque",
        "empresa": payload.empresa,
        "filial": payload.filial,
        "fornecedor": payload.fornecedor,
        "nota_fiscal": payload.nota_fiscal,
        "periodo_entrada": f"{format_date_br(data_ini)} até {format_date_br(data_fim)}",
        "origens": [clean_str(o).upper() for o in (payload.origens or ORIGENS_MATERIA_PRIMA) if clean_str(o)],
        "familia": clean_str(payload.familia),
        "codigo_produto": clean_str(payload.codigo_produto),
        "descricao_produto": clean_str(payload.descricao_produto),
        "gerado_em": now_iso(),
    }

    offset = max(0, int(payload.offset or 0))
    return {
        "ok": True,
        "total": len(dados),
        "offset": offset,
        "truncado": truncado,
        "proximo_offset": (offset + len(dados)) if truncado else None,
        "cabecalho": cabecalho,
        "dados": dados,
    }


@app.post("/api/erp/lista-materia-prima")
def lista_materia_prima(
    payload: ListaMateriaPrimaRequest,
    user=Depends(get_current_user),
):
    """Endpoint unificado: o seletor de visão do front decide a fonte.
    visao=OBRA -> MP aplicada por obra/desenho (USU_T900LCM);
    visao=ENTRADA -> MP por entrada de estoque via NF (USU_TLOTCAB/E440)."""
    visao = clean_str(payload.visao).upper() or "OBRA"

    if visao == "OBRA":
        resultado = lista_materia_prima_obra(
            ListaMateriaPrimaObraRequest(
                empresa=payload.empresa,
                obra=payload.obra,
                desenho=payload.desenho,
                op=payload.op,
                origem_op=payload.origem_op,
                codigo_componente=payload.codigo_componente,
                descricao_produto=payload.descricao_produto,
                data_inicial=payload.data_inicial,
                data_final=payload.data_final,
                limit=payload.limit,
                offset=payload.offset,
            ),
            user=user,
        )
    elif visao == "ENTRADA":
        resultado = lista_materia_prima_entrada(
            ListaMateriaPrimaEntradaRequest(
                empresa=payload.empresa,
                filial=payload.filial,
                fornecedor=payload.fornecedor,
                nota_fiscal=payload.nota_fiscal,
                periodo_entrada=payload.periodo_entrada,
                data_inicial=payload.data_inicial,
                data_final=payload.data_final,
                origens=payload.origens,
                familia=payload.familia,
                codigo_produto=payload.codigo_produto,
                descricao_produto=payload.descricao_produto,
                limit=payload.limit,
                offset=payload.offset,
            ),
            user=user,
        )
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Visão inválida: {visao}. Use OBRA ou ENTRADA."
        )

    resultado["visao"] = visao
    resultado["cabecalho"]["visao"] = visao
    return resultado


# =============================================================================
# RASTREABILIDADE DE ITENS COMERCIAIS (USU_T900REE + E075PRO.TIPPRO='C')
# =============================================================================
# Página separada da Lista de Conjuntos: só os itens COMPRADOS
# (E075PRO.TIPPRO='C' — parafusos/porcas/arruelas etc.) previstos na Relação
# dos Elementos (USU_T900REE) da última revisão de cada desenho. Não passa por
# entrada de estoque/etiqueta; usa a quantidade PREVISTA (USU_QTDPRO).

def query_rastreabilidade_itens_comerciais(
    payload: RastreabilidadeItensComerciaisRequest,
) -> tuple[list[dict[str, Any]], bool]:
    limit = int(payload.limit or 50000)
    limit = max(1, min(limit, 50000))
    offset = max(0, int(payload.offset or 0))

    f_obra = payload.obra or None
    f_desenho = payload.desenho or None
    f_codpro = clean_str(payload.codigo_produto) or None
    f_despro = clean_str(payload.descricao_produto) or None

    sql = f"""
        SELECT
            REE.USU_CODEMP AS empresa,
            REE.USU_NUMPRJ AS obra,
            PRJ.NOMPRJ     AS nome_projeto,
            REE.USU_NUMDES AS desenho,
            REE.USU_REVDES AS revisao,
            REE.USU_ITEREE AS item_ree,

            REE.USU_CODPRO AS codigo_produto,
            REE.USU_CODDER AS derivacao,
            PRO.DESPRO     AS descricao_produto,
            REE.USU_DESPRO AS descricao_ree,
            PRO.CODFAM     AS familia,
            FAM.DESFAM     AS descricao_familia,
            PRO.TIPPRO     AS tipo_produto,
            DER.DESDER     AS descricao_derivacao,

            REE.USU_QTDPRO AS quantidade_prevista,
            REE.USU_QTDEMB AS quantidade_embalagem,
            REE.USU_QTDETQ AS quantidade_etiquetas,
            REE.USU_PESREA AS peso_unitario,
            CAST((ISNULL(REE.USU_QTDPRO, 0) * ISNULL(REE.USU_PESREA, 0)) AS DECIMAL(18, 2)) AS peso_total,

            ULT.ultima_revisao AS ultima_revisao

        FROM USU_T900REE REE

        INNER JOIN E075PRO PRO
            ON PRO.CODEMP = REE.USU_CODEMP
           AND PRO.CODPRO = REE.USU_CODPRO

        LEFT JOIN E012FAM FAM
            ON FAM.CODEMP = PRO.CODEMP
           AND FAM.CODFAM = PRO.CODFAM

        LEFT JOIN E075DER DER
            ON DER.CODEMP = REE.USU_CODEMP
           AND DER.CODPRO = REE.USU_CODPRO
           AND DER.CODDER = REE.USU_CODDER

        LEFT JOIN E615PRJ PRJ
            ON PRJ.CODEMP = REE.USU_CODEMP
           AND PRJ.NUMPRJ = REE.USU_NUMPRJ

        OUTER APPLY (
            SELECT TOP 1 P.USU_REVDES AS ultima_revisao
              FROM USU_T900PRJ P
             WHERE P.USU_CODEMP = REE.USU_CODEMP
               AND P.USU_NUMPRJ = REE.USU_NUMPRJ
               AND P.USU_NUMDES = REE.USU_NUMDES
             ORDER BY P.USU_REVDES DESC
        ) ULT

        WHERE REE.USU_CODEMP = ?
          AND (? IS NULL OR REE.USU_NUMPRJ = ?)
          AND (? IS NULL OR REE.USU_NUMDES = ?)
          AND PRO.TIPPRO = 'C'
          AND REE.USU_REVDES = ULT.ultima_revisao
          AND (? IS NULL OR REE.USU_CODPRO = ?)
          AND (? IS NULL OR UPPER(PRO.DESPRO) LIKE '%' + UPPER(?) + '%')

        ORDER BY
            REE.USU_NUMPRJ,
            REE.USU_NUMDES,
            REE.USU_REVDES,
            PRO.DESPRO,
            REE.USU_CODPRO,
            REE.USU_ITEREE
        OFFSET {offset} ROWS FETCH NEXT {limit + 1} ROWS ONLY
    """

    params = [
        payload.empresa,
        f_obra, f_obra,
        f_desenho, f_desenho,
        f_codpro, f_codpro,
        f_despro, f_despro,
    ]

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = fetch_rows_dict(cur)

        truncado = len(rows) > limit
        if truncado:
            rows = rows[:limit]

        for row in rows:
            # Descrição: o REE guarda só o código para itens comprados; a boa é
            # a do cadastro (E075PRO.DESPRO).
            desc = clean_str(row.get("descricao_produto")) or clean_str(row.get("descricao_ree"))
            row["descricao_produto"] = desc
            row["descricao"] = desc
            row["descricao_cadastro_produto"] = clean_str(row.get("descricao_produto"))
            row["descricao_ree"] = clean_str(row.get("descricao_ree"))
            row["descricao_derivacao"] = clean_str(row.get("descricao_derivacao"))

            # Aliases de compatibilidade com o front.
            row["codigo"] = clean_str(row.get("codigo_produto"))
            row["produto"] = clean_str(row.get("codigo_produto"))
            row["codigo_componente"] = clean_str(row.get("codigo_produto"))
            row["qtd_produto"] = row.get("quantidade_prevista")
            row["qtd_prevista"] = row.get("quantidade_prevista")
            row["qtd_embalagem"] = row.get("quantidade_embalagem")
            row["qtd_etiquetas"] = row.get("quantidade_etiquetas")

        return rows, truncado
    finally:
        conn.close()


@app.post("/api/erp/rastreabilidade-itens-comerciais")
def rastreabilidade_itens_comerciais(
    payload: RastreabilidadeItensComerciaisRequest,
    user=Depends(get_current_user),
):
    dados, truncado = query_rastreabilidade_itens_comerciais(payload)

    nome_projeto = clean_str(dados[0].get("nome_projeto")) if dados else ""
    obra = payload.obra or ""

    cabecalho = {
        "titulo": "RASTREABILIDADE DE ITENS COMERCIAIS",
        "empresa": payload.empresa,
        "obra": obra,
        "desenho": payload.desenho,
        "obra_cliente": f"{obra} | {nome_projeto}" if nome_projeto else clean_str(obra),
        "somente_comprados": True,
        "gerado_em": now_iso(),
    }

    return {
        "ok": True,
        "total": len(dados),
        "truncado": truncado,
        "cabecalho": cabecalho,
        "dados": dados,
    }


# =============================================================================
# DEBUG / HOMOLOGAÇÃO
# =============================================================================

@app.get("/debug/lote/{lot_key}")
def debug_lote(lot_key: str, user=Depends(get_current_user)):
    lot = lovable_find_one(TABLE_LOTS, {"lot_key": lot_key})
    docs = lovable_select(TABLE_DOCUMENTS, filters={"lot_key": lot_key}, limit=5)
    return {"lot": lot, "documents": docs}


@app.get("/debug/lovable")
def debug_lovable(user=Depends(get_current_user)):
    return {
        "lovable_db_url": LOVABLE_DB_URL,
        "table_lots": TABLE_LOTS,
        "table_documents": TABLE_DOCUMENTS,
        "table_certificates_ai": TABLE_CERTIFICATES_AI,
        "table_analysis_jobs": TABLE_ANALYSIS_JOBS,
        "table_document_comments": TABLE_DOCUMENT_COMMENTS,
        "has_key": bool(LOVABLE_DB_KEY),
        "key_prefix": LOVABLE_DB_KEY[:20] if LOVABLE_DB_KEY else "",
    }


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("certificado:app", host="0.0.0.0", port=8004, reload=True)
