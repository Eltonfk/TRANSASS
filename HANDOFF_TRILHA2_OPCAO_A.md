# HANDOFF — Trilha 2 (Opção A Canônica): Web ↔ Runtime V2.3.8

**Sessão origem**: `candidate/v2.3.8` commit `7eb7b5d` (v2.4.9)  
**Data**: 2026-08-26  
**Próxima sessão**: Iniciar com `/subtranslate-next`

---

## Contexto Atual

### Produção (v2.4.9 adaptado)
- **Pipeline**: `v2_3_0` + French residue (v2.4.9) + Gemini primário + Ollama fallback
- **Status**: Saudável, canário E01 pendente (usuário fará na UI)
- **Transport**: Gemini `gemini-3.6-flash` primário, Ollama `qwen3.5:9b` fallback, chave preservada

### Candidata (branch `candidate/v2.3.8`)
- **HEAD**: `7eb7b5d` — v2.4.9 (French residue + Gemini swap)
- **131 commits à frente do main** (`05b5827`)
- **Runtime V2.3.8 implementado mas NÃO conectado à web**:
  - `pipeline_registry.py` registra V2.3.8
  - `production_v2_3_8_adapter.py` + `v238_full_translation_stage.py` + `v238_base_materializer.py`
  - `v238_llama_policy.py` (budget, llama provider, load/unload)
  - `pipeline_orchestrator.py` despacha planos
- **Web layer usa legacy para tradução normal** (`_run_episode` → subprocess)
- **Retradução JÁ usa V2.3.8** via `web_retranslation_runner` → orchestrator

### Estado Canônico (authority)
- **PROJECT_STATE.json**: `READONLY_PROBE_20260822` — **STALE** (não reflete v2.4.9 nem V2.3.8 runtime)
- **HANDOFF_CHATGPT.md**: Addendums até AUTO-03E (E07-E12 completa, revisão humana pendente)
- **Reconciliação canônica OBRIGATÓRIA** antes de qualquer side effect (AGENTS.md)

---

## Documentos Preparados (na candidata)

| Arquivo | Conteúdo |
|---------|----------|
| `docs/gap-analysis-web-v2_3_8.md` | 11 gaps (5 críticos), matriz de dependências, decisões D1-D5, riscos |
| `docs/canonical-contracts-v2_3_8.md` | 7 contratos (C1-C7): Context Factory, Provider Adapter, Llama Policy, Pipeline Selection, Normal Path, Exactly-Once, Observabilidade |
| `docs/migration-plan-v2_3_8-web.md` | 5 fases + Fase 0 obrigatória, cronograma ~1-2 sem, rollback plan, gates, responsáveis |

---

## Próximos Passos (Ordem Estrita)

### 1. **Fase 0: Reconciliação Canônica** (BLOQUEIO)
- **Skill**: `subtranslate-canonical-reconciliation`
- **Input**: `PROJECT_STATE.json` (stale) + runtime evidence (commits 82-131, v2.4.9 artifacts)
- **Output**: Novo `PROJECT_STATE.json` aditivo + `HANDOFF_CHATGPT.md` aditivo
- **Gate**: `USER_AUTHORIZATION_REQUIRED` para escrita documental
- **Não prosseguir sem isso** — AGENTS.md proíbe side effects com estado canônico stale

### 2. **Design Review** (após Fase 0)
- **Skill**: `subtranslate-review`
- **Escopo**: Contratos C1-C7 em `docs/canonical-contracts-v2_3_8.md`
- **Gate**: Aprovação independente antes de implementação

### 3. **Fase 1: Infraestrutura** (C1, C2, C4)
- `web_execution_context.py` (Context Factory)
- `web_durable_provider.py` (Provider Adapter)
- Pipeline selection config + endpoint
- Feature flag `USE_V238` em `_run_episode`

### 4. **Fase 2: Normal Path** (C5, C6)
- Refatorar `_run_episode` para orchestrator V2.3.8
- Exactly-once web layer (budget + job idempotência)
- Default `TRANSLATOR_PIPELINE=v2_3_8`

### 5. **Fase 3: Observabilidade** (C7)
- Métricas V2.3.8 no job telemetry
- Logs estruturados por stage
- Compatibilidade state JSONL

### 6. **Fase 4: Canary Preflight**
- **Skill**: `subtranslate-canary` (read-only, 1 batch, payload identity, 1 call, 0 retry, forced stop)
- **Gate**: `HUMAN_GATE_B4_RECOVERY_CALL_EXECUTION`
- Canary live (1 episódio real) → `USER_AUTHORIZATION_REQUIRED`

### 7. **Fase 5: Cutover**
- Default V2.3.8 em produção
- Rollback plan testado

---

## Comandos para Iniciar Próxima Sessão

```bash
cd /home/palhacinho/codex-projects/subtranslate-v238-candidate

# 1. Verificar estado
git status
git log --oneline -3

# 2. Ler documentos
cat docs/gap-analysis-web-v2_3_8.md
cat docs/canonical-contracts-v2_3_8.md
cat docs/migration-plan-v2_3_8-web.md

# 3. Executar /subtranslate-next
#    → dispara subtranslate-canonical-reconciliation (Fase 0)
#    → requer sua autorização para escrita documental
```

---

## Lembretes Críticos

| Regra | Fonte |
|-------|-------|
| **Não modificar `PROJECT_STATE.json` / `HANDOFF_CHATGPT.md` sem autorização documental** | AGENTS.md |
| **Não executar model calls / transports / batches sem gate explícito** | AGENTS.md + skills |
| **Não misturar hotfixes (v2.4.9) com mudança arquitetural (Opção A)** | Decisão desta sessão |
| **Auditoria read-only antes de qualquer declaração de PASS** | `subtranslate-evidence-audit` skill |
| **Fail-closed sempre** | Constituição operacional |

---

## Estado do Canário Atual (Trilha 1 — Paralelo)

| Item | Status |
|------|--------|
| French residue detection (v2.4.9) | ✅ Deployado, 42 testes passando |
| Gemini primário + Ollama fallback | ✅ Via API, chave preservada |
| `source_language` global corrigido | ✅ `inglês` (com acento) |
| **Validação E01 (francês + Gemini + retry)** | ⏳ **PENDENTE** — usuário fará na UI |
| Git push + releases | ✅ Usuário confirmou "feitos e ok" |

> A Trilha 1 (canário operacional) roda em paralelo. Quando o usuário reportar o resultado do E01, a Trilha 1 fecha. A Trilha 2 (esta) é independente e arquitetural.

---

## Contatos / Escalação

- **Dúvidas sobre contratos**: `subtranslate-review` (design review independente)
- **Dúvidas sobre estado canônico**: `subtranslate-audit` (read-only) + `subtranslate-canonical-reconciliation` (doc write)
- **Dúvidas sobre canário**: `subtranslate-canary` (preflight read-only)
- **Autorizações**: Sempre `USER_AUTHORIZATION_REQUIRED` (token literal `AUTORIZAR` na sessão)

---

**Fim do handoff. Próxima sessão inicia com `/subtranslate-next` → Fase 0 (Reconciliação Canônica).**