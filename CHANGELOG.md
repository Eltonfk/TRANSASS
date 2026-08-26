# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Versão única de verdade: `src/subtranslate/_version.py` (consumida por `/health`,
`/version` e tooling). Atualizar em conjunto com a tag anotada no Git.

## [2.4.7] - 2026-08-26

### Corrigido
- **Retradução com idioma errado**: o runner resolvia o idioma como
  `config global → ambiente`; como o store sempre materializa um default
  não-vazio ("inglês"), o prompt saía *"traduza de inglês"* mesmo com texto
  francês — causa principal do resíduo francês nas retraduções.
  Precedência agora: **ambiente por job → config global → inglês**.
- Idioma de origem por episódio ligado ponta-a-ponta na retradução:
  UI envia a seleção → `/retranslate(/preflight)` → resolução da fonte →
  campo `source_language` no job → ambiente do processo do runner.

## [2.4.6] - 2026-08-26

### Corrigido
- **NameError em `web_retranslation_runner._run_pipeline`**
  (`transport_config` fora de escopo) — toda retradução falhava com código 1.
- **Escolha de faixa embutida**: faixas "Forced"/"Signs & Songs" do idioma
  configurado perdiam para a faixa de diálogo completa; desempate
  determinístico por flag `default` e índice. ("French [Forced]" era
  escolhida no lugar de "French [Full]" no Paranoia Agent S01E01.)
- Caminho V226 honra `TRANSLATOR_SOURCE_LANGUAGE` do ambiente quando não há
  `execution_context` (necessário para o plano v2_3_0 via orquestrador).

### Alterado
- Pipeline padrão da web: `v2_3_8` → `v2_3_0`. O plano v2_3_8 exige o
  contexto canônico de execução live (`response_provider` + identidade de
  checkpoint), ainda não conectado ao caminho web — a primeira tentativa real
  falhou fechada (`V238_EXECUTION_CONTEXT_REQUIRED`), sem publicar nada.
  Conectar o runtime canônico à web é trabalho futuro planejado, com gate e
  auditoria próprios.

## [2.4.5] - 2026-08-26

### Corrigido
- **Seleção de episódios individuais**: a UI envia o caminho completo na
  biblioteca (`ep.source`); `_selected_sources` duplicava o caminho
  (`pasta + caminho`) e devolvia "seleção de episódios inválida".
  Agora aceita as duas formas, mantendo contenção em `BASE_LIBRARY`.
- Badge de fonte reflete o **idioma selecionado** (antes sempre o global
  "inglês"): `/episodes?source_language=…`, cache por idioma e novo endpoint
  `GET /source-status` com atualização imediata ao trocar o idioma.

## [2.4.4] - 2026-08-26

### Corrigido
- Auto-classificação ANIME: resolução do caminho absoluto do vídeo antes da
  detecção (relative path causava `no_embedded_ass_ssa`). Verificado em
  produção: Paranoia Agent/Season 1 → 13 episódios catalogados.

## [2.4.3] - 2026-08-26

### Adicionado
- `POST /library/auto-classify`: classifica série como ANIME quando vídeos da
  pasta possuem ASS/SSA embutido; registra episódios; respeita NON_ANIME
  explícito. Disparado pelo botão "Usar esta pasta". 6 testes offline.

## [2.4.2] - 2026-08-26

### Corrigido/Alterado
- `/source-options` aceita `path` relativo do vídeo além de `episode_id`
  (detecção funciona em qualquer pasta navegada, não só catálogo ANIME);
  seletor por episódio compacto.

## [2.4.0 – 2.4.1] - 2026-08-25

### Adicionado
- **Idioma de origem configurável (qualquer idioma → PT-BR)** com preservação
  de karaokê/letras; mapas de códigos de idioma; prompts parametrizados.
- Detecção de todos os idiomas de legenda do vídeo; seletor por episódio e
  seletor por temporada com botão "Detectar"; configuração de transporte
  (primário/fallback) pela UI incluindo idioma global.

---

## Histórico anterior (linha V2.3.8 / Transass Web) — resumo

- **Renomeação** Subtranslate → **Transass** (pacote interno `src/subtranslate`).
- **App web Flask**: fila com exactly-once, sessões, persistência atômica de
  estado, publicação por rename no mesmo diretório, polling compacto.
- **Biblioteca SQLite** (`anime_subtitle_library`): séries/episódios/objetos
  com dedupe por SHA-256, registros com lineage, ingest hooks de fonte
  extraída/externa/traduzida, classificações ANIME/NON_ANIME/UNKNOWN.
- **Pipelines registrados** (`pipeline_registry`): legacy → v2_1_2 … v2_2_6,
  v2_3_0 (full + aumento de karaokê) e v2_3_8 (estágio durável + karaokê),
  com archive/lineage por plano.
- **Runtime canônico V2.3.8**: estágio de tradução full durável, política
  llama de fallback único em grupo, `DurableResponseProvider`
  (LIVE_CAPTURED / OFFLINE_REPLAY / TEST_FAKE), materializador base V226,
  orçamento de chamadas, capturas duráveis e replay — provado pelos gates
  B4/B5 via tooling dedicado (fora do caminho web).
- **Transportes plugáveis**: ollama / openai_compat / gemini com fallback
  primário→secundário configurável pela UI.
- **Qualidade/auditoria**: validação estrutural, flags
  (POSSIBLE_UNTRANSLATED_OUTPUT, SHORT_ENGLISH_POSSIBLE, …), auditoria por
  episódio/série, retradução com pré-flight fail-closed, memória de tradução
  aprovada e glossário por série.
