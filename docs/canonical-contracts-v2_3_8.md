# Contratos Canônicos: Web Layer ↔ Runtime V2.3.8

**Versão**: 2.2 (corrigida após re-revisão final — B1-B4 resolvidos)
**Data**: 2026-08-27
**Base**: Gap Analysis `docs/gap-analysis-web-v2_3_8.md` + Design Review (D1-D22) + Re-revisão (R1-R10) + Re-revisão final (B1-B4)
**Status**: **DRAFT v2.2** — aguarda gate canônico e implementação Fase 1

---

## 0. Decisões de Design (registradas)

| Decisão | Valor | Justificativa |
|---------|-------|---------------|
| **D2** | `a` — sem Llama phase na web | Evita GPU management; fallback de transporte cobre resiliência |
| **D3** | `revisada` — **autoridade de modelo generalizada** | Usuário quer poder escolher Qwen OU Gemini como primário no caminho V2.3.8; exige alteração canônica em `v238_base_materializer.py:379` (gate canônico separado) |
| **D4** | `execution_mode` (não `mode`) | Runtime lê `execution_mode` (`pipeline_orchestrator.py:121`, `v238_base_materializer.py:213,140`) |
| **D5** | Fallback de transporte = re-execução completa com novo `operation_id` | `web_retranslation_runner.py:102-107`; evita colisão com checkpoint parcial |

---

## 1. Princípios Gerais

| Princípio | Descrição |
|-----------|-----------|
| **P1: Fail-closed** | Qualquer violação de contrato → aborta execução, não degrada silenciosamente |
| **P2: Exactly-once** | Cada `operation_id` resulta em ≤131 calls Qwen e ≤1 call Llama por fase; budget enforçado no provider via `reserve()` |
| **P3: Durabilidade** | Escrita atômica + completion marker + reconciliação; nenhum estado parcial persistido |
| **P4: Linha de vida única** | `operation_id` rastreia: request → provider → stage → materialização → archive |
| **P5: Separação de responsabilidades** | Web layer = orquestração/UI/state; Runtime V2.3.8 = execução pura, determinística, testável |

---

## 2. Contrato C1: Execution Context Factory

### 2.1 Assinatura

```python
# src/subtranslate/web_execution_context.py (novo)
def build_v238_execution_context(
    *,
    job: dict,                    # job dict do web layer (queue_helpers.build_job_batch)
    transport_config: dict,       # saída de public_transport_config()
    source_language: str,         # "inglês" | "francês" | ...
    operation_id: str,            # UUID único por execução (novo por tentativa)
    execution_mode: str = "LIVE_CAPTURED",  # "LIVE_CAPTURED" | "TEST_FAKE" | "OFFLINE_REPLAY"
    capture_root: Path,           # OBRIGATÓRIO em LIVE_CAPTURED/OFFLINE_REPLAY
    authorized_primary_models: list[str] | None = None,  # ex: ["qwen", "gemini"]
    glossary: dict[str, str] | None = None,       # B4: glossário (orchestrator repassa, pipeline_orchestrator.py:50)
    stage_completion_root: Path | None = None,    # B3: marker de durabilidade P3 (v238_full_translation_stage.py:109-114)
    checkpoint_root: Path | None = None,          # B3: raiz de checkpoint (v238_base_materializer.py:257)
    job_id: str | None = None,                    # B3: job id do web layer
    prompt_schema_hash: str | None = None,        # B3: hash do schema de prompt (build metadata)
    configuration_hash: str | None = None,        # B3: hash da config efetiva (build metadata)
    candidate_commit: str | None = None,          # B3: commit da imagem (env CANDIDATE_COMMIT)
    candidate_image_id: str | None = None,        # B3: ID da imagem (env CANDIDATE_IMAGE_ID)
    llama_model_tag: str | None = None,
    llama_model_digest: str | None = None,
) -> dict:
    """
    Constrói o execution_context completo exigido por production_v2_3_8_adapter.
    Usa execution_mode (não mode) — o runtime lê execution_mode.
    """
```

### 2.2 Campos Obrigatórios do Context

| Campo | Tipo | Origem | Obrigatório |
|-------|------|--------|-------------|
| `response_provider` | `DurableResponseProvider` (classe concreta) | Adapter C2 | ✅ |
| `operation_budget` | `OperationCallBudget` | `v238_llama_policy` (auto-criado pelo orchestrator se ausente, `pipeline_orchestrator.py:126`); a factory NÃO precisa criá-lo | ✅ |
| `operation_id` | `str` | Gerado no web (UUID, novo por tentativa) | ✅ |
| `execution_mode` | `str` | Parâmetro `execution_mode` (NÃO `mode`) | ✅ |
| `source_language` | `str` | `transport_config.source_language` ou seleção UI | ✅ |
| `base_materializer` | `BaseMaterializer` | Auto-criado em LIVE_CAPTURED; injetado (TEST_FIXTURE) em TEST_FAKE/OFFLINE_REPLAY (B2) | Se não-LIVE_CAPTURED |
| `capture_root` | `Path` | `state_dir / "v238-captures"` | Se LIVE_CAPTURED/OFFLINE_REPLAY |
| `model` | `str` | `transport_config.primary.model` | ✅ |
| `model_digest` | `str` | `ollama show --format json` no momento da seleção, persistido no `transport_config_store` (M5 ampliado) | ✅ |
| `episode_id` | `int` | `job["episode_id"]` (enriquecer job dict) | LIVE_CAPTURED |
| `anime_series_id` | `int` | `job["anime_series_id"]` (enriquecer job dict) | LIVE_CAPTURED |
| `prompt_schema_hash` | `str` | Hash do schema de prompt (build metadata, env `PROMPT_SCHEMA_HASH`) | LIVE_CAPTURED |
| `glossary` | `dict[str, str]` | Glossário do web layer (B4) | ✅ |
| `stage_completion_root` | `Path` | `state_dir / "v238-completions"` (B3) | ✅ |
| `checkpoint_root` | `Path` | `state_dir / "v238-checkpoints"` (B3) | ✅ |
| `job_id` | `str` | `job["id"]` (B3) | ✅ |
| `glossary_hash` | `str` | Hash do glossário usado | LIVE_CAPTURED |
| `configuration_hash` | `str` | Hash da config efetiva (build metadata, env `CONFIGURATION_HASH`) | LIVE_CAPTURED |
| `candidate_commit` | `str` | Commit da imagem (env `CANDIDATE_COMMIT`) | LIVE_CAPTURED |
| `candidate_image_id` | `str` | ID da imagem (env `CANDIDATE_IMAGE_ID`) | LIVE_CAPTURED |
| `authorized_primary_models` | `list[str]` | Config (ex: `["qwen", "gemini"]`) | LIVE_CAPTURED |
| `llama_provider` | `CanonicalLlamaProvider \| None` | C3 (None se D2=a) | Se houver unidades elegíveis |
| `llama_load` | `Callable[[], None] \| None` | C3 | Se llama_provider |
| `llama_unload` | `Callable[[], None] \| None` | C3 | Se llama_provider |
| `llama_model_tag` | `str \| None` | `LLAMA_MODEL_TAG` (`llama3.1:8b`) | Se llama_provider |
| `llama_model_digest` | `str \| None` | `LLAMA_MODEL_DIGEST` (`46e0c10c...`) | Se llama_provider |
| `primary_ledger` | `list[dict]` | Derivado do summary V226 (NÃO é Path) | ✅ (lista) |
| `v238_allow_primary_ledger_failures` | `bool` | Default `True` no adapter | ✅ |

### 2.3 Regras de Validação (Fail-closed)

```python
def _validate_context(ctx: dict) -> None:
    required = ["response_provider", "operation_budget", "operation_id",
                "execution_mode", "source_language"]
    # base_materializer NÃO é obrigatório no required: o adapter auto-cria
    # CanonicalV226LiveMaterializer em LIVE_CAPTURED
    # (production_v2_3_8_adapter.py:45-48).
    for k in required:
        if k not in ctx or ctx[k] is None:
            raise ContractError(f"execution_context missing required field: {k}")
    if ctx["execution_mode"] == "LIVE_CAPTURED":
        identity_fields = ["operation_id", "episode_id", "anime_series_id", "model",
                           "model_digest", "prompt_schema_hash", "glossary_hash",
                           "configuration_hash", "candidate_commit", "candidate_image_id"]
        for k in identity_fields:
            if not ctx.get(k):
                raise ContractError(f"LIVE_CAPTURED requires {k}")
        # Autoridade de modelo generalizada (D3 revisada)
        authorized = ctx.get("authorized_primary_models") or ["qwen"]
        model = str(ctx.get("model") or "")
        if model and not any(model.casefold().startswith(p) for p in authorized):
            raise ContractError(f"primary model {model!r} not in authorized_primary_models")
```

---

## 3. Contrato C2: Transport → DurableResponseProvider Adapter

### 3.1 Interface Real (V2.3.8) — `v238_response_provider.py`

```python
class DurableResponseProvider:  # CLASSE CONCRETA, não Protocol
    MODES = {"LIVE_CAPTURED", "OFFLINE_REPLAY", "TEST_FAKE"}
    TRANSPORTS = {"OLLAMA_MODEL", "NETWORK_NON_MODEL", "LOCAL_TEST", "OFFLINE_REPLAY", "TEST_FAKE"}

    def __init__(self, mode: str, *, capture_root=None, client=None,
                 fake=None, expected_capture_ids=None, transport_semantics=None): ...
    def attach_operation_budget(self, budget, *, phase="V238_SEMANTIC") -> None: ...
    def respond(self, request, *, capture_id=None) -> dict: ...
    def translate(self, request, *, capture_id=None) -> str: ...
    # Atributos: self.calls (LISTA de dicts), self.metrics (DICT), self.mode (str)
```

### 3.2 Adapter Proposto (subclassifica a classe real)

```python
# src/subtranslate/web_durable_provider.py (novo)
from v238_response_provider import DurableResponseProvider
from transport_providers import BaseTransport, transport_from_config

class WebDurableResponseProvider(DurableResponseProvider):
    """
    Subclasse de DurableResponseProvider (NÃO um Protocol fictício).
    O stage exige isinstance(value, DurableResponseProvider)
    (v238_full_translation_stage.py:196-200).
    """
    def __init__(self, transport_config: dict, *, mode: str = "LIVE_CAPTURED",
                 capture_root: Path, api_key: str | None = None):
        provider = str((transport_config.get("primary") or {}).get("provider", "ollama")).lower()
        transport_semantics = "OLLAMA_MODEL" if provider == "ollama" else "NETWORK_NON_MODEL"
        super().__init__(mode, capture_root=capture_root, transport_semantics=transport_semantics)
        self._transport_config = transport_config
        self._api_key = api_key
        self._client = self._build_client()

    def _build_client(self) -> callable:
        # Extrai a seção do provider (primary/fallback) do transport_config web
        # (padrão _provider_for, web_retranslation_runner.py:27-37). NÃO passar
        # o config web inteiro a transport_from_config (R3).
        def client(payload: dict) -> dict:
            section = self._select_section(payload)  # primary ou fallback
            section = self._inject_api_key(section)  # B1: api_key da seção keys
            transport = transport_from_config(section, payload)
            # B1: projeção de request — o payload do stage V238 NÃO tem
            # messages/options (v238_full_translation_stage.py:444-452);
            # build_request exige chat-shape (transport_providers.py:58-69).
            chat_payload = self._project_request(payload)
            request = transport.build_request(chat_payload)
            body = _http_post(transport.endpoint(), transport.headers(), request)
            # B1: projeção de response — extract_content devolve texto bruto;
            # se o modelo retornar JSON, decodificar e extrair translation/text.
            content = transport.extract_content(body)
            parsed = _decode_model_content(content)  # json.loads se JSON
            translation = parsed.get("translation") or parsed.get("text") or content
            return {"translation": translation}
        return client

    def _project_request(self, payload: dict) -> dict:
        # Stage payload V238 -> chat-shape para transport.build_request.
        # O payload do stage tem text/event_id/canonical_unit_id etc.
        text = payload.get("text") or payload.get("source_text") or ""
        return {
            "messages": [{"role": "user", "content": text}],
            "options": {"temperature": 0.0, "num_predict": 1024},
            "format": "json",
        }

    def _inject_api_key(self, section: dict) -> dict:
        # B1: injeta api_key da seção keys do transport_config
        # (padrão _provider_for, web_retranslation_runner.py:32-33).
        provider = str(section.get("provider", "")).lower()
        keys = self._transport_config.get("keys") or {}
        if not section.get("api_key") and provider in keys and keys[provider]:
            section = dict(section)
            section["api_key"] = keys[provider]
        return section

    def _select_section(self, payload: dict) -> dict:
        # primary incondicional dentro do provider (R2.5/D5):
        # fallback de transporte = re-execução completa com novo operation_id
        # (web_retranslation_runner.py:102-107), NÃO fallback dentro do provider.
        primary = self._transport_config.get("primary") or {}
        if primary.get("provider"):
            return primary
        fallback = self._transport_config.get("fallback") or {}
        return fallback or {"provider": "ollama", "model": "qwen3.5:9b"}
```

### 3.3 Regras de Comportamento

| Regra | Descrição |
|-------|-----------|
| **R2.1** | `execution_mode=LIVE_CAPTURED` → usa chaves reais, chama modelo real via `BaseTransport` |
| **R2.2** | `execution_mode=TEST_FAKE` → retorna payload fake determinístico (herdado da classe real) |
| **R2.3** | `execution_mode=OFFLINE_REPLAY` → lê de arquivo de replay (herdado) |
| **R2.4** | Budget enforçado via `OperationCallBudget.reserve()` (NÃO `allow_call()` — não existe) |
| **R2.5** | Fallback de transporte (primary→fallback) = **re-execução completa** com novo `operation_id` (`web_retranslation_runner.py:102-107`); NÃO é fallback dentro do provider |
| **R2.6** | Métricas: `self.metrics` (dict) com `physical_client_calls`, `model_generation_calls`, `provider_requests`, `requests_by_operation` |
| **R2.7** | `capture_root` OBRIGATÓRIO em LIVE_CAPTURED/OFFLINE_REPLAY (`v238_response_provider.py:110-111`) |

---

## 4. Contrato C3: Llama Policy Binding

### 4.1 Decisão de Design (D2=a)

> **D2=a**: não usar Llama phase na web (`llama_provider=None`). O fallback semântico V2.3.8 fica desativado; o fallback de transporte (re-execução) cobre resiliência.

### 4.2 Consequência OBRIGATÓRIA de D2=a (documentada — corrigida R1)

**Fato do código real**: com `llama_provider=None`, qualquer unidade `BLOCKED`/`SUSPECT` no ledger primário → `V238_CANONICAL_LLAMA_PROVIDER_REQUIRED` (`production_v2_3_8_adapter.py:66-68`) → **episódio inteiro falha**.

**Fato adicional (R1)**: o flag `v238_allow_primary_ledger_failures` é setado no adapter v2_3_8 (`production_v2_3_8_adapter.py:39`) mas **lido apenas** no gate de elegibilidade V2.2.5 (`production_v2_2_5_adapter.py:449`). Ele NÃO evita `V238_CANONICAL_LLAMA_PROVIDER_REQUIRED`. A mitigação documentada na v2.0 **não existe no código**.

**Decisão**: registrar **M7** (mudança canônica) — o adapter v2_3_8 deve respeitar `v238_allow_primary_ledger_failures=True` e, quando `llama_provider=None`, pular a fase llama com estado `SKIPPED_ALLOWED` (unidades não resolvidas marcadas como não publicáveis) em vez de levantar `V238_CANONICAL_LLAMA_PROVIDER_REQUIRED`. Sem M7, o caminho D2=a falha todo episódio com unidades BLOCKED/SUSPECT.

### 4.3 Se D2=b (futuro): Requisitos

| Item | Especificação |
|------|---------------|
| `llama_provider` | Instância de `CanonicalLlamaProvider` (carrega modelo, expõe `generate()`) |
| `llama_load()` | Callback que carrega modelo na GPU |
| `llama_unload()` | Callback que libera GPU |
| `llama_model_tag` | **`llama3.1:8b`** (NÃO qwen3.5:9b) — `v238_llama_policy.py:23` |
| `llama_model_digest` | **`46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e`** — `v238_llama_policy.py:24` |
| `OperationCallBudget` | `qwen_physical_maximum=131`, `llama_generation_maximum=1` (`v238_llama_policy.py:47-49`) |

> `CanonicalLlamaProvider.__init__` levanta `V238_LLAMA_MODEL_AUTHORITY_MISMATCH` para qualquer tag/digest diferente (`v238_llama_policy.py:102-103`).

---

## 5. Contrato C4: Pipeline Selection

### 5.1 Configuração

```python
# transport_config_store.py ou novo config
PIPELINE_OPTIONS = ["legacy", "v2_3_0", "v2_3_8"]
DEFAULT_PIPELINE = "v2_3_8"  # para novas execuções
```

### 5.2 Persistência da Seleção

- A seleção SERÁ persistida em `transport_config.json` (campo `pipeline`, hoje inexistente — M5), NÃO apenas em env `TRANSLATOR_PIPELINE`.
- Default atual é `"legacy"` (`app.py:60,93`; `web_retranslation_runner.py:77`) — a migração para `v2_3_8` é explícita e reversível.

### 5.3 UI/Endpoint

- Endpoint `GET/POST /pipeline-config` para selecionar pipeline ativo.
- Relação com `/pipeline` existente (GET, `app.py:1430-1432`): `/pipeline` continua expondo `_pipeline_info()` (read-only); `/pipeline-config` é o endpoint de escrita da seleção.
- Legacy mantido como fallback explícito (opt-in).

---

## 6. Contrato C5: Normal Translation Path (Refatoração)

### 6.1 Antes (Legacy)

```python
# app.py:_run_episode (atual, linhas 862-958)
# Chama anime_subtitle_translator.py via subprocess (SCRIPT_PATH)
# Não usa V2.3.8, não tem budget, não tem exactly-once
```

### 6.2 Depois (V2.3.8) — assinatura REAL do orchestrator

```python
# app.py:_run_episode (novo)
def _run_episode(job: dict) -> None:
    from pipeline_orchestrator import execute_pipeline_plan
    from web_execution_context import build_v238_execution_context
    from web_durable_provider import WebDurableResponseProvider

    # 1. Transport config
    transport_cfg = load_transport_config(STATE_DIR / "transport_config.json")
    # 2. Execution context (execution_mode, NÃO mode)
    ctx = build_v238_execution_context(
        job=job,
        transport_config=transport_cfg,
        source_language=job.get("source_language") or "inglês",
        operation_id=_new_operation_id(job),  # uuid.uuid4().hex por tentativa (R7)
        execution_mode="LIVE_CAPTURED",
        capture_root=STATE_DIR / "v238-captures",
        authorized_primary_models=["qwen", "gemini"],  # D3 revisada
    )
    # 3. Staging de temporada preservado (glossário por série, app.py:865-873):
    #    temporary_dir com nome da temporada + symlink do source.
    # 4. Executa pipeline V2.3.8 — assinatura REAL:
    #    execute_pipeline_plan(plan_id, source_path, output_path, context=None)
    result = execute_pipeline_plan("v2_3_8", staged_source, staged_output, ctx)
    # 5. Persiste resultado — _apply_canonical_pipeline_summary + _persist_locked
    _apply_canonical_pipeline_summary(job, result)
    _persist_locked()
    # 6. output_exists_race preservado (app.py:933-936): se o destino final
    #    apareceu durante o job, marcar FAILED sem sobrescrever.
    if destination.exists():
        job["status"] = "FAILED"
        job["reason"] = "output_exists_race"
        job["error"] = "A legenda final apareceu durante o job; nada foi sobrescrito"
        _persist_locked()
```

### 6.2.1 Projeção do summary v2_3_8 (R6)

O resultado de `execute_pipeline_plan("v2_3_8", ...)` NÃO tem `status`/`stage`/`resolved`/`events`/`flags`/`critical_flags` no topo — retorna `stages`, `calls`, `karaoke`, `metrics_measurements`, `operation_budget` (`pipeline_orchestrator.py:170-194`). `_apply_canonical_pipeline_summary` (`app.py:804-818`) exige esses campos.

**Requisito**: `_project_v238_summary(result) -> dict` que projeta:
- `status`: `"COMPLETED"` se todas as stages passaram e `karaoke.failures == []`; senão `"FAILED"`;
- `stage`: última stage completada;
- `resolved`/`events`: derivados do `primary_ledger` (unidades RESOLVED / total);
- `flags`/`critical_flags`: derivados de unidades BLOCKED/SUSPECT não resolvidas;
- `v238_metrics`: conforme C7.

Sem essa projeção, o job fica preso em `VALIDATING` com progresso 0/0.

### 6.3 Compatibilidade e Controles Operacionais

- Manter `_run_episode_legacy` como fallback explícito (feature flag).
- **Cancelamento/progresso**: o slot `state["process"]` existe (`app.py:746`) mas hoje guarda um `subprocess.Popen`; o modo in-process requer guardar um handle de thread/processo in-process e adaptar `/stop` e `/pause` (`app.py:1658-1708`, `_send_process_group_signal` `:764`). Requer:
  - `state["process"]` = handle do thread/processo in-process;
  - `/stop` e `/pause` (`app.py:1658-1708`) verificam o handle in-process;
  - progresso via `_apply_canonical_pipeline_summary` (já atualiza `job["progress"]`).
- **dry_run**: preservado via `job["dry_run"]` → `execution_mode="TEST_FAKE"`. **B2**: a auto-criação do materializer só ocorre em LIVE_CAPTURED (`production_v2_3_8_adapter.py:45-48`); em TEST_FAKE/OFFLINE_REPLAY, `require_materializer` levanta `V238_BASE_TRANSLATION_MATERIALIZER_REQUIRED` (`v238_base_materializer.py:441-448`). Requer injeção explícita de materializer `TEST_FIXTURE` no C1 para esses modos (M10).
- **Marcador de summary**: `_consume_worker_output_line` (`app.py:848`) lê stdout do subprocess; no modo in-process, o summary vem do retorno direto de `execute_pipeline_plan`.

---

## 7. Contrato C6: Exactly-Once Web Layer

### 7.1 Garantias (corrigidas)

| Camada | Garantia | Como |
|--------|----------|------|
| **Provider (FULL_TRANSLATION_V238)** | ≤131 calls Qwen + ≤1 call Llama por `operation_id` | `OperationCallBudget.reserve()` (`v238_llama_policy.py:55-82`) |
| **Karaoke V230 (etapa separada)** | Fora do OperationCallBudget; POST direto ao Ollama local | `pipeline_orchestrator.py:152-164`; `production_v2_3_0_adapter.py:176,210` |
| **Orchestrator** | Não re-executa stage se `output.exists()` | `pipeline_orchestrator.py:129-130` (FileExistsError) |
| **Web Layer** | Não submete job duplicado | `_existing_output` (`app.py:1634`) + fila ativa (`:1632`) |

### 7.1.1 Nota sobre o karaoke V230 (R4)

O pipeline v2_3_8 completo no orchestrator **sempre** executa `augment_karaoke_candidate_v2_3_0` (`pipeline_orchestrator.py:152-164`), que faz POST direto ao Ollama local (`production_v2_3_0_adapter.py:176,210`) **fora do OperationCallBudget** e **fora da seleção de transporte** (sempre Ollama local, ignora Gemini/Qwen). `_validate_v230_result` (`pipeline_orchestrator.py:87-104`) exige `translated_units == song_units` e `failures == []`.

**Decisão**: o contrato P2/C6 aplica-se ao stage `FULL_TRANSLATION_V238`. O karaoke V230 é uma etapa separada com transporte fixo (Ollama local) e validação própria. O custo de modelo do karaoke é contabilizado separadamente (`v230_calls`, `pipeline_orchestrator.py:173-179`). Se o karaoke falhar, o episódio falha (fail-closed) — documentado, não silencioso.

### 7.2 Regras de Ouro

1. **Nunca** chamar `respond()` além do budget com mesmo `operation_id` — `reserve()` levanta `LlamaPolicyError`
2. **Sempre** verificar `output.exists()` antes de executar stage (orchestrator já faz)
3. **Job idempotente**: re-submissão do mesmo `operation_id` → no-op ou erro controlado
4. **Budget é por execução** (criado fresco em `pipeline_orchestrator.py:126`); a garantia entre submissões depende de `output.exists()` + checagens da web
5. **Fallback de transporte** (re-execução) usa **NOVO `operation_id`** por tentativa — evita `V238_CHECKPOINT_PARTIAL_OR_CONCURRENT_CLAIM` (`v238_base_materializer.py:336-337`)

---

## 8. Contrato C7: Observabilidade

### 8.1 Métricas Expostas no Job Telemetry (nomes reais)

```python
# _job_telemetry(job) → dict (aditivo ao existente, app.py:572-585)
{
    "v238_metrics": {
        "calls": int,                    # total de calls (model_calls)
        "physical_client_calls": int,    # v238_response_provider.metrics
        "model_generation_calls": int,
        "provider_requests": int,
        "prompt_tokens": int,            # NOME REAL (não tokens_in)
        "completion_tokens": int,        # NOME REAL (não tokens_out)
        "elapsed_seconds": float,        # NOME REAL (não latency_ms)
        "budget_used": int,              # operation_budget.snapshot().total_reserved
        "budget_remaining": int,         # qwen_physical_maximum - qwen_reserved
        "provider_mode": str,            # execution_mode
        "fallback_used": bool,           # re-execução com novo operation_id
    },
    "stages": [
        {"name": "FULL_TRANSLATION_V238", "status": "completed", "duration_ms": 1234},
        {"name": "KARAOKE_AUGMENTATION_V230", "status": "completed", "duration_ms": 567},
    ]
}
```

### 8.2 Projeção do primary_ledger

- O telemetry atual lê `units.json`/`attempts.jsonl` do failure ledger V2.2.5.
- O caminho V2.3.8 produz `primary_ledger` (lista) em formato diferente.
- Requer projeção: `primary_ledger` → `units.json` compatível para a UI não mostrar progresso vazio.

### 8.3 Logs Estruturados

- `technical` log: cada call do provider com `operation_id`, `stage`, `elapsed_seconds`, `prompt_tokens`/`completion_tokens`
- `summary` log: resultado final por episódio (igual hoje + métricas V2.3.8)

---

## 9. Matriz de Conformidade (Checklist de Validação)

| Contrato | Implementado | Testado Offline | Canary Preflight | Auditoria Independente |
|----------|--------------|-----------------|------------------|------------------------|
| C1: Context Factory | ❌ | ❌ | ❌ | ❌ |
| C2: Provider Adapter | ❌ | ❌ | ❌ | ❌ |
| C3: Llama Policy | ❌ (D2=a) | ❌ | ❌ | ❌ |
| C4: Pipeline Selection | ❌ | ❌ | ❌ | ❌ |
| C5: Normal Path | ❌ | ❌ | ❌ | ❌ |
| C6: Exactly-Once | ⚠️ (parcial) | ❌ | ❌ | ❌ |
| C7: Observabilidade | ❌ | ❌ | ❌ | ❌ |

---

## 10. Mudanças Canônicas Necessárias (gate separado)

| # | Arquivo | Mudança | Gate |
|---|---------|---------|------|
| M1 | `v238_base_materializer.py:379` | Generalizar `V238_PRIMARY_MODEL_AUTHORITY_NOT_QWEN` → verificar `authorized_primary_models` (D3 revisada) | Gate canônico separado |
| M2 | `queue_helpers.py:20-29` | Enriquecer job dict com `episode_id`, `anime_series_id` | Fase 1 |
| M3 | `app.py` | `_run_episode` in-process + `_apply_canonical_pipeline_summary` + `_persist_locked` | Fase 2 |
| M4 | `app.py` | Cancelamento in-process: flag cooperativa `state["cancel_requested"]` + polling de checkpoint a cada batch; `/stop` seta a flag e o thread aborta no próximo checkpoint (sinais SIGTERM não alcançam thread Python) | Fase 2 |
| M5 | `transport_config_store.py` | Campo `pipeline` persistido + `authorized_primary_models` + `model_digest` (estender `DEFAULT_CONFIG` e merge, `transport_config_store.py:20-26,51`) | Fase 1 |
| M6 | `app.py` | Projeção `primary_ledger` → `units.json` | Fase 3 |
| M7 | `production_v2_3_8_adapter.py:66-68` | Respeitar `v238_allow_primary_ledger_failures=True`: com `llama_provider=None`, pular fase llama com `SKIPPED_ALLOWED` em vez de `V238_CANONICAL_LLAMA_PROVIDER_REQUIRED` (R1) | Gate canônico separado |
| M8 | `app.py` | `_project_v238_summary(result)` — projeção do summary v2_3_8 para `_apply_canonical_pipeline_summary` (R6) | Fase 2 |
| M9 | `app.py` | `_new_operation_id(job)` — `uuid.uuid4().hex` por tentativa no fallback (R7) | Fase 2 |
| M10 | `web_execution_context.py` | Injeção de materializer `TEST_FIXTURE` para TEST_FAKE/OFFLINE_REPLAY (B2) | Fase 1 |

---

## 11. Próximos Passos (Pós-Re-Revisão)

1. **Re-revisão final** → `subtranslate-review` valida contratos C1-C7 v2.2
2. **Gate canônico M1** → autorização para alterar `v238_base_materializer.py` (D3 revisada)
3. **Implementação** na candidata (feature branch `feature/v2_3_8-web-integration`)
4. **Testes Offline** determinísticos (mock providers, fake budgets)
5. **Canary Preflight** (`subtranslate-canary` skill) — 1 batch read-only
6. **Gate Canônico** → autorização para execução real
7. **Reconciliação Canônica** → atualizar `PROJECT_STATE.json` aditivo

---

## 12. Referências (corrigidas)

- `v238_response_provider.py` — `DurableResponseProvider` (classe concreta), `ResponseProviderError`
- `v238_llama_policy.py` — `OperationCallBudget.reserve()`, `CanonicalLlamaProvider`, `LLAMA_MODEL_TAG`/`LLAMA_MODEL_DIGEST`
- `production_v2_3_8_adapter.py` — `translate_subtitle_file_v2_3_8`, `execution_context` schema, `v238_allow_primary_ledger_failures`
- `pipeline_orchestrator.py` — `execute_pipeline_plan(plan_id, source, output, context)`, `PipelinePlan`
- `transport_providers.py` — `BaseTransport`, `OllamaTransport`, `GeminiTransport`, `OpenAICompatTransport`, `transport_from_config`
- `transport_config_store.py` — config web (primary/fallback/keys)
- `v238_base_materializer.py` — `CanonicalV226LiveMaterializer`, identidade LIVE_CAPTURED, autoridade de modelo
- `v238_full_translation_stage.py` — `execute_v238_stage`, `_provider` (isinstance check)
- `queue_helpers.py` — `build_job_batch` (job dict)
- `app.py` — `_run_episode`, `_apply_canonical_pipeline_summary`, `_persist_locked`, `_job_telemetry`
