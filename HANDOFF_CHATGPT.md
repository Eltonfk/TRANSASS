

---

3# Addendum 2026-09-06 — CONSOLIDACAO-ATOMICA-UNLRACKED-AUDITORIA-V238

Consolidação atômica dos módulos untracked identificados durante a auditoria arquitetural. Todas as alterações foram isoladas em commits independentes na branch `candidate/v2.3.8` para garantir auditabilidade total e reversibilidade simples via `git revert`.

### Commits Realizados:
1. `ba228b3` — `feat(infra): add credential store, thermal guard and runtime config modules`
   - Módulos: `credential_store.py`, `diagnostics.py`, `gpu_thermal_guard.py`, `runtime_config.py`, `runtime_paths.py`, `state_migration.py`.
2. `ff92c9e` — `feat(web): add onboarding assets and client-side i18n localization`
   - Assets: `i18n.js`, `onboarding.css`, `onboarding.js`.
3. `eb34b53` — `feat(desktop): add desktop app package, specification and GitHub workflow`
   - Módulos: `desktop/`, `docs/DESKTOP_PLAN.md`, `.github/workflows/desktop.yml`.
4. `ad1606a` — `test(offline): add tests for thermal guard, comment preservation and ui localization`
   - Testes: `conftest.py`, `test_ass_comment_preservation.py`, `test_gpu_thermal_guard.py`, `test_pipeline_v226_regressions.py`, `test_ui_localization.py`.

### Instruçôes de Reversão para OpenCode e Codex:
Para reverter qualquer um dos blocos individualmente sem afetar os demais:
- Reverter Infraestrutura: ggit revert ba228b3`
- Reverter Frontend/i18n: git revert ff92c9e`
- Reverter Módulo Desktop: git revert eb34b53`
- Reverter Testes Offline: `git revert ad1606a`
- Reverter Todos os Commits da Auditoria: `git revert ba228b3^..ad1606a`

STATUS_TESTES_OFFLINE=100% PASS (965 selected / 0 failures)
FUTURE_SIDE_EFFECTS_AUTHORIZED=false


---

### Addendum 2026-09-06 — EXECUCAO-PLANO-ACAO-PRAGMATICO-V238

Execução do Plano de Ação Pragmático autorizado pelo usuário no repositório  na branch .

#### Ações Executadas:
1. **Remoção de Código Morto e Módulos Obsoletos ():**
   - Removidos os 6 arquivos de facade  sem uso no ecossistema V2.3.8.
   - Removido o diretório completo  (~2800 linhas de código legado não referenciado pela aplicação).
   - Removidas as suítes de teste obsoletas vinculadas ao  em .
   - Desacoplado  da dependência direta de .
   - **Commit:**  — 

2. **Otimização de Concorrência e Runtime:**
   - Mantida execução in-process no  para tarefas v2.3.8 e isolamento de estado de processo do servidor web.

#### Instruções de Reversão para OpenCode e Codex:
Para reverter a remoção do código morto:
- 

STATUS_TESTES_OFFLINE=100% PASS (879 selected / 0 failures)
FUTURE_SIDE_EFFECTS_AUTHORIZED=false


---

### Addendum 2026-09-06 — EXECUCAO-PLANO-ACAO-PRAGMATICO-V238

Execução do Plano de Ação Pragmático autorizado pelo usuário no repositório /home/palhacinho/codex-projects/subtranslate-v238-candidate na branch candidate/v2.3.8.

#### Ações Executadas:
1. **Remoção de Código Morto e Módulos Obsoletos (refactor(core)):**
   - Removidos os 6 arquivos de facade pipeline_v2_2_[1-6].py sem uso no ecossistema V2.3.8.
   - Removido o diretório completo src/subtranslate/recovery_guard/ (~2800 linhas de código legado não referenciado pela aplicação).
   - Removidas as suítes de teste obsoletas vinculadas ao recovery_guard em tests/offline/.
   - Desacoplado production_v2_3_0_adapter.py da dependência direta de production_v2_2_6_adapter.py.
   - **Commit:** 19a08f0 — refactor(core): remove dead v2.2.x facades, recovery_guard module and decouple v2.3.0 adapter

2. **Otimização de Concorrência e Runtime:**
   - Mantida execução in-process no app.py para tarefas v2.3.8 e isolamento de estado de processo do servidor web.

#### Instruções de Reversão para OpenCode e Codex:
Para reverter a remoção do código morto:
- git revert 19a08f0

STATUS_TESTES_OFFLINE=100% PASS (879 selected / 0 failures)
FUTURE_SIDE_EFFECTS_AUTHORIZED=false
