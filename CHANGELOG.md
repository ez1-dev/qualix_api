# CHANGELOG — QualifX ERP API (`certificado.py`)

Histórico consolidado das alterações feitas na API Python/FastAPI que integra o
ERP Senior (SQL Server) com o Supabase/Lovable.

> **Servidor de certificados (imagens):** `\\Ezortea-srvseni\sgq\CERTIFQ`
> **Regra LSP do ERP:** `USU_CAMDOC` é gravado **sem** `.JPG`; o ERP acrescenta `.JPG` ao abrir.

---

## Visão geral das mudanças por área

| Área | O que mudou |
|------|-------------|
| Descrições produto × NF | Campos separados (`product_description` × `invoice_item_description`) |
| Imagens/anexos do ERP | Endpoints para servir JPG e listar anexos por lote |
| Certificado via anexo | Lê `USU_TLOTANE` além do cabeçalho `USU_TLOTCAB` |
| Campos amigáveis | `product_code`, `origin_code`, `family_code`, `derivation_code` |
| Múltiplos certificados/páginas | Endpoint de aprovação múltipla + resumo por certificado |
| Sincronização automática | APScheduler diário às 20:00 + endpoint de status |
| Robustez | ERP como etapa principal; falha no Supabase vira `warning`, não 500 |
| Rastreabilidade de Matéria Prima | Nova consulta em `USU_T900LCM` + endpoint |

---

## 1. Descrição do produto × descrição da NF

**Problema:** a query misturava a descrição do cadastro do produto (`PRO.DESPRO`)
com o complemento da nota fiscal (`IPC.CPLIPC`).

**Mudanças (`query_erp_lotes_exicer` / `build_lots_payload`):**
- `PRODUCT_DESCRIPTION` = `COALESCE(PRO.DESPRO, '')`
- Novo `INVOICE_ITEM_DESCRIPTION` = `COALESCE(IPC.CPLIPC, '')`
- Payload do lote passou a gravar `invoice_item_description`
- `/api/erp/certificados/search` agora também pesquisa por `invoice_item_description`

**Novo endpoint:**
- `GET /api/erp/certificados-qualidade` — alias com filtro `status_app` e busca livre `q`
  (lote, certificado, NF, fornecedor, produto, família, origem, descrições, ordem de compra)

**Coluna nova no Supabase (`lots`):**
```sql
alter table public.lots add column if not exists invoice_item_description text;
```

---

## 2. Imagens / anexos do ERP

**Helpers novos:**
- `safe_camdoc(value)` — limpa o nome, remove extensão e bloqueia path traversal (`..`, `/`, `\`)
- `resolve_certifq_jpg_path(camdoc, must_exist)` — monta `<FINAL_SERVER_BASE_PATH>\<camdoc>.JPG`

**Endpoints novos:**
- `GET /api/erp/certificados/arquivo/{camdoc}` — serve o JPG via `FileResponse` (exige Bearer)
- `GET /api/erp/lotes/{lot_key}/anexos-erp` — lê `USU_TLOTANE` e retorna todos os anexos

> O `.env` já apontava para `\\Ezortea-srvseni\sgq\CERTIFQ` (nenhuma mudança necessária).

---

## 3. Certificado também vindo dos anexos (`USU_TLOTANE`)

**Problema:** o número do certificado era lido só de `USU_TLOTCAB.USU_CODCER`,
mas ele também fica em `USU_TLOTANE.USU_CODCER` (e o arquivo em `USU_TLOTANE.USU_CAMDOC`).

**Mudanças:**
- `query_erp_lotes_exicer`: `OUTER APPLY` em `USU_TLOTANE` (primeiro anexo com certificado)
  trazendo `ERP_ANE_CODCER` e `ERP_ANE_CAMDOC`
- `build_lots_payload`: fallback — se o cabeçalho estiver pendente mas o anexo tiver código,
  usa o do anexo. Novos campos: `erp_anexo_codcer`, `erp_camdoc`

**Colunas novas no Supabase (`lots`):**
```sql
alter table public.lots add column if not exists erp_anexo_codcer text;
alter table public.lots add column if not exists erp_camdoc text;
```

---

## 4. Campos amigáveis (produto / origem / família / derivação)

**Mudanças:**
- Query com `COALESCE(...)` em todos os campos de produto/origem/família (nunca `NULL`)
- `build_lots_payload` grava pares amigáveis além dos `erp_*`:
  `product_code`, `derivation_code`, `family_code`, `origin_code`

**Diagnóstico:** confirmado via dados reais que a API entrega esses campos preenchidos —
quando não aparecem na tela, a causa é mapeamento no front-end ou dados não re-sincronizados.

**Colunas novas no Supabase (`lots`):**
```sql
alter table public.lots add column if not exists product_code text;
alter table public.lots add column if not exists derivation_code text;
alter table public.lots add column if not exists family_code text;
alter table public.lots add column if not exists origin_code text;
```

---

## 5. Múltiplos certificados e páginas por lote

**Regra:** 1 lote pode ter N certificados; 1 certificado pode ter N páginas.
Cada página = 1 linha em `USU_TLOTANE`. A verdade completa vem de `/anexos-erp`.

**Mudanças:**
- `build_final_file_name`: paginação com 2 dígitos (`-01`, `-02`, …)
- `convert_document_to_jpg_pages(..., start_page=1)`: novo offset para numeração **contínua**
  entre vários documentos do mesmo certificado
- `upsert_erp_certificate_pages`: `DELETE` por `USU_CODCER` (não por lote) — não apaga outros
  certificados; `UPDATE USU_TLOTCAB` só preenche o cabeçalho **se vazio/pendente**
- `/api/erp/lotes/{lot_key}/anexos-erp`: retorna todas as páginas + bloco `certificados`
  agrupado por código (`certificate_code`, `pages_count`, `pages`) + `certificates_count`
- Helper `summarize_lot_certificates(conn, lot)` — resumo real do lote (códigos, contagem,
  páginas, primeiro camdoc), coerente com o ERP mesmo em lote multi-certificado

**Novo modelo + endpoint:**
- `ApproveMultipleRequest` (`lot_key`, `document_ids[]`, `certificate_code`, `approved_by`, `notes`)
- `POST /api/erp/certificados/aprovar-multiplos` — aprova N imagens/documentos como **um**
  certificado, numerando páginas `01, 02, 03…` e fazendo **um único** upsert no ERP

**Colunas novas no Supabase (`lots`):**
```sql
alter table public.lots add column if not exists certificate_code text;
alter table public.lots add column if not exists certificate_codes jsonb default '[]'::jsonb;
alter table public.lots add column if not exists certificate_count integer default 0;
alter table public.lots add column if not exists certificate_pages_count integer default 0;
```

---

## 6. Robustez na aprovação (ERP é a etapa principal)

**Problema:** o ERP gravava certo, mas se o `patch` no Supabase falhasse
(ex.: `PGRST204 — coluna inexistente`), a API retornava **500** mesmo com o ERP OK.

**Mudanças em `approve_certificate` e `approve_certificate_multiple`:**
- Etapa **ERP** (principal): falha → `500` real
- Etapa **Supabase** (secundária): falha → campo `"warning"` no JSON, sem 500
- Campos-resumo gravados a partir de `summarize_lot_certificates` (count/pages/códigos reais)
- `erp_codcer` no Supabase espelha o cabeçalho do ERP (só grava se vazio/pendente);
  `certificate_code` = último aprovado (resumo)
- `build_lots_payload` (sync) também popula `certificate_count` / `certificate_pages_count`
  via subqueries agregadas em `USU_TLOTANE`

> Observação: o `alterar` (`/api/erp/certificados/alterar`) continua sobrescrevendo
> o certificado de propósito — é a ação explícita de troca.

---

## 7. Sincronização automática (APScheduler)

**Dependência nova:** `apscheduler` (em `requirements.txt`)

**Mudanças:**
- `run_lotes_sync(...)` — rotina reutilizável (manual + agendado usam a mesma lógica)
- `POST /api/erp/lotes/sync` — agora chama `run_lotes_sync` com origem `MANUAL:<usuario>`
- `scheduler` (BackgroundScheduler) + `scheduled_lotes_sync()` (origem `AGENDADO_20H`)
- Eventos `startup`/`shutdown` para iniciar/parar o scheduler
- `GET /api/erp/lotes/sync/status` — estado do agendamento + próxima execução

**Config nova no `.env`:**
```env
SYNC_SCHEDULE_ENABLED=true
SYNC_HOUR=20
SYNC_MINUTE=0
SYNC_TIMEZONE=America/Sao_Paulo
```

> **Produção:** rodar **sem** `--reload` e com **1 worker** (reload/múltiplos workers
> duplicam o job e a sync rodaria mais de uma vez às 20h).

### 7.1. Lotes pendentes + limite do sync

**Problema:** lotes com `USU_TLOTCAB.USU_CODCER = 'Certificado pendente'` não apareciam
quando o produto/OC não tinham `USU_EXICER='S'` (ex.: NF 650066, lote 26000419, TINTA00610).

**Mudanças:**
- `query_erp_lotes_exicer` — `WHERE` inclui produto/OC exige cert (com `ISNULL`), **família
  TINTAS** e lotes com **certificado pendente** no cabeçalho:
  `ISNULL(PRO.USU_EXICER,'N')='S' OR ISNULL(OCP.USU_EXICER,'N')='S' OR PRO.CODFAM='TINTAS'
  OR CAB.USU_CODCER IS NULL OR ...='' OR UPPER(...) IN ('PENDENTE','CERTIFICADO PENDENTE')`.
- **`ERP_SYNC_REASON`** (SELECT) + **`sync_reason`** (payload) — motivo da inclusão do lote
  para diagnóstico: `PRODUTO_EXIGE_CERTIFICADO` / `OC_EXIGE_CERTIFICADO` / `FAMILIA_TINTAS` /
  `CERTIFICADO_PENDENTE_ERP`.
- **Limite do sync aumentado** — conjunto completo (com TINTAS) = **15.591 linhas → 14.175
  lotes** (PRODUTO 7.335, TINTAS 3.628, PENDENTE 2.402, OC 810). `limit=5000` perdia milhares.
  Endpoint manual: `default=30000, le=50000`. Sync agendado: `limit=30000`. Query ~0,4s.
- **Upsert em lotes (chunking de 500)** no `lovable_upsert_many` — evita POST gigante ao
  sincronizar 14k lotes.

> Validado: NF 650066/for 30 → lotes 26000419 e 26000420 capturados (`sync_reason=FAMILIA_TINTAS`).
> Botão "Sincronizar ERP" no Lovable: `POST /api/erp/lotes/sync` (default já é 30000).
> ⚠️ A tabela `lots` do Supabase passa a ter ~14k lotes — garantir as colunas (SQL consolidado).

---

## 8. Rastreabilidade de Matéria Prima (`USU_T900LCM`)

**Novo modelo + endpoint:**
- `RastreabilidadeMateriaPrimaRequest`
- `POST /api/erp/rastreabilidade-materia-prima` — cabeçalho + dados

**Funções:**
- `get_titulo_rastreabilidade(relatorio)` — título por relatório (1/2/3)
- `validar_ordenacao_rastreabilidade(relatorio, ordenacao)` —
  rel.1 → `PROJETO`; rel.2 e 3 → `FAMILIA`
- `query_rastreabilidade_materia_prima(payload)` — consulta em `USU_T900LCM` com joins:
  - `E075PRO` (descrição/família do produto)
  - `E012FAM` (descrição da família)
  - `E615PRJ` (nome do projeto)
  - `OUTER APPLY USU_TLOTANE` (certificados do lote, via `STRING_AGG`)
  - `OUTER APPLY USU_T900COP/QDO/MPR` (posições no desenho, se `listar_posicao_desenho = S`)
  - `OUTER APPLY USU_T900LCM (SUBST)` — componente substituído (espelha `ComponenteSubstituido()`):
    se o mesmo lote real aparece em outro componente, usa o original para achar a posição.
    Campos novos: `codcmp_substituido`, `dercmp_substituido`, `componente_substituido` (S/N)

**Filtros avançados (da tela e615prj / u900lcm):**
- `numdes` (número do desenho) → `LCM.USU_NUMDES`
- `codori` (origem) → `LCM.USU_CODORI`
- `numorp` (número da OP) → `LCM.USU_NUMORP`
- Valores vazios (`0` / `""`) são normalizados para `None` e **não filtram**
- `Obra` e `Projeto` colapsam em um único filtro `USU_NUMPRJ` (`projeto or obra`)

**Nomes finais dos campos no retorno (`dados[]`) — alinhados ao Lovable:**
`projeto`, `nome_projeto`, `desenho`, `revisao_desenho`, `origem`, `op`,
`codigo_componente`, `derivacao`, `descricao_produto`, `familia`, `descricao_familia`,
`lote`, `certificados`, `posicao_desenho` (singular).
Também retorna `posicoes_desenho` (plural) como **espelho** de `posicao_desenho`,
para compatibilidade de nome com o front-end (evita coluna vazia por divergência singular/plural).
Extras: `qtd_certificados`, `componente_substituido` (S/N),
`codigo_componente_original`, `derivacao_original`, `descricao_produto_original`.

> **Decisão de arquitetura:** a posição é calculada via `OUTER APPLY` (1 query total),
> NÃO por busca linha-a-linha em Python. O modelo per-row seria N+1 (~10.000 queries no
> projeto 640 / 5000 linhas) e perderia o guard de lote vazio. O `OUTER APPLY` já entrega
> 337/350 (665) e 4706/5000 (640) em ~0,7s. Se a posição não aparece na tela, é nome de
> campo no front (`posicao_desenho || posicoes_desenho`) ou projeto sem `GravarLotesMatPrima`.
Quando há substituição, `descricao_produto` vira `"<orig> - <cod> *** substituído por *** <atual> - <cod>"`.

**Notas de implementação (correções sobre o modelo original):**
- `USU_T900LCM` **não tem coluna de filial** → o filtro de filial não é aplicado
  (campo aceito no payload por compatibilidade, mas ignorado no `WHERE`)
- `Obra` e `Projeto` filtram `USU_NUMPRJ` (ponto inicial; ajustável)
- **A tabela `USU_T900LCM` é alimentada pela rotina interna do Senior `GravarLotesMatPrima`
  (LSP), disparada na tela vinculada a `E615PRJ`.** Essa rotina NÃO é chamável pela FastAPI
  (é interna do ERP, sem web service/procedure exposto). Por isso a API é **somente leitura**
  da `USU_T900LCM`; a atualização/geração dos lotes continua sendo feita pelo ERP.
- Junção dos certificados usa `TRY_CONVERT(bigint, LCM.USU_CODLOT)` porque
  `USU_T900LCM.usu_codlot` é `varchar` e `USU_TLOTANE.usu_codlot` é `bigint`
- `STRING_AGG` validado (SQL Server 2019 / v15); posições com `WITHIN GROUP (ORDER BY USU_DESPEC)`
- **Guard de lote vazio no SUBST:** sem ele, linhas de lote vazio (`''`) casariam entre si,
  gerando substituições FALSAS e **posições erradas**. Com o guard, projeto 665 = 0 substituições
  e 337/350 posições (idêntico ao correto). `usu_cmpsbs` está vazio em toda a base, então a
  substituição vem do cenário de mesmo lote em componente diferente.

> **Performance:** sempre enviar `obra` ou `projeto` (a tabela tem ~46k linhas + 2 OUTER APPLY).
> Se ficar pesado, avaliar cache no Supabase.

---

## 9. Gravar lotes de matéria-prima na OP (`E900EOQ.USU_CODLOT`)

Espelha a ação `E900COP.AGrvLot` do ERP, em **modo seguro**.

**Novo modelo + endpoint:**
- `GravarLotesOpRequest` (`empresa`, `origem`, `op`, `executar`, `usar_derivacao`,
  `criar_apontamento_se_necessario`)
- `POST /api/erp/rastreabilidade-materia-prima/gravar-lotes-op` (exige **admin**)

**Lógica (igual ao relatório, sem a parte de INSERT):**
1. Lê lotes já apontados na OP (`E900EOQ.USU_CODLOT`)
2. Percorre componentes da OP (`E900CMO`)
3. Para cada um, verifica se já tem lote vinculado (`USU_TLOTITE` + `E440IPC`)
4. Se não, busca o lote mais novo recebido (`E440NFC.DATENT DESC`) — sentinela `999999999999999` se nenhum
5. Procura linha de `E900EOQ` com `USU_CODLOT` vazio e **atualiza** (apenas se `executar=true`)

**Segurança:**
- `executar=false` (padrão) → simula tudo e faz **ROLLBACK** (não grava nada)
- `executar=true` → grava só via **UPDATE** em linha com lote vazio
- **NÃO cria apontamento novo (INSERT)** — a regra Senior original criaria; aqui retorna
  `acao: "sem_linha_livre"` com aviso. Criação automática fica para depois da validação.
- `require_admin` no endpoint (escrita em produção)

> **Correção sobre o código original:** o pyodbc devolve atributos de `Row` no case real do
> banco (minúsculo). O código acessava `linha_livre.CODETG`/`.SEQEOQ` (maiúsculo) → `AttributeError`.
> Resolvido aliasando as colunas para minúsculas no `SELECT`.

> **Nota de simulação:** em `executar=false` (rollback), vários componentes podem aparecer
> apontando para a mesma linha livre. Em `executar=true` cada `UPDATE` ocupa a linha dentro da
> mesma transação, então o próximo componente pega a linha seguinte (comportamento correto).

---

## 10. Data de entrada da nota fiscal (`E440NFC.DATENT`)

**Mudanças:**
- Helper `to_iso_date()` — converte `datetime`/`date`/str para ISO `YYYY-MM-DD` (ou `None`)
- `query_erp_lotes_exicer`: `LEFT JOIN E440NFC` (nas chaves de `CAB`) + `NFC.DATENT AS ERP_DATENT`
- `build_lots_payload`: grava `erp_datent` e `invoice_entry_date` (ISO date)

**Colunas novas no Supabase (`lots`):**
```sql
alter table public.lots add column if not exists erp_datent date;
alter table public.lots add column if not exists invoice_entry_date date;
```

> Validado: 500/500 lotes com `erp_datent` em ISO. Necessário **re-sincronizar** para
> preencher os lotes existentes.

---

## 11. Robustez de conexões (Windows / file descriptors)

**Sintoma:** `ValueError: too many file descriptors in select()` — o `SelectorEventLoop`
do asyncio no Windows limita ~512 FDs. Causado pelo front-end chamando `/anexos-erp`
para muitos lotes simultaneamente (fan-out), abrindo muitas conexões de uma vez.

**Mudança na API:**
- `requests.Session` compartilhada (`_http_session`) com `HTTPAdapter`
  (`pool_connections=10`, `pool_maxsize=20`) para TODAS as chamadas ao Supabase.
  Reusa sockets via keep-alive em vez de abrir/fechar um por chamada → muito menos FDs.

**Como rodar (produção / teste com Lovable):**
```powershell
py -m uvicorn certificado:app --host 0.0.0.0 --port 8004 --workers 1 --limit-concurrency 50
```
- **SEM `--reload`** (cria processo filho e piora no Windows)
- `--limit-concurrency 50` faz o uvicorn responder 503 acima de 50 requisições
  simultâneas, evitando o estouro de FDs mesmo se o front-end disparar em massa

**Correção necessária no front-end (Lovable):**
- NÃO chamar `/api/erp/lotes/{lot_key}/anexos-erp` para todos os lotes da listagem.
- Buscar anexos só ao abrir o detalhe / clicar "Ver anexos".
- Na listagem, usar os campos já presentes no lote (`certificate_code`, `erp_codcer`,
  `certificate_count`, `erp_camdoc`, `status_app`, etc.).

> Recomendação: rodar com Python 3.11/3.12 (mais estável p/ FastAPI+uvicorn+pyodbc no
> Windows que o 3.14 atual).

---

## 12. Rastreabilidade de Tintas (MPOP501.GER / `E210MVP`)

Relatório de movimentos de consumo de tinta.

**Novo modelo + endpoint:**
- `RastreabilidadeTintasRequest` (`empresa`, `filial`, `obra`, `codigo_relatorio`,
  `revisao_documento`, `data_inicial`, `data_final`, `listar_imagens_certificados`,
  `ordenacao`, `limit`)
- `POST /api/erp/rastreabilidade-tintas` — cabeçalho + dados

**Helpers:** `parse_date_input()` (aceita `YYYY-MM-DD` e `DD/MM/YYYY`), `format_date_br()`.

**Query (`E210MVP`):**
- Filtros fixos do relatório: `PRO.CODFAM = 'TINTAS'` e `MVP.CODTNS IN ('90250', '90251')`
  (era só `90251`; obra 664 tem tinta só em `90250` — ficava vazia. Confirmado: 664 → 1264
  movimentos, todos `90250`, 1178 com certificado/imagem)
- Campos de status por linha: `certificado_status` (`COM_CERTIFICADO` /
  `SEM_CERTIFICADO_NO_LOTE` / `MOVIMENTO_SEM_LOTE`) + `certificado_mensagem`
- **Período obrigatório** (`DATMOV` entre `data_inicial` e `data_final`) — 400 se faltar/invertido
- Joins: `E075PRO` (produto/família), `E615PRJ` (nome da obra),
  `E906OPE` ×2 (operadores `USU_CODOP1`/`USU_CODOP2` → `NOMOPE`)
- `OUTER APPLY USU_TLOTANE`: certificados (`STUFF/FOR XML`), `qtd_certificados`,
  `qtd_imagens_certificados` (anexos com `USU_CAMDOC`)
- Ordenação: `DATA` (padrão) / `LOTE` / `PRODUTO`
- `obra`/`filial` = 0 ou vazio **não filtram**

**Validações (schema confirmado antes de implementar):**
- `E210MVP`/`E906OPE` existem; todas as colunas (`USU_CODLOT`, `USU_CODOP1/2`, etc.) presentes
- `CODFAM='TINTAS'` → 707 produtos; `CODTNS='90251'` → 950k movimentos; combinado → 82k
- Teste maio/2026: 664 linhas em 0,19s, famílias só `TINTAS`, transações só `90251`

> Imagens dos certificados NÃO são buscadas em massa (lição do crash de FDs). O front busca
> por linha via `GET /api/erp/lotes/{lote}/anexos-erp` só quando o usuário clica.

**Aliases de saída** (compatibilidade front — o Lovable estava lendo nomes diferentes):
- Produto: `codigo_produto`, `product_code`
- Derivação: `codigo_derivacao`, `derivation_code`
- Lote: `codigo_lote`, `lot_number`
- Certificado: `certificados`, `codigo_certificado`, `certificate_code`, `certificado`
- Operadores (snake + snake_en + camelCase): `operador_1/2`, `nome_operador_1/2`,
  `operator_1/2`, `operator_1_name`/`operator_2_name`, `operador1/2`, `nomeOperador1/2`
- `operador_2` = `0`/vazio é normalizado para `""` (sem segundo operador)
- Validado: zero duplicidade de linha (`E906OPE` sem `NUMCAD` repetido), todos os aliases presentes.

> **Não** troquei a query para a versão com `OUTER APPLY` de operador/lote normalizado:
> medi a cobertura e dá resultado **idêntico** (cert 458=458; operadores 637/637 resolvidos).
> `USU_CODOP1/2` e `NUMCAD` são `int` (join direto já casa 100%); `TRY_CONVERT(bigint, USU_CODLOT)`
> já trata espaços/vazios. Mantive a query simples e rápida (0,19s).

**Imagens de certificado por lote (mesma fonte da rastreabilidade de MP):**
- Helper `buscar_anexos_certificados_por_lotes()` — busca em **UMA query com chunking**
  (lotes de 1000, sob o limite de ~2100 params do SQL Server) os anexos de `USU_TLOTANE`
  de todos os lotes da página. **Sem N+1** (lição do crash de FDs).
- Quando `listar_imagens_certificados='S'`, cada linha de tinta recebe:
  `qtd_imagens_certificados`, `certificados_imagens`/`certificate_images` (páginas com
  `camdoc`, `exists`, `arquivo_url`), `certificados_detalhe` (agrupado por certificado).
  Com `'N'`, não busca (mais rápido).
- Novo endpoint `GET /api/erp/certificados/lote/{codlot}/imagens?codemp=N` — imagens direto
  do ERP por lote (não depende da tabela `lots` do Supabase). Para o botão "Ver imagens"
  quando o lote da tinta não está no Supabase.
- A imagem em si continua sendo servida por `GET /api/erp/certificados/arquivo/{camdoc}`
  (só ao clicar). Validado: lote 26000218 → `SU0096AX1` (`640461-SU0096AX1-26000218.JPG`,
  exists=true); 724 linhas + imagens em 0,16s.

---

## 13. Lista de Conjuntos (RDCG208.GER / `USU_T900ETQ`)

Etiquetas de entrada de estoque por obra/desenho.

**Novo modelo + endpoint:**
- `ListaConjuntosRequest` (`empresa`, `obra`, `desenho`, `periodo_entrada`,
  `data_inicial`, `data_final`, `codigo_barras`, `descricao_produto`, `limit`)
- `POST /api/erp/lista-conjuntos`

**Helpers novos:** `parse_periodo_entrada()` (aceita `DD/MM/YYYY-DD/MM/YYYY`,
` até `, ` a `), `format_hora_senior()` (minutos `int` → `HH:MM`).

**Query (`USU_T900ETQ` + `USU_T900REE` + `USU_T900PRJ`):**
- Só etiquetas com entrada: `USU_USUENT > 0`
- Só a **última revisão** do desenho (`OUTER APPLY` em `USU_T900PRJ` ordenando `USU_REVDES DESC`)
- Período obrigatório sobre `USU_DATENT` (datetime)
- Filtros opcionais: `obra`, `desenho`, `codigo_barras` (exato), `descricao_produto` (LIKE)
- `peso_total = USU_QTDPRO × USU_PESREA`
- Aliases de saída: `data_entrada_br`, `hora_entrada_br`, `codigo`, `produto`, `descricao`,
  `qtd_produto/embalagem/etiquetas`

**Validações (schema confirmado antes de implementar):**
- 3 tabelas existem; todas as colunas (`USU_DATENT`, `USU_HORENT`, `USU_USUENT`, etc.) presentes
- `USU_DATENT` é `datetime`, `USU_HORENT` é `int` (minutos), `USU_USUENT` é `bigint`
- Teste obra 667: 802 linhas em 0,09s, todas na última revisão, todas com `USUENT>0`

> **Correção sobre o modelo original:** o filtro de data era `USU_DATENT <= data_fim`.
> Como `USU_DATENT` é **datetime**, isso perderia entradas com hora no último dia. Troquei por
> `>= data_ini AND < (data_fim + 1 dia)` para incluir o dia final inteiro.

**Bitola / dimensão / descrições (ajuste):**
- `bitola` = `REE.USU_BITPRO` e `dimensao` = `REE.USU_DIMPRO`, com `NULLIF(LTRIM(RTRIM(...)))`
  (vazio → null). **Nunca** usar `DESPRO` como bitola (retorna coisas como "COLUNA").
- Joins novos `E075PRO`/`E075DER` para `descricao_cadastro_produto` (`PRO.DESPRO`) e
  `descricao_derivacao` (`DER.DESDER`).
- Data de geração do cadastro do produto (`E075PRO`): `data_geracao_cadastro_produto` +
  `_br` (`HORGER` int → `HH:MM`), `usuario_geracao_cadastro_produto`, aliases
  `product_created_date`/`product_created_at`. Validado `6671030-CL1` → `29/04/2026 15:45`.
- Aliases: `bitola_produto`/`product_gauge`, `dimensao_produto`/`product_dimension`.
- Validado item 667/1030/B/`6671030-CL1`: `bitola=''` (vazia no ERP — não é falha da API),
  `dimensao='642 * 460 * 7709'`, `descricao_cadastro_produto='COLUNA'`, `descricao_derivacao='única'`.
  Bitola vazia se corrige na **origem** (`UPDATE USU_T900REE.USU_BITPRO`), não na API.

---

## Resumo de endpoints novos

| Método | Rota |
|--------|------|
| GET | `/api/erp/certificados-qualidade` |
| GET | `/api/erp/certificados/arquivo/{camdoc}` |
| GET | `/api/erp/lotes/{lot_key}/anexos-erp` |
| POST | `/api/erp/certificados/aprovar-multiplos` |
| GET | `/api/erp/lotes/sync/status` |
| POST | `/api/erp/rastreabilidade-materia-prima` |
| POST | `/api/erp/rastreabilidade-materia-prima/gravar-lotes-op` |
| POST | `/api/erp/rastreabilidade-tintas` |
| POST | `/api/erp/lista-conjuntos` |
| GET | `/api/erp/certificados/lote/{codlot}/imagens` |

## SQL consolidado do Supabase (rodar tudo antes de re-sincronizar)

Lista **completa e idempotente** de TODAS as colunas que o `sync` grava em `public.lots`.
Seguro rodar mesmo nas que já existem (`if not exists` ignora).

```sql
alter table public.lots add column if not exists lot_key text;
alter table public.lots add column if not exists lot_number text;
alter table public.lots add column if not exists erp_codemp bigint;
alter table public.lots add column if not exists erp_codlot bigint;
alter table public.lots add column if not exists erp_codfil bigint;
alter table public.lots add column if not exists erp_codfor bigint;
alter table public.lots add column if not exists supplier_name text;
alter table public.lots add column if not exists erp_numnfc bigint;
alter table public.lots add column if not exists erp_codsnf text;
alter table public.lots add column if not exists erp_datent date;
alter table public.lots add column if not exists invoice_entry_date date;
alter table public.lots add column if not exists purchase_order text;
alter table public.lots add column if not exists erp_codpro text;
alter table public.lots add column if not exists product_code text;
alter table public.lots add column if not exists erp_codder text;
alter table public.lots add column if not exists derivation_code text;
alter table public.lots add column if not exists product_description text;
alter table public.lots add column if not exists invoice_item_description text;
alter table public.lots add column if not exists erp_codfam text;
alter table public.lots add column if not exists family_code text;
alter table public.lots add column if not exists family_description text;
alter table public.lots add column if not exists erp_codori text;
alter table public.lots add column if not exists origin_code text;
alter table public.lots add column if not exists erp_pro_exicer text;
alter table public.lots add column if not exists requires_certificate boolean;
alter table public.lots add column if not exists erp_codcer text;
alter table public.lots add column if not exists certificate_code text;
alter table public.lots add column if not exists erp_anexo_codcer text;
alter table public.lots add column if not exists erp_camdoc text;
alter table public.lots add column if not exists certificate_count bigint;
alter table public.lots add column if not exists certificate_pages_count bigint;
alter table public.lots add column if not exists status_app text;
alter table public.lots add column if not exists status_erp text;
alter table public.lots add column if not exists synced_from_erp_at timestamptz;
alter table public.lots add column if not exists updated_at timestamptz;

-- Opcional (resumo extra de certificados em lista)
alter table public.lots add column if not exists certificate_codes jsonb default '[]'::jsonb;

-- Recarrega o cache de schema do PostgREST
notify pgrst, 'reload schema';
```

## Como rodar

```powershell
# Desenvolvimento
py -m uvicorn certificado:app --reload --host 0.0.0.0 --port 8004

# Produção (sem --reload, 1 worker — por causa do scheduler)
py -m uvicorn certificado:app --host 0.0.0.0 --port 8004
```
