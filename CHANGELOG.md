# Changelog

Todas as mudanças notáveis do **Subtranslate** são documentadas neste arquivo.
Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/)
e versionamento [SemVer](https://semver.org/lang/pt-BR/).

## [2.4.0] — 2026-08-25

Primeira release versionada. Consolida a linha canônica V238 com a temporada
completa traduzida, motor de tradução escolhível e toolchain de operação
generalizada.

### Adicionado
- **Tradução integral da temporada Zombie Land Saga (E07–E12)**: 10.549 eventos,
  8.372 traduções aplicadas, 2.177 eventos preservados por design (músicas,
  signos, técnicos), 1.266 lotes executados com zero retries silenciosos.
- **Motor de tradução escolhível** (`transport` no config do episódio):
  `ollama` (local/GPU), `openai_compat` (Groq, OpenRouter, LM Studio, vLLM,
  llama.cpp server), `gemini` (Google free tier). API keys exclusivamente por
  variável de ambiente.
- **Pipeline de episódio genérico**: `episode_config_builder`,
  `episode_planner --plan-all` (inventário completo do episódio em uma única
  passada determinística), `episode_range_runner` (authorize/status/execute/
  recover-batch/reconcile-batch/retry-failed/finalize-retries),
  `episode_assembly`.
- **Probe somente-leitura v0.4.1**: snapshot íntegro com lock transitório
  tratado como estado normal; `app_version` exposta; `context_inspect --summary`
  com inventário de chaves canônicas (`canonical_keys`).
- **Higiene R2** (`subtranslate_hygiene_detach.py`): migração episode-agnóstica
  de famílias reconciliadas para o history root com manifest completo e
  rollback guiado.
- **Reconciliação documental** (`subtranslate_doc_reconcile.py`): objetos
  aditivos atômicos com backup e verificação pós-escrita.
- Deploy web em produção com o pipeline V238 completo (imagem
  `v2.3.8-dockerfile-rc4567`).

### Alterado
- `/health` expõe `version`; novo endpoint `/version`.
- Executor de lote transport-aware: request real enviado à API escolhida é a
  evidência durável gravada.
- Retomada idempotente: payloads, backups e autorizações toleram re-execução
  após interrupções.

### Conhecido
- Lotes com parse failure persistente (E09·B150/B194, E10·B2, E11·B96,
  E12·B47, E08 evento 1486) ficam sinalizados para a revisão humana única —
  textos originais preservados nas legendas.

## [2.3.8] — linha V238 (estado original)

- Pipeline canônico V238: durabilidade por família (ledgers exactly-once),
  normalização V3, semantic style ownership, seletores RC3–RC10,
  prompt contracts RC7a–RC7b1, materialização base.
- App web Flask para auditoria, retradução seletiva, Library
  (subtitle_record/review_session/human_feedback), glossário versionado e
  memória de tradução.
- Toolchain operacional: probe somente-leitura, transições canônicas,
  backup canônico, recovery ledger reprepare.
- Sem deploy público, sem release versionada, sem remote Git.
