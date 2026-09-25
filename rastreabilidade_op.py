# -*- coding: utf-8 -*-
"""
QualifX ERP API — extensão: lotes de matéria-prima por Ordem de Produção
(e o inverso, lote -> OPs) + modelo de produto (E700MOD). Somente leitura.

Ligação no certificado.py (3 linhas, antes do ENTRY POINT):

    import rastreabilidade_op
    rastreabilidade_op.configurar(get_connection=get_connection, get_current_user=get_current_user,
        fetch_rows_dict=fetch_rows_dict, clean_str=clean_str, format_date_br=format_date_br,
        buscar_anexos_certificados_por_lotes=buscar_anexos_certificados_por_lotes,
        empresa_padrao=EMPRESA_PADRAO)
    app.include_router(rastreabilidade_op.router)

Os helpers chegam por configurar() (não por import) para não criar ciclo de
importação com o certificado.py. Rotas:

    GET /api/erp/ordens-producao/{origem}/{op}/lotes      OP -> lotes (NF, fornecedor, certificado, imagens)
                                                         ?incluir_conjuntos=S: conjuntos da peça / peças da operação
    GET /api/erp/lotes/{codlot}/ordens-producao          lote -> OPs (recall / auditoria)
    GET /api/erp/conjuntos/{codigo}/rastreabilidade      conjunto -> peças (E700CMM) -> OP da peça -> lotes
    GET /api/erp/desenhos/{obra}/{desenho}/{rev}/rastreabilidade  desenho -> conjuntos -> peças -> OPs -> lotes
    GET /api/erp/produtos/{codpro}/modelo                modelo do produto (E075PRO.CODMOD -> E700MOD)
    GET /api/erp/modelos                                 busca paginada de modelos
    GET /api/erp/modelos/{codmod}                        detalhe do modelo

Cadeia de dados (mesmas tabelas que a rastreabilidade já usa nesta API):
    USU_T900LCM  OP + componente -> lote (USU_CODLOT varchar)
    USU_TLOTCAB  lote (bigint) -> NF de entrada (codfil/codfor/numnfc/codsnf), certificado, chave NF-e
    USU_TLOTITE  lote -> item da NF (E440IPC: produto, qtd, peso)
    USU_TLOTANE  lote -> páginas de certificado (imagens; URL via helper existente)
    E440NFC / E095FOR / E900COP / E075PRO / E700MOD / E700DMO / E700VMO / E700CMM / E710ROT / E012FAM

Regras:
- NÃO bloqueia origem 100 (ao contrário da impressão de OP): a rastreabilidade de aço vive nas origens 100/TKE.
- OP existente sem apontamento responde 200 com dados=[] (cobertura zero é informação). 404 só se a OP não existe.
- Uma linha por vínculo OP x componente x lote; vínculo apontado sem lote vem com lote="" (igual ao lista-materia-prima).
- Lote apontado mas sem cabeçalho em USU_TLOTCAB: lote_cadastrado=false (há 1 no histórico, digitado errado).
- Produto sem modelo (chapa, cantoneira comprada) responde 200 com possui_modelo=false, não 404.
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

router = APIRouter(tags=["Rastreabilidade por OP / Modelo de produto"])

_security = HTTPBearer(auto_error=True)
_cfg: dict[str, Any] = {}


def configurar(**servicos: Any) -> None:
    """Recebe do certificado.py: get_connection, get_current_user, fetch_rows_dict, clean_str,
    format_date_br, buscar_anexos_certificados_por_lotes, empresa_padrao."""
    _cfg.update(servicos)


def _svc(nome: str):
    fn = _cfg.get(nome)
    if fn is None:
        raise HTTPException(status_code=500, detail=f"rastreabilidade_op nao configurado ({nome} ausente)")
    return fn


def _dep_usuario(credentials: HTTPAuthorizationCredentials = Depends(_security)) -> dict[str, Any]:
    return _svc("get_current_user")(credentials)


def _empresa_padrao() -> int:
    return int(_cfg.get("empresa_padrao", 1))


def _clean(v: Any) -> str:
    return _svc("clean_str")(v)


def _rows(cur) -> list[dict[str, Any]]:
    return _svc("fetch_rows_dict")(cur)


def _data_br(v: Any) -> str:
    return _svc("format_date_br")(v)


SITUACAO_OP = {"E": "Explodida", "L": "Liberada", "S": "Suspensa", "F": "Finalizada", "A": "Em andamento", "C": "Cancelada"}
SITUACAO_MODELO = {"A": "Ativo", "I": "Inativo"}


def _like_contains(termo: str) -> str:
    """Escapa curingas do LIKE (sintaxe T-SQL de colchetes) e envolve com %...%."""
    escapado = termo.replace("[", "[[]").replace("%", "[%]").replace("_", "[_]")
    return f"%{escapado}%"


# =============================================================================
# LOTES: cabeçalho (NF / fornecedor / certificado), itens da NF e imagens
# =============================================================================

def _carregar_lotes(cur, codemp: int, lotes: list[str], listar_imagens: bool) -> dict[str, dict[str, Any]]:
    """Mapa lote(texto) -> {nota_fiscal, fornecedor, certificado, itens_nf[], imagens}.
    Lotes não numéricos (não existem em USU_TLOTCAB) não aparecem no mapa."""
    numericos = sorted({int(l) for l in lotes if _clean(l).isdigit()})
    if not numericos:
        return {}
    marcas = ",".join("?" for _ in numericos)
    params = [codemp, *numericos]

    cur.execute(
        f"""
        SELECT
            CONVERT(varchar(20), CAB.USU_CODLOT)                       AS lote,
            NULLIF(LTRIM(RTRIM(CONVERT(varchar(100), CAB.USU_CODCER))), '') AS certificado_lote,
            COALESCE(CAB.USU_CHVNEL, '')                               AS chave_nfe,
            CAB.USU_CODFIL                                             AS filial,
            CAB.USU_NUMNFC                                             AS nota_fiscal,
            COALESCE(CAB.USU_CODSNF, '')                               AS serie,
            NFC.DATEMI                                                 AS data_emissao,
            NFC.DATENT                                                 AS data_entrada,
            CAB.USU_CODFOR                                             AS fornecedor,
            COALESCE(FORN.NOMFOR, '')                                  AS nome_fornecedor,
            COALESCE(FORN.APEFOR, '')                                  AS apelido_fornecedor,
            FORN.CGCCPF                                                AS cnpj_fornecedor,
            CERT.certificados                                          AS certificados,
            CERT.qtd_certificados                                      AS qtd_certificados
        FROM USU_TLOTCAB CAB
        LEFT JOIN E440NFC NFC
               ON NFC.CODEMP = CAB.USU_CODEMP AND NFC.CODFIL = CAB.USU_CODFIL AND NFC.CODFOR = CAB.USU_CODFOR
              AND NFC.NUMNFC = CAB.USU_NUMNFC AND NFC.CODSNF = CAB.USU_CODSNF
        LEFT JOIN E095FOR FORN ON FORN.CODFOR = CAB.USU_CODFOR
        OUTER APPLY (
            SELECT STRING_AGG(CONVERT(varchar(100), A.USU_CODCER), ';') AS certificados,
                   COUNT(DISTINCT A.USU_CODCER) AS qtd_certificados
            FROM USU_TLOTANE A
            WHERE A.USU_CODEMP = CAB.USU_CODEMP AND A.USU_CODLOT = CAB.USU_CODLOT
              AND ISNULL(LTRIM(RTRIM(A.USU_CODCER)), '') <> ''
        ) CERT
        WHERE CAB.USU_CODEMP = ? AND CAB.USU_CODLOT IN ({marcas})
        """,
        params,
    )
    por_lote: dict[str, dict[str, Any]] = {}
    for cab in _rows(cur):
        lote = _clean(cab.pop("lote"))
        cab["data_emissao_br"] = _data_br(cab.get("data_emissao"))
        cab["data_entrada_br"] = _data_br(cab.get("data_entrada"))
        cab["qtd_certificados"] = int(cab.get("qtd_certificados") or 0)
        cab["itens_nf"] = []
        por_lote[lote] = cab

    cur.execute(
        f"""
        SELECT
            CONVERT(varchar(20), ITE.USU_CODLOT) AS lote,
            ITE.USU_SEQIPC                       AS item_nf,
            COALESCE(IPC.CODPRO, '')             AS codigo_produto,
            COALESCE(IPC.CODDER, '')             AS derivacao,
            COALESCE(PRO.DESPRO, '')             AS descricao_produto,
            COALESCE(IPC.CPLIPC, '')             AS complemento_item,
            IPC.QTDREC                           AS quantidade_recebida,
            COALESCE(IPC.UNIMED, '')             AS unidade,
            IPC.PESLIQ                           AS peso_liquido,
            ITE.USU_QTDITE                       AS quantidade_lote,
            ITE.USU_QTDETI                       AS etiquetas
        FROM USU_TLOTITE ITE
        LEFT JOIN E440IPC IPC
               ON IPC.CODEMP = ITE.USU_CODEMP AND IPC.CODFIL = ITE.USU_CODFIL AND IPC.CODFOR = ITE.USU_CODFOR
              AND IPC.NUMNFC = ITE.USU_NUMNFC AND IPC.CODSNF = ITE.USU_CODSNF AND IPC.SEQIPC = ITE.USU_SEQIPC
        LEFT JOIN E075PRO PRO ON PRO.CODEMP = IPC.CODEMP AND PRO.CODPRO = IPC.CODPRO
        WHERE ITE.USU_CODEMP = ? AND ITE.USU_CODLOT IN ({marcas})
        ORDER BY ITE.USU_CODLOT, ITE.USU_SEQIPC
        """,
        params,
    )
    for item in _rows(cur):
        lote = _clean(item.pop("lote"))
        if lote in por_lote:
            por_lote[lote]["itens_nf"].append(item)

    # Imagens de certificado: mesmo helper (uma query com chunking) da rastreabilidade de tintas.
    if listar_imagens and por_lote:
        anexos = _svc("buscar_anexos_certificados_por_lotes")(cur=cur, codemp=codemp, codlots=list(por_lote.keys()))
        for lote, cab in por_lote.items():
            info = anexos.get(lote) or {"total": 0, "dados": [], "certificados": [], "certificate_codes": []}
            cab["qtd_imagens_certificados"] = int(info.get("total") or 0)
            cab["certificados_imagens"] = info.get("dados") or []
            cab["certificados_detalhe"] = info.get("certificados") or []
    return por_lote


_CAMPOS_LOTE_VAZIOS = {
    "certificado_lote": None, "chave_nfe": "", "filial": None, "nota_fiscal": None, "serie": "",
    "data_emissao": None, "data_emissao_br": "", "data_entrada": None, "data_entrada_br": "",
    "fornecedor": None, "nome_fornecedor": "", "apelido_fornecedor": "", "cnpj_fornecedor": None,
    "certificados": None, "qtd_certificados": 0, "itens_nf": [],
}


def _linha_material(v: dict[str, Any], lotes: dict[str, dict[str, Any]], listar_imagens: bool) -> dict[str, Any]:
    """Linha de vínculo (USU_T900LCM) + detalhe do lote (ou campos vazios quando sem lote/sem cadastro)."""
    lote = _clean(v.get("lote"))
    detalhe = lotes.get(lote) if lote else None
    linha = {**v, "lote": lote, "data_lancamento_br": _data_br(v.get("data_lancamento")),
             "lote_cadastrado": detalhe is not None,
             "produto": v.get("codigo_componente", ""), "descricao": v.get("descricao_produto", "")}
    if detalhe is not None:
        linha.update(detalhe)
    else:
        linha.update(_CAMPOS_LOTE_VAZIOS)
        if listar_imagens:
            linha.update({"qtd_imagens_certificados": 0, "certificados_imagens": [], "certificados_detalhe": []})
    return linha


def _resumo_materiais(dados: list[dict[str, Any]]) -> dict[str, int]:
    com_lote = [d for d in dados if d.get("lote")]
    return {
        "vinculos": len(dados),
        "com_lote": len(com_lote),
        "sem_lote": len(dados) - len(com_lote),
        "lotes_distintos": len({d["lote"] for d in com_lote}),
        "lotes_sem_cadastro": len({d["lote"] for d in com_lote if not d.get("lote_cadastrado")}),
        "fornecedores_distintos": len({d["fornecedor"] for d in com_lote if d.get("fornecedor")}),
        "componentes_distintos": len({d.get("codigo_componente") for d in dados}),
    }


def _chunks(seq: list, n: int = 400):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


_SQL_VINCULOS_BASE = """
    SELECT
        LCM.USU_CODORI                   AS origem_op,
        LCM.USU_NUMORP                   AS op,
        LCM.USU_SEQ                      AS seq,
        LCM.USU_CODETG                   AS etapa,
        LCM.USU_SEQCMP                   AS seq_componente,
        COALESCE(LCM.USU_CODCMP, '')     AS codigo_componente,
        COALESCE(LCM.USU_DERCMP, '')     AS derivacao,
        COALESCE(PRO.DESPRO, '')         AS descricao_produto,
        COALESCE(PRO.CODFAM, '')         AS familia,
        COALESCE(FAM.DESFAM, '')         AS descricao_familia,
        COALESCE(LCM.USU_CMPSBS, '')     AS componente_substituto,
        COALESCE(LCM.USU_DERSBS, '')     AS derivacao_substituto,
        LTRIM(RTRIM(COALESCE(LCM.USU_CODLOT, ''))) AS lote,
        LCM.USU_NUMPRJ                   AS obra,
        LCM.USU_NUMDES                   AS desenho,
        COALESCE(LCM.USU_REVDES, '')     AS revisao,
        LCM.USU_DATGER                   AS data_lancamento,
        LCM.USU_USUGER                   AS usuario_lancamento
    FROM USU_T900LCM LCM
    LEFT JOIN E075PRO PRO ON PRO.CODEMP = LCM.USU_CODEMP AND PRO.CODPRO = LCM.USU_CODCMP
    LEFT JOIN E012FAM FAM ON FAM.CODEMP = PRO.CODEMP AND FAM.CODFAM = PRO.CODFAM
"""


def _materiais_por_ops(cur, codemp: int, ops: list[tuple[str, int]], listar_imagens: bool) -> dict[tuple[str, int], list[dict[str, Any]]]:
    """Vínculos de lote de várias OPs (uma query por lote de 400 OPs) + detalhe dos lotes em uma passada."""
    pares = sorted({(_clean(o), int(n)) for o, n in ops if _clean(o) and n})
    if not pares:
        return {}
    vinculos: list[dict[str, Any]] = []
    for parte in _chunks(pares):
        valores = ",".join("(?,?)" for _ in parte)
        params: list[Any] = [codemp]
        for o, n in parte:
            params += [o, n]
        cur.execute(
            _SQL_VINCULOS_BASE
            + f" JOIN (VALUES {valores}) V(ORI, NUM) ON V.ORI = LCM.USU_CODORI AND V.NUM = LCM.USU_NUMORP"
            + " WHERE LCM.USU_CODEMP = ? ORDER BY LCM.USU_NUMORP, LCM.USU_SEQ, LCM.USU_CODLOT",
            [*params[1:], codemp],
        )
        vinculos.extend(_rows(cur))
    lotes = _carregar_lotes(cur, codemp, [v["lote"] for v in vinculos if v["lote"]], listar_imagens)
    saida: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for v in vinculos:
        chave = (_clean(v.get("origem_op")), int(v.get("op") or 0))
        saida.setdefault(chave, []).append(_linha_material(v, lotes, listar_imagens))
    return saida


def _ops_por_produto(cur, codemp: int, codpros: list[str]) -> dict[str, list[dict[str, Any]]]:
    """OPs (E900COP) cujo produto é cada código informado — peças (TKE) ou conjuntos (TKC)."""
    codigos = sorted({_clean(c) for c in codpros if _clean(c)})
    saida: dict[str, list[dict[str, Any]]] = {}
    for parte in _chunks(codigos):
        marcas = ",".join("?" for _ in parte)
        cur.execute(
            f"""
            SELECT E.CODPRO AS codigo_produto, E.CODORI AS origem_op, E.NUMORP AS op,
                   COALESCE(E.SITORP, '') AS situacao, COALESCE(E.TIPORP, '') AS tipo_op,
                   E.DATGER AS data_geracao, E.DTRINI AS real_inicio, E.DTRFIM AS real_fim,
                   E.QTDPRV AS quantidade_prevista, E.QTDRE1 AS quantidade_realizada
            FROM E900COP E
            WHERE E.CODEMP = ? AND E.CODPRO IN ({marcas})
            ORDER BY E.CODPRO, E.NUMORP
            """,
            [codemp, *parte],
        )
        for o in _rows(cur):
            o["situacao_descricao"] = SITUACAO_OP.get(o["situacao"], o["situacao"])
            o["data_geracao_br"] = _data_br(o.get("data_geracao"))
            o["real_fim_br"] = _data_br(o.get("real_fim"))
            saida.setdefault(_clean(o.pop("codigo_produto")), []).append(o)
    return saida


def _pecas_dos_modelos(cur, codemp: int, codmods: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Peças (componentes) de cada modelo de conjunto — E700CMM + quantidade em E700CTM."""
    modelos = sorted({_clean(m) for m in codmods if _clean(m)})
    saida: dict[str, list[dict[str, Any]]] = {}
    for parte in _chunks(modelos):
        marcas = ",".join("?" for _ in parte)
        cur.execute(
            f"""
            SELECT C.CODMOD AS modelo, C.CODETG AS etapa, C.SEQMOD AS seq,
                   C.CODCMP AS peca, COALESCE(C.DESCMP, '') AS descricao,
                   COALESCE(P.DESPRO, '') AS descricao_cadastro, COALESCE(P.TIPPRO, '') AS tipo_produto,
                   CT.QTDUTI AS quantidade, COALESCE(CT.UNIME2, '') AS unidade
            FROM E700CMM C
            LEFT JOIN E700CTM CT ON CT.CODEMP = C.CODEMP AND CT.CODMOD = C.CODMOD AND CT.CODETG = C.CODETG AND CT.SEQMOD = C.SEQMOD
            LEFT JOIN E075PRO P ON P.CODEMP = C.CODEMP AND P.CODPRO = C.CODCMP
            WHERE C.CODEMP = ? AND C.CODMOD IN ({marcas})
            ORDER BY C.CODMOD, C.CODETG, C.SEQMOD
            """,
            [codemp, *parte],
        )
        for p in _rows(cur):
            saida.setdefault(_clean(p.pop("modelo")), []).append(p)
    return saida


def _conjuntos_da_peca(cur, codemp: int, codpro: str) -> list[dict[str, Any]]:
    """Em quais modelos (conjuntos) a peça entra como componente."""
    cur.execute(
        """
        SELECT C.CODMOD AS conjunto, COALESCE(M.DESMOD, '') AS descricao, C.CODETG AS etapa, C.SEQMOD AS seq,
               CT.QTDUTI AS quantidade, COALESCE(M.CODFAM, '') AS familia
        FROM E700CMM C
        LEFT JOIN E700MOD M ON M.CODEMP = C.CODEMP AND M.CODMOD = C.CODMOD
        LEFT JOIN E700CTM CT ON CT.CODEMP = C.CODEMP AND CT.CODMOD = C.CODMOD AND CT.CODETG = C.CODETG AND CT.SEQMOD = C.SEQMOD
        WHERE C.CODEMP = ? AND C.CODCMP = ?
        ORDER BY C.CODMOD
        """,
        [codemp, codpro],
    )
    return _rows(cur)


def _montar_pecas(cur, codemp: int, pecas: list[dict[str, Any]], listar_imagens: bool) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Para uma lista de peças: OPs de cada peça e os materiais/lotes de cada OP. Devolve (dados, resumo)."""
    ops_por_peca = _ops_por_produto(cur, codemp, [p["peca"] for p in pecas])
    todas_ops = [(o["origem_op"], o["op"]) for lst in ops_por_peca.values() for o in lst]
    materiais = _materiais_por_ops(cur, codemp, todas_ops, listar_imagens)
    dados = []
    lotes_set, forn_set = set(), set()
    pecas_com_op = pecas_com_lote = 0
    for p in pecas:
        ops = []
        tem_lote = False
        for o in ops_por_peca.get(_clean(p["peca"]), []):
            mats = materiais.get((o["origem_op"], int(o["op"])), [])
            for m in mats:
                if m.get("lote"):
                    tem_lote = True
                    lotes_set.add(m["lote"])
                    if m.get("fornecedor"):
                        forn_set.add(m["fornecedor"])
            ops.append({**o, "materiais": mats, "resumo": _resumo_materiais(mats)})
        if ops:
            pecas_com_op += 1
        if tem_lote:
            pecas_com_lote += 1
        dados.append({**p, "ops": ops})
    resumo = {"pecas": len(pecas), "pecas_com_op": pecas_com_op, "pecas_com_lote": pecas_com_lote,
              "pecas_sem_op": len(pecas) - pecas_com_op, "lotes_distintos": len(lotes_set),
              "fornecedores_distintos": len(forn_set)}
    return dados, resumo


def _desenho_do_produto(cur, codemp: int, codpro: str) -> Optional[dict[str, Any]]:
    """Obra/desenho/revisão em que o produto (conjunto) aparece na relação de elementos (última revisão)."""
    cur.execute(
        """
        SELECT TOP 1 R.USU_NUMPRJ AS obra, COALESCE(PRJ.NOMPRJ, '') AS nome_projeto,
               R.USU_NUMDES AS desenho, COALESCE(R.USU_REVDES, '') AS revisao, R.USU_ITEREE AS item_elemento,
               R.USU_QTDPRO AS quantidade_prevista, R.USU_PESREA AS peso_unitario, R.USU_QTDETQ AS qtd_etiquetas
        FROM USU_T900REE R
        LEFT JOIN E615PRJ PRJ ON PRJ.CODEMP = R.USU_CODEMP AND PRJ.NUMPRJ = R.USU_NUMPRJ
        WHERE R.USU_CODEMP = ? AND R.USU_CODPRO = ?
        ORDER BY R.USU_NUMPRJ DESC, R.USU_NUMDES DESC, R.USU_REVDES DESC
        """,
        [codemp, codpro],
    )
    linhas = _rows(cur)
    return linhas[0] if linhas else None


# =============================================================================
# 1) OP -> lotes de matéria-prima
# =============================================================================

def _cabecalho_op(cur, codemp: int, origem: str, op: int) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            C.CODEMP                 AS empresa,
            C.CODORI                 AS origem_op,
            C.NUMORP                 AS op,
            COALESCE(C.CODPRO, '')   AS codigo_produto,
            COALESCE(P.DESPRO, '')   AS descricao_produto,
            COALESCE(C.CODFAM, '')   AS familia,
            COALESCE(C.SITORP, '')   AS situacao,
            COALESCE(C.TIPORP, '')   AS tipo_op,
            C.DATGER                 AS data_geracao,
            C.DTPINI                 AS previsao_inicio,
            C.DTPFIM                 AS previsao_fim,
            C.DTRINI                 AS real_inicio,
            C.DTRFIM                 AS real_fim,
            C.QTDPRV                 AS quantidade_prevista,
            C.QTDRE1                 AS quantidade_realizada,
            C.NUMPED                 AS pedido,
            COALESCE(C.RELPRD, '')   AS relatorio_producao,
            COALESCE(C.OBSORP, '')   AS observacao
        FROM E900COP C
        LEFT JOIN E075PRO P ON P.CODEMP = C.CODEMP AND P.CODPRO = C.CODPRO
        WHERE C.CODEMP = ? AND C.CODORI = ? AND C.NUMORP = ?
        """,
        [codemp, origem, op],
    )
    linhas = _rows(cur)
    if not linhas:
        return None
    cab = linhas[0]
    cab["situacao_descricao"] = SITUACAO_OP.get(cab["situacao"], cab["situacao"])
    for campo in ("data_geracao", "previsao_inicio", "previsao_fim", "real_inicio", "real_fim"):
        cab[f"{campo}_br"] = _data_br(cab.get(campo))
    return cab


@router.get("/api/erp/ordens-producao/{origem}/{op}/lotes")
def lotes_da_ordem_producao(
    origem: str,
    op: int,
    empresa: int = Query(default=None),
    listar_imagens_certificados: str = Query(default="S"),
    incluir_conjuntos: str = Query(default="N"),
    user=Depends(_dep_usuario),
):
    """Lotes de matéria-prima consumidos por uma OP: nota fiscal, fornecedor, certificado,
    itens da nota e imagens (com arquivo_url, como na rastreabilidade de tintas).

    incluir_conjuntos=S acrescenta ao cabeçalho:
    - `conjuntos`: em quais conjuntos a peça desta OP entra (E700CMM) e as OPs desses conjuntos (TKC);
    - `operacao` + `pecas_operacao`: para OP de desenho manual (origem 100), a operação do desenho
      (USU_T900COP) e as peças/marcas cortadas nela (USU_T900QDO).
    """
    codemp = empresa or _empresa_padrao()
    origem = _clean(origem)
    if not origem:
        raise HTTPException(status_code=400, detail="Informe a origem da OP.")
    if not op or op < 1:
        raise HTTPException(status_code=400, detail="Informe o número da OP.")
    listar_imagens = _clean(listar_imagens_certificados).upper() != "N"
    com_conjuntos = _clean(incluir_conjuntos).upper() == "S"

    conn = _svc("get_connection")()
    try:
        cur = conn.cursor()
        cab_op = _cabecalho_op(cur, codemp, origem, op)
        if cab_op is None:
            raise HTTPException(status_code=404, detail="Ordem de produção não encontrada.")

        cur.execute(
            _SQL_VINCULOS_BASE
            + " WHERE LCM.USU_CODEMP = ? AND LCM.USU_CODORI = ? AND LCM.USU_NUMORP = ? ORDER BY LCM.USU_SEQ, LCM.USU_CODLOT",
            [codemp, origem, op],
        )
        vinculos = _rows(cur)
        lotes = _carregar_lotes(cur, codemp, [v["lote"] for v in vinculos if v["lote"]], listar_imagens)

        extras: dict[str, Any] = {}
        if com_conjuntos:
            # Conjuntos em que a peça desta OP entra (Tekla: OP TKE = peça; conjunto = modelo E700CMM)
            conjuntos = _conjuntos_da_peca(cur, codemp, cab_op["codigo_produto"])
            ops_conj = _ops_por_produto(cur, codemp, [c["conjunto"] for c in conjuntos])
            for c in conjuntos:
                c["ops"] = ops_conj.get(_clean(c["conjunto"]), [])
                d = _desenho_do_produto(cur, codemp, c["conjunto"])
                c["obra"] = d["obra"] if d else None
                c["nome_projeto"] = d["nome_projeto"] if d else ""
                c["desenho"] = d["desenho"] if d else None
                c["revisao"] = d["revisao"] if d else ""
            extras["conjuntos"] = conjuntos
            # Operação do desenho (origem 100) e as peças cortadas nela
            cur.execute(
                """
                SELECT TOP 1 C.USU_NUMPRJ AS obra, COALESCE(PRJ.NOMPRJ, '') AS nome_projeto, C.USU_NUMDES AS desenho,
                       COALESCE(C.USU_REVDES, '') AS revisao, C.USU_SEQOPR AS seq_operacao,
                       COALESCE(C.USU_DESOPR, '') AS descricao_operacao, COALESCE(C.USU_CODOPR, '') AS codigo_operacao
                FROM USU_T900COP C
                LEFT JOIN E615PRJ PRJ ON PRJ.CODEMP = C.USU_CODEMP AND PRJ.NUMPRJ = C.USU_NUMPRJ
                WHERE C.USU_CODEMP = ? AND C.USU_CODORI = ? AND C.USU_NUMORP = ?
                ORDER BY C.USU_REVDES DESC
                """,
                [codemp, origem, op],
            )
            oper = _rows(cur)
            extras["operacao"] = oper[0] if oper else None
            pecas_op: list[dict[str, Any]] = []
            if oper:
                o = oper[0]
                cur.execute(
                    """
                    SELECT Q.USU_ITEOPR AS item, Q.USU_DESPEC AS peca, Q.USU_QTDPEC AS quantidade,
                           COALESCE(Q.USU_CODMPR, '') AS materia_prima, COALESCE(Q.USU_DERMPR, '') AS derivacao_mp,
                           COALESCE(MPR.USU_CODSEN, '') AS codigo_materia_prima,
                           Q.USU_COMPEC AS comprimento, Q.USU_LARPEC AS largura, Q.USU_TOTKGS AS peso_total_kg,
                           COALESCE(Q.USU_CODORI, '') AS origem_op_peca, Q.USU_NUMORP AS op_peca
                    FROM USU_T900QDO Q
                    LEFT JOIN USU_T900MPR MPR ON MPR.USU_CODEMP = Q.USU_CODEMP AND MPR.USU_CODMPR = Q.USU_CODMPR AND MPR.USU_DERMPR = Q.USU_DERMPR
                    WHERE Q.USU_CODEMP = ? AND Q.USU_NUMPRJ = ? AND Q.USU_NUMDES = ? AND Q.USU_REVDES = ? AND Q.USU_SEQOPR = ?
                    ORDER BY Q.USU_ITEOPR
                    """,
                    [codemp, o["obra"], o["desenho"], o["revisao"], o["seq_operacao"]],
                )
                pecas_op = _rows(cur)
            extras["pecas_operacao"] = pecas_op
    finally:
        conn.close()

    dados = [_linha_material(v, lotes, listar_imagens) for v in vinculos]
    for d in dados:
        d.pop("origem_op", None)
        d.pop("op", None)
    resumo = _resumo_materiais(dados)
    cabecalho = {"titulo": "LOTES DE MATÉRIA PRIMA DA ORDEM DE PRODUÇÃO", **cab_op,
                 "listar_imagens_certificados": "S" if listar_imagens else "N",
                 "incluir_conjuntos": "S" if com_conjuntos else "N", "resumo": resumo}
    cabecalho.update(extras)
    return {"ok": True, "total": len(dados), "cabecalho": cabecalho, "dados": dados}


# =============================================================================
# 2) Lote -> OPs que o consumiram (recall / auditoria)
# =============================================================================

@router.get("/api/erp/lotes/{codlot}/ordens-producao")
def ordens_producao_do_lote(
    codlot: str,
    empresa: int = Query(default=None),
    listar_imagens_certificados: str = Query(default="S"),
    user=Depends(_dep_usuario),
):
    """Cabeçalho do lote (nota, fornecedor, certificado, itens, imagens) e todas as OPs que o consumiram."""
    codemp = empresa or _empresa_padrao()
    codlot = _clean(codlot)
    if not codlot:
        raise HTTPException(status_code=400, detail="Informe o código do lote.")
    listar_imagens = _clean(listar_imagens_certificados).upper() != "N"

    conn = _svc("get_connection")()
    try:
        cur = conn.cursor()
        detalhe = _carregar_lotes(cur, codemp, [codlot], listar_imagens).get(codlot)
        cur.execute(
            """
            SELECT
                LCM.USU_CODORI                   AS origem_op,
                LCM.USU_NUMORP                   AS op,
                LCM.USU_SEQ                      AS seq,
                LCM.USU_CODETG                   AS etapa,
                COALESCE(LCM.USU_CODCMP, '')     AS codigo_componente,
                COALESCE(LCM.USU_DERCMP, '')     AS derivacao,
                COALESCE(PM.DESPRO, '')          AS descricao_componente,
                COALESCE(LCM.USU_CMPSBS, '')     AS componente_substituto,
                LCM.USU_NUMPRJ                   AS obra,
                COALESCE(PRJ.NOMPRJ, '')         AS nome_projeto,
                LCM.USU_NUMDES                   AS desenho,
                COALESCE(LCM.USU_REVDES, '')     AS revisao,
                LCM.USU_DATGER                   AS data_lancamento,
                COALESCE(C.CODPRO, '')           AS codigo_produto,
                COALESCE(P.DESPRO, '')           AS descricao_produto,
                COALESCE(C.SITORP, '')           AS situacao,
                C.DATGER                         AS data_geracao_op,
                C.DTRFIM                         AS real_fim_op,
                C.NUMPED                         AS pedido,
                C.QTDPRV                         AS quantidade_prevista,
                C.QTDRE1                         AS quantidade_realizada
            FROM USU_T900LCM LCM
            LEFT JOIN E900COP C  ON C.CODEMP = LCM.USU_CODEMP AND C.CODORI = LCM.USU_CODORI AND C.NUMORP = LCM.USU_NUMORP
            LEFT JOIN E075PRO P  ON P.CODEMP = C.CODEMP AND P.CODPRO = C.CODPRO
            LEFT JOIN E075PRO PM ON PM.CODEMP = LCM.USU_CODEMP AND PM.CODPRO = LCM.USU_CODCMP
            LEFT JOIN E615PRJ PRJ ON PRJ.CODEMP = LCM.USU_CODEMP AND PRJ.NUMPRJ = LCM.USU_NUMPRJ
            WHERE LCM.USU_CODEMP = ? AND LTRIM(RTRIM(LCM.USU_CODLOT)) = ?
            ORDER BY LCM.USU_DATGER DESC, LCM.USU_NUMORP DESC, LCM.USU_SEQ
            """,
            [codemp, codlot],
        )
        ordens = _rows(cur)
    finally:
        conn.close()

    if detalhe is None and not ordens:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")

    for o in ordens:
        o["situacao_descricao"] = SITUACAO_OP.get(o["situacao"], o["situacao"])
        o["data_lancamento_br"] = _data_br(o.get("data_lancamento"))
        o["real_fim_op_br"] = _data_br(o.get("real_fim_op"))

    cabecalho: dict[str, Any] = {"titulo": "ORDENS DE PRODUÇÃO QUE CONSUMIRAM O LOTE", "empresa": codemp,
                                 "lote": codlot, "lote_cadastrado": detalhe is not None}
    cabecalho.update(detalhe if detalhe is not None else _CAMPOS_LOTE_VAZIOS)
    cabecalho["resumo"] = {
        "vinculos": len(ordens),
        "ordens_distintas": len({(o["origem_op"], o["op"]) for o in ordens}),
        "obras_distintas": len({o["obra"] for o in ordens if o["obra"]}),
        "componentes_distintos": len({o["codigo_componente"] for o in ordens}),
    }
    return {"ok": True, "total": len(ordens), "cabecalho": cabecalho, "dados": ordens}


# =============================================================================
# 2b) Conjunto -> peças -> OPs -> lotes  (Tekla: conjunto = modelo E700CMM; peça = OP TKE)
# =============================================================================

@router.get("/api/erp/conjuntos/{codigo}/rastreabilidade")
def rastreabilidade_conjunto(
    codigo: str,
    empresa: int = Query(default=None),
    listar_imagens_certificados: str = Query(default="N"),
    user=Depends(_dep_usuario),
):
    """Que aço foi para este conjunto (viga, pilar...): peças do modelo (E700CMM), OP de cada peça
    (E900COP) e os lotes apontados em cada OP (USU_T900LCM → NF, fornecedor, certificado).

    - 404 se o código não existe como produto nem como modelo.
    - Conjunto sem modelo (desenhos manuais, origem 100) responde 200 com `dados=[]` e aviso.
    - Imagens desligadas por padrão (muitas OPs por conjunto); ligue com listar_imagens_certificados=S.
    """
    codemp = empresa or _empresa_padrao()
    codigo = _clean(codigo)
    if not codigo:
        raise HTTPException(status_code=400, detail="Informe o código do conjunto.")
    listar_imagens = _clean(listar_imagens_certificados).upper() == "S"

    conn = _svc("get_connection")()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT P.CODPRO AS codigo, COALESCE(P.DESPRO, '') AS descricao, COALESCE(P.CODFAM, '') AS familia,
                   COALESCE(P.TIPPRO, '') AS tipo_produto, COALESCE(P.SITPRO, '') AS situacao,
                   COALESCE(P.CODMOD, '') AS modelo, P.PESLIQ AS peso_liquido
            FROM E075PRO P WHERE P.CODEMP = ? AND P.CODPRO = ?
            """,
            [codemp, codigo],
        )
        prod = _rows(cur)
        conjunto = prod[0] if prod else {"codigo": codigo, "descricao": "", "familia": "", "tipo_produto": "",
                                         "situacao": "", "modelo": "", "peso_liquido": None}
        codmod = _clean(conjunto.get("modelo")) or codigo
        pecas = _pecas_dos_modelos(cur, codemp, [codmod]).get(codmod, [])
        if not prod and not pecas:
            raise HTTPException(status_code=404, detail="Conjunto não encontrado (nem produto, nem modelo).")

        ops_conjunto = _ops_por_produto(cur, codemp, [codigo]).get(codigo, [])
        desenho = _desenho_do_produto(cur, codemp, codigo)
        dados, resumo = _montar_pecas(cur, codemp, pecas, listar_imagens)
    finally:
        conn.close()

    aviso = "" if pecas else "Conjunto sem modelo/estrutura no ERP (desenho manual): composição peça → conjunto não registrada."
    cabecalho = {
        "titulo": "RASTREABILIDADE DE MATÉRIA PRIMA DO CONJUNTO",
        "empresa": codemp, **conjunto, "modelo": codmod,
        "obra": desenho["obra"] if desenho else None,
        "nome_projeto": desenho["nome_projeto"] if desenho else "",
        "desenho": desenho["desenho"] if desenho else None,
        "revisao": desenho["revisao"] if desenho else "",
        "item_elemento": desenho["item_elemento"] if desenho else None,
        "quantidade_prevista": desenho["quantidade_prevista"] if desenho else None,
        "ops_conjunto": ops_conjunto,
        "listar_imagens_certificados": "S" if listar_imagens else "N",
        "aviso": aviso,
        "resumo": resumo,
    }
    return {"ok": True, "total": len(dados), "cabecalho": cabecalho, "dados": dados}


# =============================================================================
# 2c) Desenho (obra/desenho/revisão) -> conjuntos -> peças -> OPs -> lotes
# =============================================================================

@router.get("/api/erp/desenhos/{obra}/{desenho}/{revisao}/rastreabilidade")
def rastreabilidade_desenho(
    obra: int,
    desenho: int,
    revisao: str,
    empresa: int = Query(default=None),
    listar_imagens_certificados: str = Query(default="N"),
    user=Depends(_dep_usuario),
):
    """Todos os conjuntos fabricados de um desenho (USU_T900REE, TIPPRO='P') com suas peças, OPs e lotes.
    Itens comerciais (parafusos etc.) ficam fora: já existem em /api/erp/rastreabilidade-itens-comerciais."""
    codemp = empresa or _empresa_padrao()
    revisao = _clean(revisao)
    if not obra or not desenho or not revisao:
        raise HTTPException(status_code=400, detail="Informe obra, desenho e revisão.")
    listar_imagens = _clean(listar_imagens_certificados).upper() == "S"

    conn = _svc("get_connection")()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT R.USU_ITEREE AS item_elemento, R.USU_CODPRO AS codigo, COALESCE(R.USU_DESPRO, '') AS descricao,
                   COALESCE(P.DESPRO, '') AS descricao_cadastro, COALESCE(P.CODMOD, '') AS modelo,
                   R.USU_QTDPRO AS quantidade_prevista, R.USU_PESREA AS peso_unitario, R.USU_QTDETQ AS qtd_etiquetas,
                   COALESCE(PRJ.NOMPRJ, '') AS nome_projeto
            FROM USU_T900REE R
            LEFT JOIN E075PRO P ON P.CODEMP = R.USU_CODEMP AND P.CODPRO = R.USU_CODPRO
            LEFT JOIN E615PRJ PRJ ON PRJ.CODEMP = R.USU_CODEMP AND PRJ.NUMPRJ = R.USU_NUMPRJ
            WHERE R.USU_CODEMP = ? AND R.USU_NUMPRJ = ? AND R.USU_NUMDES = ? AND R.USU_REVDES = ?
              AND COALESCE(P.TIPPRO, 'P') <> 'C'
            ORDER BY R.USU_ITEREE
            """,
            [codemp, obra, desenho, revisao],
        )
        conjuntos = _rows(cur)
        if not conjuntos:
            cur.execute("SELECT TOP 1 1 FROM USU_T900PRJ WHERE USU_CODEMP = ? AND USU_NUMPRJ = ? AND USU_NUMDES = ? AND USU_REVDES = ?",
                        [codemp, obra, desenho, revisao])
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Desenho/revisão não encontrado.")

        nome_projeto = conjuntos[0]["nome_projeto"] if conjuntos else ""
        for c in conjuntos:
            c.pop("nome_projeto", None)
            c["modelo"] = _clean(c.get("modelo")) or _clean(c["codigo"])
        pecas_por_modelo = _pecas_dos_modelos(cur, codemp, [c["modelo"] for c in conjuntos])
        ops_conj = _ops_por_produto(cur, codemp, [c["codigo"] for c in conjuntos])
        todas_pecas = [dict(p, _modelo=m) for m, lst in pecas_por_modelo.items() for p in lst]
        dados_pecas, _ = _montar_pecas(cur, codemp, todas_pecas, listar_imagens)
    finally:
        conn.close()

    por_modelo: dict[str, list[dict[str, Any]]] = {}
    for p in dados_pecas:
        por_modelo.setdefault(p.pop("_modelo"), []).append(p)
    dados = []
    tot = {"conjuntos": len(conjuntos), "conjuntos_com_modelo": 0, "pecas": 0, "pecas_com_lote": 0, "lotes_distintos": set()}
    for c in conjuntos:
        pecas = por_modelo.get(c["modelo"], [])
        if pecas:
            tot["conjuntos_com_modelo"] += 1
        tot["pecas"] += len(pecas)
        for p in pecas:
            if any(m.get("lote") for o in p["ops"] for m in o["materiais"]):
                tot["pecas_com_lote"] += 1
            for o in p["ops"]:
                for m in o["materiais"]:
                    if m.get("lote"):
                        tot["lotes_distintos"].add(m["lote"])
        dados.append({**c, "ops_conjunto": ops_conj.get(_clean(c["codigo"]), []), "pecas": pecas})
    tot["lotes_distintos"] = len(tot["lotes_distintos"])
    return {
        "ok": True,
        "total": len(dados),
        "cabecalho": {"titulo": "RASTREABILIDADE DE MATÉRIA PRIMA DO DESENHO", "empresa": codemp,
                      "obra": obra, "nome_projeto": nome_projeto, "desenho": desenho, "revisao": revisao,
                      "listar_imagens_certificados": "S" if listar_imagens else "N", "resumo": tot},
        "dados": dados,
    }


# =============================================================================
# 3) Modelo de produto (E700MOD)
# =============================================================================

_SQL_MODELO = """
    SELECT
        M.CODMOD                           AS codigo_modelo,
        COALESCE(M.DESMOD, '')             AS descricao_modelo,
        COALESCE(M.CODFAM, '')             AS familia,
        COALESCE(FM.DESFAM, '')            AS descricao_familia,
        COALESCE(M.CODMDP, '')             AS mascara_derivacao,
        M.QTDBAS                           AS quantidade_base,
        COALESCE(M.UNIMED, '')             AS unidade,
        M.QTDMAX                           AS quantidade_maxima,
        M.TOLQMX                           AS tolerancia_maxima,
        COALESCE(M.CODROT, '')             AS roteiro,
        COALESCE(R.DESROT, '')             AS descricao_roteiro,
        COALESCE(R.SITROT, '')             AS situacao_roteiro,
        COALESCE(M.CODCRE, '')             AS centro_recurso,
        M.FILPRD                           AS filial_producao,
        COALESCE(M.SITMOD, '')             AS situacao,
        COALESCE(M.VERMOD, '')             AS versao,
        M.DATGER                           AS data_criacao,
        M.DATALT                           AS data_alteracao,
        (SELECT COUNT(*)               FROM E700CMM C WHERE C.CODEMP = M.CODEMP AND C.CODMOD = M.CODMOD) AS qtd_componentes,
        (SELECT COUNT(DISTINCT CODETG) FROM E700CMM C WHERE C.CODEMP = M.CODEMP AND C.CODMOD = M.CODMOD) AS qtd_etapas,
        (SELECT COUNT(*)               FROM E700DMO D WHERE D.CODEMP = M.CODEMP AND D.CODMOD = M.CODMOD) AS qtd_derivacoes,
        (SELECT COUNT(*)               FROM E700VMO V WHERE V.CODEMP = M.CODEMP AND V.CODMOD = M.CODMOD) AS qtd_versoes,
        (SELECT COUNT(*)               FROM E075PRO P WHERE P.CODEMP = M.CODEMP AND P.CODMOD = M.CODMOD) AS qtd_produtos_que_usam
    FROM E700MOD M
    LEFT JOIN E710ROT R  ON R.CODEMP = M.CODEMP AND R.CODROT = M.CODROT
    LEFT JOIN E012FAM FM ON FM.CODEMP = M.CODEMP AND FM.CODFAM = M.CODFAM
    WHERE M.CODEMP = ? AND M.CODMOD = ?
"""


def _carregar_modelo(cur, codemp: int, codmod: str) -> Optional[dict[str, Any]]:
    cur.execute(_SQL_MODELO, [codemp, codmod])
    linhas = _rows(cur)
    if not linhas:
        return None
    m = linhas[0]
    m["situacao_descricao"] = SITUACAO_MODELO.get(m["situacao"], m["situacao"])
    m["data_criacao_br"] = _data_br(m.get("data_criacao"))
    m["data_alteracao_br"] = _data_br(m.get("data_alteracao"))

    cur.execute(
        """
        SELECT COALESCE(D.CODDER, '') AS derivacao, D.SEQCMD AS sequencia, D.PESLIQ AS peso_liquido,
               D.PESBRU AS peso_bruto, D.PRECUS AS preco_custo, D.DATALT AS data_alteracao
        FROM E700DMO D WHERE D.CODEMP = ? AND D.CODMOD = ? ORDER BY D.CODDER
        """,
        [codemp, codmod],
    )
    m["derivacoes"] = _rows(cur)
    cur.execute(
        """
        SELECT COALESCE(V.VERMOD, '') AS versao, V.DATALT AS data_alteracao, V.DATGER AS data_criacao,
               V.QTDBAS AS quantidade_base, COALESCE(V.CODROT, '') AS roteiro
        FROM E700VMO V WHERE V.CODEMP = ? AND V.CODMOD = ? ORDER BY V.DATALT DESC, V.VERMOD DESC
        """,
        [codemp, codmod],
    )
    m["historico_versoes"] = _rows(cur)
    for v in m["historico_versoes"]:
        v["data_alteracao_br"] = _data_br(v.get("data_alteracao"))
    return m


@router.get("/api/erp/produtos/{codpro}/modelo")
def modelo_do_produto(codpro: str, empresa: int = Query(default=None), user=Depends(_dep_usuario)):
    """Modelo de engenharia (E700MOD) do produto. Produto sem modelo -> 200 com possui_modelo=false."""
    codemp = empresa or _empresa_padrao()
    codpro = _clean(codpro)
    if not codpro:
        raise HTTPException(status_code=400, detail="Informe o código do produto.")

    conn = _svc("get_connection")()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                P.CODPRO                 AS codigo_produto,
                COALESCE(P.DESPRO, '')   AS descricao_produto,
                COALESCE(P.CODFAM, '')   AS familia,
                COALESCE(FM.DESFAM, '')  AS descricao_familia,
                COALESCE(P.CODORI, '')   AS origem_produto,
                COALESCE(P.UNIMED, '')   AS unidade,
                COALESCE(P.TIPPRO, '')   AS tipo_produto,
                COALESCE(P.SITPRO, '')   AS situacao,
                COALESCE(P.CODMOD, '')   AS codigo_modelo,
                COALESCE(P.CODROT, '')   AS roteiro,
                COALESCE(P.GERORP, '')   AS gera_op,
                COALESCE(P.BXAORP, '')   AS baixa_por_op,
                P.PESLIQ                 AS peso_liquido,
                P.PESBRU                 AS peso_bruto
            FROM E075PRO P
            LEFT JOIN E012FAM FM ON FM.CODEMP = P.CODEMP AND FM.CODFAM = P.CODFAM
            WHERE P.CODEMP = ? AND P.CODPRO = ?
            """,
            [codemp, codpro],
        )
        linhas = _rows(cur)
        if not linhas:
            raise HTTPException(status_code=404, detail="Produto não encontrado.")
        produto = linhas[0]
        codmod = _clean(produto.get("codigo_modelo"))
        modelo = _carregar_modelo(cur, codemp, codmod) if codmod else None
    finally:
        conn.close()

    if modelo is not None:
        aviso = ""
    elif not codmod:
        aviso = "Produto sem modelo cadastrado (item comprado / sem estrutura)."
    else:
        aviso = f"Produto aponta para o modelo {codmod}, que não existe na E700MOD."
    return {
        "ok": True,
        "produto": produto,
        "possui_modelo": modelo is not None,
        "modelo_igual_ao_produto": bool(codmod) and codmod == codpro,
        "aviso": aviso,
        "modelo": modelo,
    }


@router.get("/api/erp/modelos")
def lista_modelos(
    q: str = Query(default=""),
    familia: str = Query(default=""),
    mascara_derivacao: str = Query(default=""),
    situacao: str = Query(default=""),
    empresa: int = Query(default=None),
    limit: int = Query(default=100),
    offset: int = Query(default=0),
    user=Depends(_dep_usuario),
):
    """Busca paginada de modelos (E700MOD) para combobox: q em código/descrição (ignora acentos)."""
    codemp = empresa or _empresa_padrao()
    limit = max(1, min(int(limit or 100), 1000))
    offset = max(0, int(offset or 0))
    condicoes = ["M.CODEMP = ?"]
    params: list[Any] = [codemp]
    q = _clean(q)
    if q:
        condicoes.append("(M.CODMOD LIKE ? OR M.DESMOD COLLATE Latin1_General_CI_AI LIKE ?)")
        params += [_like_contains(q), _like_contains(q)]
    if _clean(familia):
        condicoes.append("M.CODFAM = ?"); params.append(_clean(familia))
    if _clean(mascara_derivacao):
        condicoes.append("M.CODMDP = ?"); params.append(_clean(mascara_derivacao).upper())
    if _clean(situacao):
        condicoes.append("M.SITMOD = ?"); params.append(_clean(situacao).upper())

    conn = _svc("get_connection")()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT
                M.CODMOD                AS codigo_modelo,
                COALESCE(M.DESMOD, '')  AS descricao_modelo,
                COALESCE(M.CODFAM, '')  AS familia,
                COALESCE(FM.DESFAM, '') AS descricao_familia,
                COALESCE(M.CODMDP, '')  AS mascara_derivacao,
                COALESCE(M.UNIMED, '')  AS unidade,
                COALESCE(M.CODROT, '')  AS roteiro,
                COALESCE(M.SITMOD, '')  AS situacao,
                COALESCE(M.VERMOD, '')  AS versao,
                M.DATALT                AS data_alteracao
            FROM E700MOD M
            LEFT JOIN E012FAM FM ON FM.CODEMP = M.CODEMP AND FM.CODFAM = M.CODFAM
            WHERE {' AND '.join(condicoes)}
            ORDER BY M.CODMOD
            OFFSET {offset} ROWS FETCH NEXT {limit + 1} ROWS ONLY
            """,
            params,
        )
        dados = _rows(cur)
    finally:
        conn.close()

    truncado = len(dados) > limit
    if truncado:
        dados = dados[:limit]
    for d in dados:
        d["situacao_descricao"] = SITUACAO_MODELO.get(d["situacao"], d["situacao"])
        d["data_alteracao_br"] = _data_br(d.get("data_alteracao"))
    return {
        "ok": True,
        "total": len(dados),
        "offset": offset,
        "truncado": truncado,
        "proximo_offset": (offset + len(dados)) if truncado else None,
        "cabecalho": {"titulo": "MODELOS DE PRODUTO", "empresa": codemp, "q": q,
                      "familia": _clean(familia), "mascara_derivacao": _clean(mascara_derivacao).upper(),
                      "situacao": _clean(situacao).upper()},
        "dados": dados,
    }


@router.get("/api/erp/modelos/{codmod}")
def detalhe_modelo(codmod: str, empresa: int = Query(default=None), user=Depends(_dep_usuario)):
    """Detalhe de um modelo: capa, roteiro, derivações, histórico de versões e contagens."""
    codemp = empresa or _empresa_padrao()
    codmod = _clean(codmod)
    if not codmod:
        raise HTTPException(status_code=400, detail="Informe o código do modelo.")
    conn = _svc("get_connection")()
    try:
        modelo = _carregar_modelo(conn.cursor(), codemp, codmod)
    finally:
        conn.close()
    if modelo is None:
        raise HTTPException(status_code=404, detail="Modelo não encontrado na E700MOD.")
    return {"ok": True, "modelo": modelo}
