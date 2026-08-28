# Mapeamento de Gaps: Web Atual (candidata v2.4.9) ↔ Runtime Canônico V2.3.8

**Data**: 2026-08-26  
**Base**: Auditoria `subtranslate-audit` (commit `7eb7b5d`, v2.4.9)  
**Objetivo**: Subsidiar o design de contratos para Opção A (conectar web ao runtime canônico V2.3.8)

---

## 1. Visão Geral dos Dois Mundos

| Dimensão | Web Atual (candidata) | V2.3.8 Canônico (implementado, não conectado) |
|----------|----------------------|-----------------------------------------------|
| **Pipeline default** | `legacy` (via `TRANSLATOR_PIPELINE` env) | `v2_3_8` (registrado em `pipeline_registry.py`) |
| **Tradução normal** | `_run_episode` → `anime_subtitle_translator.py` (subprocess legacy) | `pipeline_orchestrator.execute_pipeline_plan("v2_3_8", ...)` |
| **Retradução** | `_run_retranslation_episode` → `web_retranslation_runner` → orchestrator V2.3.8 | Já usa V2.3.8 via orchestrator |
| **Provider/Transport** | `transport_config_store` (primary/fallback, chaves seguras) | `DurableResponseProvider` (mode, budget, llama boundary, ownership) |
| **Budget/Llama** | Ausente | `OperationCallBudget`, `CanonicalLlamaProvider`, load/unload, model tag/digest |
| **Execution Context** | Não construído | Obrigatório: `response_provider`, `operation_budget`, `operation_id`, `llama_*`, `base_materializer`, `primary_ledger` |
| **Durabilidade** | Best-effort (state JSONL) | `_atomic_stage_write` + completion marker + `reconcile_atomic_stage_output` |
| **Exactly-once** | Parcial (runner evita duplicar) | `OperationCallBudget` enforçado no provider + validação `output.exists()` |
| **Karaoke** | Via orchestrator (V2.3.0 stage) | Integrado no plano V2.3.8 (`KARAOKE_AUGMENTATION_V230`) |

---

## 2. Gaps por Camada (Classificados)

### 🔴 CRÍTICO — Bloqueiam uso de V2.3.8 na web

| ID | Gap | Localização | Esforço Estimado |
|----|-----|-------------|------------------|
| **G1** | Caminho normal (`_run_episode`) não usa V2.3.8 | `app.py:_run_episode` (linha ~838) | Médio (refatorar para orchestrator) |
| **G2** | Interface Provider incompatível | `transport_providers.py` vs `v238_provider.py:DurableResponseProvider` | Alto (adapter necessário) |
| **G3** | Llama Policy não conectada | `v238_llama_policy.py` não instanciado no web | Alto (callbacks load/unload, model tag/digest) |
| **G4** | Execution Context não materializado | `production_v2_3_8_adapter.py` exige context completo | Alto (factory no web layer) |
| **G5** | STALE_CANONICAL_STATE | `PROJECT_STATE.json` não reflete v2.4.9 | Processo documental (reconciliação) |

### 🟡 MÉDIO — Requerem design/decisão

| ID | Gap | Localização | Esforço Estimado |
|----|-----|-------------|------------------|
| **G6** | Auto-fallback web ≠ V2.3.8 fallback semântico | `web_retranslation_runner.py` vs `v238_llama_policy.py` | Médio (mapear semânticas) |
| **G7** | Source Language mapping | `transport_config.source_language` → `execution_context["source_language"]` | Baixo |
| **G8** | Pipeline selection UI/config | `TRANSLATOR_PIPELINE` env default `legacy` | Baixo |
| **G9** | Observabilidade V2.3.8 no job telemetry | `_job_telemetry` não expõe `metrics_measurements` | Baixo |

### 🟢 BAIXO — Melhorias/ajustes

| ID | Gap | Localização |
|----|-----|-------------|
| **G10** | Karaoke já OK via orchestrator | — |
| **G11** | Retradução já OK via orchestrator | — |

---

## 3. Matriz de Dependências entre Gaps

```
G5 (Reconciliação Canônica) ──────────────────┐
                                               ▼
G1 (Normal path) ← G4 (Context Factory) ← G2 (Provider Adapter) ← G3 (Llama Policy)
                                               │
                                               ▼
                                          G6 (Fallback semântico)
                                               │
                                               ▼
                                          G7, G8, G9 (Config/Observabilidade)
```

**Ordem lógica**: G5 → G4 → G2 → G3 → G1 → G6 → G7/G8/G9

---

## 4. Decisões de Design Pendentes (para Contratos C1-C7)

| Decisão | Opções | Impacto |
|---------|--------|---------|
| **D1: Modo V2.3.8 na web** | `LIVE_CAPTURED` (real) vs `TEST_FAKE` (sem model calls) vs `OFFLINE_REPLAY` | Define se precisa Llama real, budget, provider live |
| **D2: Llama na web** | (a) Não usar Llama phase → `llama_provider=None` | Simplifica G3, mas perde fallback semântico V2.3.8 |
| | (b) Usar Llama real → precisa load/unload, model tag/digest fixos | Completo, mas complexo (GPU, model management) |
| **D3: Provider Adapter** | (a) `WebDurableResponseProvider` wrap do `transport_config` (primary/fallback) | Reutiliza infra web, mas fallback web ≠ fallback V2.3.8 |
| | (b) Provider dedicado V2.3.8 (separado do transport config) | Limpo, mas duplica configuração |
| **D4: Pipeline default** | Mudar `TRANSLATOR_PIPELINE` default para `v2_3_8` | Quebra compatibilidade se legacy ainda necessário |
| **D5: Exactly-once web** | Enforçar `OperationCallBudget` no adapter vs confiar no runner | Adapter enforça = mais seguro; runner = mais simples |

---

## 5. Riscos Identificados

| Risco | Probabilidade | Impacto | Mitigação |
|-------|---------------|---------|-----------|
| **R1**: Reconciliação canônica revela divergências irreconciliáveis | Média | Alto | Auditoria prévia completa (já feita) |
| **R2**: Adapter Provider introduz bugs de fallback | Média | Alto | Testes offline determinísticos + canary preflight |
| **R3**: Llama policy na web exige GPU/model management novo | Alta (se D2=b) | Médio | Preferir D2=a inicialmente |
| **R4**: Mudança de pipeline default quebra usuários existentes | Baixa | Médio | Feature flag + migração gradual |
| **R5**: Exactly-once falha por race condition no web layer | Baixa | Alto | Budget enforçado no provider (não no runner) |

---

## 6. Próximos Passos Imediatos (Esta Sessão)

1. ✅ Auditoria read-only completa
2. 📝 **Documento de Contratos Canônicos** (`docs/canonical-contracts-v2_3_8.md`)
3. 📝 **Plano de Migração** (`docs/migration-plan-v2_3_8-web.md`)
4. 📝 **Handoff** para próxima sessão com `/subtranslate-next`

---

## 7. Referências Cruzadas

- `PROJECT_STATE.json` (authority) — snapshot `READONLY_PROBE_20260822`
- `HANDOFF_CHATGPT.md` (authority) — addendums AUTO-03C/03D/03E
- Candidata commit `7eb7b5d` (v2.4.9) — 131 commits à frente do main
- `src/subtranslate/pipeline_registry.py` — registro V2.3.8
- `src/subtranslate/production_v2_3_8_adapter.py` — adapter V2.3.8
- `src/subtranslate/v238_llama_policy.py` — Llama policy V2.3.8
- `src/subtranslate/transport_providers.py` — providers web atuais
- `src/subtranslate/transport_config_store.py` — config web
- `src/subtranslate/web_retranslation_runner.py` — retradução já usa V2.3.8