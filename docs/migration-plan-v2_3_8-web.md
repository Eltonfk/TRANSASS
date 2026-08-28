# Plano de Migração: Web Layer → Runtime Canônico V2.3.8

**Versão**: 1.0  
**Data**: 2026-08-26  
**Base**: Gap Analysis + Contratos Canônicos  
**Status**: **DRAFT** — para design review e gate canônico

---

## 1. Objetivo

Conectar a camada web (UI, fila, state, transport config) ao **runtime canônico V2.3.8** como pipeline padrão para novas execuções, mantendo:
- Compatibilidade com jobs legacy existentes
- Exactly-once, durabilidade, lineage canônicos
- Fallback seguro (transport primary→fallback)
- Observabilidade completa

---

## 2. Escopo

| Incluído | Excluído |
|----------|----------|
| Tradução normal (`_run_episode`) via V2.3.8 | Llama phase real na web (D2=a: `llama_provider=None`) |
| Retradução (já usa V2.3.8) | Migração de state real/Library (separado) |
| Provider adapter (transport → DurableResponseProvider) | Promoção de candidata (gate de release separado) |
| Execution Context Factory | Alterações em `main` branch |
| Pipeline selection (default V2.3.8) | Escrita em `PROJECT_STATE.json` / `HANDOFF_CHATGPT.md` (via doc-sync) |

---

## 3. Fases e Entregáveis

### Fase 0: Reconciliação Canônica (Pré-requisito Obrigatório)

| Atividade | Responsável | Gate | Critério de Saída |
|-----------|-------------|------|-------------------|
| Rodar `subtranslate-canonical-reconciliation` skill | Agent | `USER_AUTHORIZATION_REQUIRED` (doc write) | `PROJECT_STATE.json` aditivo reflete v2.4.9 + V2.3.8 runtime |
| Atualizar `HANDOFF_CHATGPT.md` aditivo | Agent + Doc-sync | `USER_AUTHORIZATION_REQUIRED` | Histórico preservado, novo estado documentado |

> **BLOQUEIO**: Nenhuma implementação da Fase 1+ inicia sem Fase 0 concluída.

---

### Fase 1: Infraestrutura de Contratos (C1, C2, C4)

| Task | Arquivos | Estimativa | Validação |
|------|----------|------------|-----------|
| **T1.1** Execution Context Factory (`web_execution_context.py`) | Novo arquivo | 2-3h | Testes unitários: context válido para cada mode |
| **T1.2** Provider Adapter (`web_durable_provider.py`) | Novo arquivo | 3-4h | Testes: budget enforçado, fallback transporte, modes |
| **T1.3** Pipeline Selection Config + Endpoint | `transport_config_store.py`, `app.py` | 1-2h | GET/POST `/pipeline-config` funcional |
| **T1.4** Integração mínima em `_run_episode` (feature flag) | `app.py` | 2h | Dry-run V2.3.8 via flag `USE_V238=1` |

**Critério de Saída Fase 1**: Todos os testes offline passam; dry-run V2.3.8 executa sem model calls (mode=TEST_FAKE).

---

### Fase 2: Normal Translation Path (C5, C6)

| Task | Arquivos | Estimativa | Validação |
|------|----------|------------|-----------|
| **T2.1** Refatorar `_run_episode` para usar orchestrator V2.3.8 | `app.py` | 3-4h | Testes: job normal produz mesmo output que legacy (comparação) |
| **T2.2** Exactly-once web layer (budget + job idempotência) | `app.py`, `web_durable_provider.py` | 2h | Teste: re-submissão mesmo job → no-op |
| **T2.3** Feature flag `TRANSLATOR_PIPELINE=v2_3_8` default | `app.py`, env | 30min | Novos jobs usam V2.3.8; legacy opt-in |

**Critério de Saída Fase 2**: Tradução normal E2E via V2.3.8 produz resultados idênticos (ou melhores) que legacy em episódios de teste offline.

---

### Fase 3: Observabilidade e Compatibilidade (C7)

| Task | Arquivos | Estimativa | Validação |
|------|----------|------------|-----------|
| **T3.1** Métricas V2.3.8 no job telemetry | `app.py:_job_telemetry` | 1h | `/jobs/<id>` expõe `v238_metrics` |
| **T3.2** Logs estruturados por stage | `app.py:_consume_worker_output_line` | 1h | Logs técnicos mostram stage V2.3.8 |
| **T3.3** Compatibilidade state JSONL (archive, lineage) | `app.py:_persist_job_result` | 1h | State existente legível, novos campos adicionados |

**Critério de Saída Fase 3**: Dashboard/UI mostra métricas V2.3.8; state JSONL compatível.

---

### Fase 4: Validação Canária (Preflight)

| Atividade | Skill | Gate | Critério |
|-----------|-------|------|----------|
| Canary preflight read-only (1 batch) | `subtranslate-canary` | `HUMAN_GATE_B4_RECOVERY_CALL_EXECUTION` | Payload identity, 1 call, 0 retry, forced stop, auditoria pós |
| Canary live (1 episódio real) | Manual + `subtranslate-audit` | `USER_AUTHORIZATION_REQUIRED` | Qualidade ≥ legacy, retries funcionando, métricas coletadas |

---

### Fase 5: Cutover e Limpeza

| Atividade | Critério |
|-----------|----------|
| Default `TRANSLATOR_PIPELINE=v2_3_8` em produção | Canary live aprovado |
| Remover feature flag legacy | 1 semana sem regressões |
| Documentar runbook de rollback | Pronto antes do cutover |

---

## 4. Cronograma Estimado (Otimista)

| Fase | Duração | Dependências |
|------|---------|--------------|
| **Fase 0** | 1-2 dias (processo documental) | Autorização usuário |
| **Fase 1** | 1-2 dias | Fase 0 concluída |
| **Fase 2** | 1-2 dias | Fase 1 concluída |
| **Fase 3** | 0.5-1 dia | Fase 2 concluída |
| **Fase 4** | 1-2 dias (inclui aguardo autorização) | Fase 3 concluída |
| **Fase 5** | 0.5 dia | Fase 4 aprovada |
| **Total** | **~1-2 semanas** | — |

---

## 5. Rollback Plan

| Cenário | Ação | Tempo |
|---------|------|-------|
| **Bug crítico em produção V2.3.8** | `TRANSLATOR_PIPELINE=legacy` via env / UI | <5 min |
| **State corrompido** | Restore `state/` do backup diário | <30 min |
| **Provider adapter falha** | Feature flag `USE_V238=0` | <1 min |
| **Métricas ausentes** | Log fallback + alerta | <10 min |

**Backups obrigatórios antes do cutover**:
- `state/` completo (JSONL + transport_config + ledger)
- `transport_config.json` (chaves incluídas)
- Docker image tag `pre-v238-cutover`

---

## 6. Riscos e Mitigações

| Risco | Prob. | Impacto | Mitigação |
|-------|-------|---------|-----------|
| **R1**: Reconciliação canônica revela gaps irreconciliáveis | Média | Alto | Auditoria prévia completa (já feita); design review antecipado |
| **R2**: Adapter Provider introduz regressão de fallback | Média | Alto | Testes offline exaustivos + canary preflight obrigatório |
| **R3**: Exactly-once falha por race no web layer | Baixa | Alto | Budget enforçado no provider (não no runner); job idempotência |
| **R4**: Mudança de default quebra usuários | Baixa | Médio | Feature flag + migração gradual + comunicação |
| **R5**: Llama phase necessário no futuro (D2=b) | Média | Médio | Arquitetura preparada (C3); implementar quando necessário |

---

## 7. Responsáveis e Gates

| Papel | Responsável | Gates de Autorização |
|-------|-------------|---------------------|
| **Design Review** | `subtranslate-review` | Após Fase 1 (contratos C1-C7) |
| **Auditoria Read-only** | `subtranslate-audit` | Pré-Fase 0, Pré-Fase 4 |
| **Reconciliação Canônica** | `subtranslate-doc-sync` | `USER_AUTHORIZATION_REQUIRED` (doc write) |
| **Canary Preflight** | `subtranslate-canary` | `HUMAN_GATE_B4_RECOVERY_CALL_EXECUTION` |
| **Canary Live** | Usuário + Agent | `USER_AUTHORIZATION_REQUIRED` (model call) |
| **Cutover Produção** | Usuário | `USER_AUTHORIZATION_REQUIRED` (deploy) |

---

## 8. Checklist de Pronto para Próxima Sessão

- [x] Auditoria read-only completa (`subtranslate-audit`)
- [x] Gap Analysis documentado (`docs/gap-analysis-web-v2_3_8.md`)
- [x] Contratos Canônicos documentados (`docs/canonical-contracts-v2_3_8.md`)
- [x] Plano de Migração documentado (`docs/migration-plan-v2_3_8-web.md`)
- [ ] **Próxima sessão**: `/subtranslate-next` → inicia Fase 0 (reconciliação canônica) após autorização

---

## 9. Comandos de Início Rápido (Próxima Sessão)

```bash
# 1. Verificar estado
cd /home/palhacinho/codex-projects/subtranslate-v238-candidate
git status
git log --oneline -3

# 2. Ler documentos preparados
cat docs/gap-analysis-web-v2_3_8.md
cat docs/canonical-contracts-v2_3_8.md
cat docs/migration-plan-v2_3_8-web.md

# 3. Iniciar reconciliação canônica (requer autorização)
# /subtranslate-next  →  dispara skill subtranslate-canonical-reconciliation
```

---

## 10. Referências

- `PROJECT_STATE.json` (authority) — snapshot `READONLY_PROBE_20260822`
- `HANDOFF_CHATGPT.md` (authority) — addendums AUTO-03C/03D/03E
- Candidata commit `7eb7b5d` (v2.4.9)
- `docs/gap-analysis-web-v2_3_8.md`
- `docs/canonical-contracts-v2_3_8.md`
- AGENTS.md — regras de autorização, fail-closed, evidência histórica