# Resumo histórico — linha V2.3.8 e origem do Transass Web

Release de contexto (sem código novo). Resume os marcos anteriores à v2.4.7
para quem chega agora ao projeto.

## Origem

- Projeto iniciado como **Subtranslate**: tradutor de legendas de anime
  (ASS/SSA) para português do Brasil, com preservação estrutural e de karaokê.
- Renomeado para **Transass**; pacote interno permanece `src/subtranslate`.

## Fundações (linha V2.3.8)

- **Runtime canônico V2.3.8**: estágio de tradução full durável sobre a base
  V226 materializada, política llama de fallback único em grupo,
  `DurableResponseProvider` com modos LIVE_CAPTURED / OFFLINE_REPLAY /
  TEST_FAKE, orçamento de chamadas por operação, capturas duráveis e replay
  determinístico.
- Prova de execução feita por gates dedicados (B4/B5) com tooling próprio —
  o caminho web ainda não está conectado a esse runtime (planejamento em
  andamento, com gate e auditoria próprios).
- **Pipelines registrados**: legacy → v2_1_2 … v2_2_6 → v2_3_0 → v2_3_8,
  todos com archive e lineage na biblioteca.

## Transass Web

- App Flask com fila exactly-once, persistência atômica de estado, publicação
  segura por rename no mesmo diretório.
- Biblioteca SQLite: séries/episódios/objetos dedupe por SHA-256, registros
  versionados com lineage, classificações ANIME/NON_ANIME/UNKNOWN.
- Transportes plugáveis (ollama / openai_compat / gemini) com fallback
  primário→secundário configurável pela UI.
- Auditoria por episódio/série com flags objetivos; retradução com pré-flight
  fail-closed preservando versões antigas; memória de tradução aprovada;
  glossário por série.

## Estado atual

Ver CHANGELOG.md — série 2.4.x levou multi-idioma, auto-classificação e o
pipeline v2_3_0 ao uso real pela web, com correções de seleção, faixas
embutidas e idioma de retradução validadas em produção.
