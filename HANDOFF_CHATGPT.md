

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

---

### Addendum 2026-09-06 — EXECUCAO-PLANO-ACAO-PRAGMATICO-DESKTOP

Execução do Plano de Ação Pragmático (Desktop) autorizado pelo usuário no repositório `subtranslate-v238-candidate` na branch `candidate/v2.3.8`.

#### Ações Executadas:
1. **Otimização Extrema de Escopo (Desktop):**
   - Removida a dependência arquitetural da `QWebEngineView` (Chromium embutido) no `launcher.py`. A janela desktop agora consiste em um painel nativo minimalista e lança automaticamente o aplicativo no navegador padrão do usuário via `webbrowser`. Isso economiza centenas de megabytes no build final e otimiza severamente a alocação de RAM no runtime.
2. **Refatoração Semântica de Testes:**
   - Testes `test_phase4.py` e `test_phase6.py` renomeados semanticamente para `test_diagnostics_migration.py` e `test_runtime_identity.py`, eliminando a nomenclatura defasada.
   - **Commit:** `5b4c43c` — `refactor(desktop): drop QWebEngineView for lightweight native UI and rename legacy test phases`

#### Instruções de Reversão para OpenCode e Codex:
Para reverter a otimização de escopo do desktop e voltar a embutir o Chromium:
- `git revert 5b4c43c`

STATUS_TESTES_OFFLINE=100% PASS (desktop pytest validation ok)
FUTURE_SIDE_EFFECTS_AUTHORIZED=false


---

### Addendum 2026-09-06 — CORRECAO-RESOLUCAO-PATHS-TRANSPORT-CONFIG-LOCAL

Resolução do erro de permissão [Errno 13] '/app/state/transport_config.json' reportado durante a execução do Transass local.

#### Diagnóstico e Causa Raiz:
- O módulo `src/subtranslate/runtime_config.py` possuía uma falha na lógica de fallback de `default_state_dir()` e `default_media_root()`: quando variáveis de ambiente lidas de `.env` continham caminhos de container (`/app/state` ou `/shows`), a verificação de desvio falhava e caía na expressão `or configured`, reaceitando `/app/state` involuntariamente.
- No `src/subtranslate/app.py`, a variável `TRANSPORT_CONFIG_PATH` estava hardcoded como `STATE_DIR / "transport_config.json"`, ignorando `RUNTIME_CONFIG.transport_config` e a sobrescrita dinâmica de ambiente feita pelo launcher Desktop/local.

#### Ações Executadas:
1. **`src/subtranslate/runtime_config.py`**: Ajustada a resolução de desvio para rejeitar estritamente pseudônimos de container (`/app/state`, `/shows`) e utilizar o diretório local do usuário (`~/.local/share/transass/state`).
2. **`src/subtranslate/app.py`**: Atualizado `TRANSPORT_CONFIG_PATH` para utilizar a propriedade unificada `RUNTIME_CONFIG.transport_config`.
3. **`desktop/src/transass_desktop/paths.py`**: Sincronizada a resolução de `state_dir` e `media_root` para ignorar caminhos de container.
4. **`desktop/tests/test_paths.py`**: Adicionado caso de teste automatizado `test_container_state_aliases_are_bypassed`.
- **Commit:** `d69a828` — `fix(runtime): resolve local transport config path and bypass container aliases`

STATUS_TESTES_OFFLINE=100% PASS (904 passed, 0 failures)
FUTURE_SIDE_EFFECTS_AUTHORIZED=false
