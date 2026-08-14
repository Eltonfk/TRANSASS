# SUBTRANSLATE — MASTER ROADMAP AUTÔNOMO PARA O CODEX
## V2.2.6 → SIGNS S04 → KARAOKE V2.3.0 → GLOSSÁRIO → S04 FINAL → GENERALIZAÇÃO → HARDENING → GITHUB READY → MODEL BENCHMARK LAB

**Versão:** 1.1  
**Data:** 2026-08-13  
**Base:** 1.0  
**Motivo da revisão:** `MANDATORY_ARCHITECTURE_REPOSITORY_DOCUMENTATION_FILESYSTEM_PRODUCTION_CONSOLIDATION`  
**Modo:** AUTÔNOMO COM GATES, FAIL-CLOSED

## 0. COMO USAR
Este arquivo é a fonte de execução desta macrosequência.

O Codex deve:
1. ler este documento inteiro antes de executar;
2. ler `HANDOFF_CHATGPT.md` e `PROJECT_STATE.json`;
3. reconciliar este documento com o estado real atual;
4. considerar o estado real mais recente como fonte operacional quando houver diferença factual;
5. preservar todas as regras de segurança, rollback, versionamento e fail-closed daqui;
6. avançar automaticamente quando um gate puder ser decidido objetivamente;
7. parar somente em gates realmente dependentes de decisão humana, autorização externa, nova classe semântica/estrutural ou falha objetiva;
8. atualizar `HANDOFF_CHATGPT.md` e `PROJECT_STATE.json` ao final de cada etapa relevante.

## 1. ESTIMATIVA GLOBAL
Se todas as fases puderem avançar:
- cenário rápido: 15–20 h;
- cenário normal: 20–30 h;
- com segundo anime complexo / regressões / clean-install: 30–40+ h.

Prioridade:
SEGURANÇA → EVIDÊNCIA → GATES → PRESERVAÇÃO → QUALIDADE → AUTONOMIA.

## 2. REGRA MESTRA
PASS objetivo → avançar automaticamente.
FAIL objetivo → parar imediatamente com evidências.
Nova classe semântica/estrutural não prevista → `NEW_PIPELINE_VERSION_REQUIRED`.
Revisão humana → somente quando realmente indispensável; não usar como parada por conveniência.

## 3. ESTADO ATUAL CONHECIDO
Produção:
- pipeline `v2_2_5`
- model `qwen3.5:9b`
- healthy
- fila vazia
- `S04_V225_GENERATION_COMPLETE`

S04:
- E06 record/object 84
- E07–E12 records/objects 85–90
- SHORT_ENGLISH_RESIDUAL=0
- zero falhas estruturais
- geração V2.2.5 sem publicações automáticas
- sidecars e Translation Memory preservados

Observabilidade:
- failure ledger persistente por unidade/tentativa;
- retenção limitada;
- snapshot atômico;
- progresso web real: unidades, estágio, calls, retries, budget, duração, last activity.

## 4. V2.2.6 — ESTADO
Candidate:
- `v2_2_6`
- S04E02 record/object 91
- SHA-256 `516875...5dae`
- 36964/36964
- 109 frames de signs ajustados
- estrutura preservada
- diálogos intactos
- `blocking_flags=[]`
- ainda não promovida

Recursos:
- `SIGN_SEMANTIC_ID`
- fan-out determinístico
- validators de consistência por grupo

Auditoria signs S04:
- 2.943 candidatos
- 1.230 traduzidos
- 856 parciais
- 107 inglês preservado
- 750 não linguísticos/desenho
- 43 grupos animados
- 16 mistos
- 27 animação original
- zero divergência estrutural em timestamps/layers/styles/tags/\N
- 41 candidatos de diálogo residual, nenhum confirmado
- 28 hints preliminares de karaoke

## 5. REVIEW HUMANO V2.2.6
Registrar:
`V226_HUMAN_PLAYBACK_REVIEW=PASS`
`PLAYER_REFERENCE=MPV`

A primeira impressão negativa no Firefox foi invalidada porque o playback estava dessincronizado. No MPV, o usuário confirmou sincronização correta e signs/placas visualmente corretos. Não repetir o gate por causa do Firefox/Jellyfin Web dessincronizado.


## 6. FASE 1 — PROMOVER V2.2.6
Ler estado e relatórios V2.2.6. Confirmar record 91, hash, ASS/libass, lineage, blocking_flags e regressões.

GATE 1:
Se PASS:
`TRANSLATOR_PIPELINE=v2_2_6`
Modelo continua `qwen3.5:9b`.
V2.2.5 permanece rollback imediato.

## 7. FASE 2 — SIGN AUGMENTATION DA S04
Objetivo: aplicar consistência de signs à S04 inteira sem retraduzir diálogos.

Fluxo preferido:
BEST VALIDATED CANDIDATE → SIGN AUGMENTATION → NEW VALIDATED CANDIDATE.

Escopo: S04E01–E12.

Para cada episódio:
1. best validated candidate;
2. preservar diálogos;
3. preservar songs/karaoke;
4. detectar sign groups elegíveis;
5. aplicar SIGN_SEMANTIC_ID;
6. traduzir identidade semântica uma vez quando necessário;
7. fan-out determinístico;
8. validar/auditar;
9. arquivar.

Preservar:
timestamps, Layer, Style, tags, \N, drawings, pos/move/clip/alpha/fade/transforms, diálogos.

Serial + fail-fast.
PASS → próximo.
BLOCKING FAIL → parar, cauda NOT_STARTED_AFTER_FAILURE.

Não publicar automaticamente.

GATE 2:
- todos episódios concluídos;
- zero structural critical;
- zero mixed equivalent EN/PT groups;
- sign-group consistency PASS;
- diálogos preservados.
Marcar `S04_SIGN_AUGMENTATION_COMPLETE` e continuar.


## 8. MACROFASE — KARAOKE DISCOVERY READ-ONLY
Antes de programar, auditar a representação REAL na S04.

Usar `S04_PRELIMINARY_SONG_STYLE_HINTS.json` e sources/candidates reais.

Inventariar:
OP, ED, insert songs, romaji, original script, English translation layer, effects, \k/\K/\kf/\ko, overlaps, styles, layers, alignment, position, duplicates, animated song events.

Não depender só de Style. Combinar:
language, position, timing, overlap, Style, tags, karaoke tags, repetition, semantic similarity.

Classificações:
- SONG_ROMAJI
- SONG_ORIGINAL_SCRIPT
- SONG_TRANSLATION
- SONG_EFFECT
- SONG_KARAOKE_SYLLABIC_TRANSLATION
- SONG_UNKNOWN

Somente SONG_TRANSLATION é automaticamente elegível para EN→PT-BR.
Preservar romaji/original/effect/unknown.

Se translation layer tiver timing silábico \k/\K/\kf/\ko:
classificar `KARAOKE_TRANSLATION_TIMING_UNSUPPORTED`, preservar, não retimar nesta fase.

GATE 3:
classificação genérica sem hardcode da release. Criar relatório/fixtures e continuar.


## 9. V2.3.0 — KARAOKE TRANSLATION LAYER
Criar `v2_3_0`, base `v2_2_6`.

Princípio:
Qwen recebe SOMENTE conteúdo linguístico de SONG_TRANSLATION.
Qwen não reconstrói tags, timing, posição, animação, Style, Layer, drawings.

Exemplo:
romaji superior `bokura nanno tame ni` → preservar.
tradução inglesa inferior `I wonder for what purpose` → traduzir PT-BR.

Reinjetar mantendo estrutura visual original.

Reutilizar abstração de identidade semântica quando adequado (`SONG_SEMANTIC_ID` ou equivalente).

Fixtures:
- romaji + translation
- English translation simples
- original script + translation
- animation
- inline tags
- multiple \N
- duplicate song frames
- OP/ED
- negatives
- syllabic unsupported

Regressões: V2.2.5, V2.2.6, signs, dialogue, short-English, source-copy, break blocks, drawings, Library, ledger, web progress.

Canários:
1. eventos minimizados;
2. um episódio representativo.

GATE 4:
romaji/original preservado; SONG_TRANSLATION EN→PT-BR; estrutura intacta; karaoke visual preservado; syllabic unsupported fail-closed; zero regressão. Promover V2.3.0 e continuar.

## 10. KARAOKE AUGMENTATION S04
Não retraduzir diálogos/signs desnecessariamente.
Best validated candidate → KARAOKE AUGMENTATION apenas em SONG_TRANSLATION.
S04E01–E12 serial, fail-fast, arquivar, zero publicação automática.
GATE 5: `S04_KARAOKE_AUGMENTATION_COMPLETE`.


## 11. GLOSSÁRIO 1.0
Camada independente da Translation Memory.

Schema mínimo:
source expression, preferred PT-BR, alternatives, category, register, intensity, context/usage, avoid/notes, scope, status, version, timestamps, provenance.

Categorias:
SLANG, IDIOM, INSULT, TECHNICAL_TERM, CHARACTER_TERM, UNIVERSE_TERM, OTHER.

Escopos:
GLOBAL, ANIME, CHARACTER.
Precedência: CHARACTER > ANIME > GLOBAL.

Status:
APPROVED, DRAFT, DISABLED, SUPERSEDED.

## 12. UI DO GLOSSÁRIO
Área web mobile-friendly:
buscar, filtrar, adicionar, editar, desativar, histórico, importar, exportar.

## 13. SEED CURADO DE GÍRIAS
Criar ~100–200 expressões inglesas frequentes para anime, com curadoria forte.
Não usar source→replace fixo.
Cada entrada: preferencial, alternativas, registro, intensidade, contexto, notas.
Seed explicitamente autorizada; não é auto-learning.

## 14. INTEGRAÇÃO
Glossário orienta o modelo; NÃO replace cego pós-Qwen.
Selecionar somente entradas relevantes e registrar glossary_candidates, glossary_entries_used, scope, entry_ids.
Glossário vazio deve ser neutro.
TM permanece separada e só usa SEGMENT_APPROVED.

GATE 6:
schema/API/UI, seed, integração, negativos, neutralidade e TM intacta → PASS e continuar.


## 15. AUDITORIA FINAL DA S04
Auditar melhor candidate de cada episódio após signs + karaoke.
Não retraduzir S04 inteira só para usar Glossário.

Auditar:
dialogue residual, sign coverage, mixed groups, karaoke, romaji, structural invariants, warnings, lineage, publication readiness.

Reavaliar 41 candidatos de diálogo residual. Somente TRUE confirmado bloqueia.

GATE 7:
`S04_FINAL_VALIDATED`.

## 16. PUBLICAÇÃO FINAL S04
Preparar best candidate por episódio.
Publicar apenas se lineage e best candidate forem inequívocos e a autorização de publicação já existir.
Se houver escolha humana real entre versões:
`S04_PUBLICATION_SELECTION_REQUIRED`.
Não parar por conveniência.


## 17. SEGUNDO ANIME — GENERALIZAÇÃO
Buscar READ-ONLY anime diferente de Full Metal Panic:
- source ENG ASS textual
- não PGS-only
- release/fansub diferente
- Styles/signs diferentes
- karaoke se possível
- cour manejável

Se múltiplos, pontuar por diversidade estrutural, qualidade source, tamanho e diferença para FMP.
Selecionar automaticamente o melhor.
Limite: 1 cour (~12–13 episódios).
Se nenhum: `NEXT_ANIME_CORPUS_REQUIRED`.

## 18. PROFILE
Profiling read-only sem regras específicas de FMP.

## 19. CANÁRIO
Um episódio representativo.
Exigir dialogue/signs/karaoke/structure/residual/glossary/ledger/validators.

GATE 8:
PASS → continuar.
Nova classe → `NEW_PIPELINE_VERSION_REQUIRED`.

## 20. COUR COMPLETO
Serial + fail-fast, zero publicação automática.
GATE 9:
sem patches por episódio/release → `GENERALIZATION_CORPUS_PASS`.


## MANDATORY ARCHITECTURE, REPOSITORY, DOCUMENTATION AND PRODUCTION CONSOLIDATION (P2C)
Esta é uma etapa obrigatória do roadmap, inserida após a conclusão do GATE 9 e antes do release candidate interno, hardening, productization e GitHub/beta. P2C não é cleanup opcional nem tarefa pós-beta.

### ORDEM OBRIGATÓRIA DOS GATES
`P2B2A — LINEAGE CLOSURE + HISTORICAL OFFLINE REPLAY`
→ `P2B3 — PREDEPLOY IMAGE BUILD + ISOLATED CONTAINER SMOKE + ROLLBACK PROOF`
→ `P2C — REPOSITORY + DOCUMENTATION + FILESYSTEM + PRODUCTION LAYOUT CONSOLIDATION`
→ `CONTROLLED DEPLOY OF CONSOLIDATED ARCHITECTURE`
→ `RESUME ZOMBIELAND S01 E07–E12`
→ `FINAL V2.3.8 GENERALIZATION / PROMOTION DECISION`
→ `HARDENING + PRODUCTIZATION`
→ `GITHUB + DOCKER BETA`.

Não pular de P2B3 diretamente para hardening, GitHub ou beta. P2B2A é pré-requisito de P2B3; P2B3 é pré-requisito de P2C; P2C é pré-requisito do deploy consolidado; o deploy aprovado é pré-requisito da retomada de E07–E12; a conclusão de E07–E12 e a decisão final de V2.3.8 são pré-requisitos do hardening; hardening é pré-requisito de GitHub/Docker beta. O repositório Git pode nascer durante P2C, mas release público só ocorre depois dos gates de hardening/beta.

### MOTIVAÇÃO E PRINCÍPIO DE AUTORIDADE
A auditoria arquitetural identificou que o workspace de review mistura histórico, candidates, experimentos, reports e sources de runtime. Também identificou `NO_GIT_SOURCE_OF_TRUTH` e a existência histórica de source de review, `/docker/subtranslate` como source persistente e `/app` como source executável, exigindo reconciliação por SHA. O objetivo de P2C é eliminar essa ambiguidade sem perder evidência histórica.

Depois de P2C:
- o repositório Git canônico será a source of truth do código;
- a imagem Docker versionada será a source of truth do artefato executável implantado;
- o estado persistente será a source of truth dos dados operacionais e da Library;
- produção não será workspace de desenvolvimento.

### FASE 1 — FILE CLASSIFICATION BEFORE MOVEMENT
Antes de mover ou excluir arquivos, classificar cada arquivo relevante como:
`KEEP_RUNTIME`, `KEEP_TEST`, `KEEP_DOCUMENTATION`, `MOVE_TO_HISTORICAL_ARCHIVE`, `REMOVE_AFTER_EQUIVALENCE_PROVEN`, `PRODUCTION_STATE`, `SECRET_OR_RUNTIME_CONFIG` ou `UNKNOWN_REQUIRES_REVIEW`.

`UNKNOWN_REQUIRES_REVIEW` nunca permite delete automático. `REMOVE_AFTER_EQUIVALENCE_PROVEN` só pode ser removido após prova objetiva de que não participa do runtime, testes necessários, rollback ou evidência histórica requerida.

### FASE 2 — CANONICAL GIT REPOSITORY
Criar futuramente, em caminho validado durante P2C, um repositório canônico equivalente a `/home/palhacinho/codex-projects/subtranslate/`. A estrutura conceitual é:

```text
subtranslate/
├── src/subtranslate/{control,pipeline,stages,library,validation}/
├── tests/{unit,integration,regression,fixtures}/
├── docs/
├── deploy/
├── scripts/
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── .gitignore
└── .git/
```

Esta é target architecture, não autorização para mover arquivos nesta etapa do roadmap. A estrutura final deve ser derivada do código consolidado real.

### FASE 3 — CODE ORGANIZATION
O runtime final deve ser pequeno, compreensível e possuir responsabilidades claras: canonical pipeline registry, canonical orchestrator, canonical lineage authority, stages explícitos, validators explícitos, control plane separado da Library, configuration authority clara e fronteiras fail-closed. Módulos `pipeline_v2_x`, adapters, RCs e experimentos históricos não devem permanecer misturados ao runtime final apenas por valor histórico. Nenhum código histórico pode ser removido sem prova de equivalência e preservação em archive quando necessário.

### FASE 4 — DOCUMENTATION CONSOLIDATION
Criar documentação canônica que explique como o sistema funciona agora: `README.md`, `docs/ARCHITECTURE.md`, `docs/PIPELINES.md`, `docs/LIBRARY_AND_LINEAGE.md`, `docs/CONFIGURATION.md`, `docs/OPERATIONS.md`, `docs/TESTING.md`, `docs/RECOVERY_AND_ROLLBACK.md`, `docs/SECURITY.md`, `docs/ROADMAP.md` e `CHANGELOG.md`. Considerar `docs/adr/` para decisões arquiteturais materiais. `HANDOFF_CHATGPT.md` e `PROJECT_STATE.json` continuam preservando cronologia durante o desenvolvimento, mas não são a documentação principal do produto final.

### FASE 5 — HISTORICAL ARCHIVE
Separar desenvolvimento histórico, RCs, canários, experimentos, traces, auditorias, failed candidates, reports, snapshots e evidências em archive organizado, conceitualmente em `/home/palhacinho/codex-projects/subtranslate-history/`. Não apagar história relevante. O archive deve permitir investigar decisões antigas, provar regressões, consultar RCs e reproduzir bugs quando possível, sem poluir o repositório vivo.

### FASE 6 — PRODUCTION FILESYSTEM
Produção final deve deixar de ser source of truth de código. A estrutura conceitual é:

```text
/docker/subtranslate/
├── compose.yaml
├── .env
├── config/
├── state/{anime-subtitle-library,translation-memory,...}/
├── logs/
└── backups/
```

Código executável deve vir de imagem Docker versionada, por exemplo conceitual `subtranslate:0.1.0-beta` quando a política de versionamento for decidida. Produção não deve depender de editar Python diretamente em `/docker/subtranslate`.

### FASE 7 — CONFIGURATION AND SECRETS
Git pode conter exemplos de configuração, `.env.example`, defaults não sensíveis e documentação. Git não pode conter password, token, API key, private key, cookies, credentials ou segredos reais. Segredos reais permanecem fora do Git.

### FASE 8 — REPRODUCIBLE BUILD
P2C deve comprovar capacidade de clean checkout, instalação/build, testes offline, build de imagem Docker, identificação exata da versão e isolated smoke. O build não pode depender de arquivos ocultos do antigo review workspace.

### FASE 9 — TEST ORGANIZATION
Organizar testes em `unit`, `integration`, `regression` e `fixtures`, preservando regressões históricas úteis. Separar testes que precisam de modelo, testes offline, testes de integração, testes de renderer/playback e testes manuais. Nenhum teste pode chamar modelo silenciosamente.

### FASE 10 — VERSIONING
Git deve registrar commits, tags/releases quando apropriado e mudanças arquiteturais. A imagem Docker deve ser rastreável ao commit/version que a produziu. Produção deve permitir responder qual versão está rodando, qual commit a produziu, qual image digest e qual configuração efetiva (pipeline/model).

### FASE 11 — ROLLBACK
Antes da primeira implantação da estrutura consolidada, validar backup, preservar imagem anterior, preservar compose/config anterior, documentar rollback e verificar compatibilidade do state. Rollback não pode depender de reconstruir source antigo manualmente.

### FASE 12 — NO BIG-BANG MOVE
Não fazer `mv` de tudo, delete em massa ou rewrite completo. A execução deve ser: classificar, copiar, provar, testar, trocar autoridade e somente depois arquivar/remover.

### FASE 13 — ACCEPTANCE CRITERIA
P2C só passa quando:
1. existe uma canonical Git source of truth;
2. runtime source não depende do antigo review workspace;
3. clean checkout reproduz testes e build;
4. documentação canônica descreve a arquitetura atual;
5. pipeline, stage e lineage estão documentados;
6. produção usa imagem versionada;
7. persisted state está separado do código executável;
8. secrets estão fora do Git;
9. historical artifacts estão preservados em archive organizado;
10. nenhum runtime dependency foi removido sem prova;
11. regression suites continuam PASS;
12. rollback foi comprovado;
13. produção identifica version, commit, image digest, effective pipeline e effective model;
14. nenhuma mudança de semântica de tradução foi introduzida pela reorganização;
15. Library, TM, Glossary e historical records permanecem íntegros.

### FASE 14 — NON-GOALS
P2C não é momento para trocar modelo, otimizar prompts, mudar qualidade linguística, implementar OCR/PGS, adicionar novas features, mudar classificação karaoke, mudar ASS semantics, mudar retry policy ou reescrever a Library. O objetivo é organização, reprodutibilidade, manutenibilidade, autoridade e segurança operacional.


## 21. RELEASE CANDIDATE INTERNO
Congelar pipeline estável: version, hashes, tests, features, limitations, rollback.

Criar fixtures SINTÉTICAS públicas para:
dialogue, short-English, source-copy, drawings, sign groups, multiline signs, visual break blocks, delimiters, karaoke, glossary, animated signs.

Nunca usar legendas protegidas reais.
GATE 10: release candidate interno completo.

## 22. HARDENING / PRODUTIZAÇÃO
Sanitizar:
IPs específicos, domínios pessoais, usernames, tokens, secrets, .env real, homelab paths, Library state, SQLite real, subtitles reais, TM real, human corrections reais, backups, logs privados.

## 23. CONFIGURAÇÃO
Parametrizar:
OLLAMA_URL, MODEL_PROVIDER, TRANSLATION_MODEL, REVIEW_MODEL, STATE_PATH, MEDIA_ROOTS, PORT, LOG_LEVEL etc.
Não hardcodar qwen3.5:9b.

## 24. PREPARAR MODEL BENCHMARK
Arquitetura deve aceitar Qwen, Aya/aya-expanse e outros modelos Ollama sem alterar pipeline.

## 25. DOCKER/COMPOSE
Distribuição principal.
Imagem NÃO inclui modelo.
Ollama externo/configurável.
Criar Dockerfile, compose, .env.example, volumes, healthcheck, permissions, state persistence.

## 26. CLEAN INSTALL
repo limpo → .env → docker compose up → healthy.
Sem dependência do Homelab.
GATE 11 obrigatório.


## 27. DOCUMENTAÇÃO / CI / GITHUB READY
Docs:
README, Quick Start, Architecture, Configuration, Ollama Setup, Models, Pipeline, Library, Glossary, TM, Human Review, Jellyfin, Backup/Restore, Troubleshooting, Security, Development, Testing, Limitations.

CI:
GitHub Actions para tests, lint quando aplicável, synthetic fixtures, Docker build, checks.

GHCR:
preparar workflow; não publicar imagem pública sem autorização final.

Versionamento:
separar APP VERSION e PIPELINE VERSION.
Preparar `Subtranslate v0.1.0-beta`.

## 28. LICENÇA
Se já definida, aplicar.
Se não:
não escolher automaticamente.
Parar só publicação em `LICENSE_DECISION_REQUIRED`, mas concluir repo local, sanitização, Docker, docs, CI, tests, release notes, clean install e estado `GITHUB_READY_FOR_PUBLISH`.

## 29. GIT LOCAL
Criar/organizar repo local, commits limpos, sem dados privados.

GATE 12 — GITHUB READY:
zero secrets/private state/real subtitles; synthetic fixtures only; clean install PASS; CI/docs/Docker/env/release notes prontos.

## 30. PUBLICAÇÃO GITHUB
Só publicar/push público se:
1. licença definida;
2. remote/repo inequivocamente autorizado;
3. zero exposição privada.
Caso contrário: `GITHUB_PUBLISH_APPROVAL_REQUIRED`.


## 31. APÓS GITHUB — MODEL BENCHMARK LAB
Objetivo: comparar modelos no MESMO Subtranslate, mantendo pipeline estável.

Baseline:
qwen3.5:9b

Challengers:
Aya / aya-expanse e outros modelos Ollama.

Regra:
mesmo source, pipeline, prompt, validators, Glossário, TM, hardware e parâmetros; muda SOMENTE o modelo.

Métricas:
QUALIDADE — fidelidade, naturalidade PT-BR, gírias, contexto, nomes, signs, karaoke, residual English, avaliação humana.
ROBUSTEZ — JSON/protocolo, parser, retries, timeouts, source-copy, fail-closed, estabilidade.
PERFORMANCE — tempo/chamada, tempo/episódio, warm/cold start, calls, retries.
RECURSOS — VRAM, RAM, GPU, CPU, estabilidade longa.

Futura UI:
Models / Production / Available / Benchmark.
Outputs benchmark nunca publicam automaticamente.

Não assumir vencedor único; futuramente pode haver multi-modelo para tradução/review/retries, somente após benchmark controlado.


## 32. FORA DO CAMINHO CRÍTICO
PGS/OCR: macrofase posterior.
Karaoke syllabic retiming PT-BR: posterior.
Contas públicas/multiuser/exposição internet: fora deste roadmap salvo autorização futura.

## 33. POLÍTICAS PERMANENTES
- V2.1.3 core permanece congelada/hash-preservada.
- Versão promovida não é editada in-place.
- Candidate não promovida pode ter RCs preservando falhas como histórico.
- Ambiguidade → fail-closed.
- Nunca publicar candidate só porque VALIDATED.
- Publicação atômica + rollback.
- TM somente SEGMENT_APPROVED.
- Glossário não é TM; sem autoaprovação; sem replace cego.
- Dados reais nunca entram em fixtures públicas.
- Não reiniciar Jellyfin/Ollama/Bazarr/NPM sem necessidade comprovada.
- Progresso web real preservado.
- Failure ledger deve tornar blocking failure diagnosticável sem rerun quando possível.
- Atualizar sempre `HANDOFF_CHATGPT.md` e `PROJECT_STATE.json`.

## 34. CRITÉRIO FINAL DO ROADMAP
Concluído quando existir:
1. V2.2.6 signs aplicada;
2. Karaoke V2.3.x funcional;
3. romaji/original preservado;
4. Glossário curado integrado;
5. S04 final validada;
6. segundo anime/cour PASS sem patches específicos;
7. RC interno congelado;
8. fixtures sintéticas públicas;
9. sanitização;
10. configuração env;
11. Docker/Compose limpo;
12. clean install PASS;
13. docs/CI prontas;
14. GitHub-ready tree;
15. licença definida ou gate explícito;
16. Subtranslate v0.1.0-beta pronto;
17. arquitetura preparada para Benchmark Lab;
18. zero dados privados expostos;
19. rollback/evidências preservados.

## 35. ESTADOS DE PARADA AUTORIZADOS
- NEW_PIPELINE_VERSION_REQUIRED
- HUMAN_PLAYBACK_REVIEW_REQUIRED (somente indispensável)
- HUMAN_LINGUISTIC_DECISION_REQUIRED
- S04_PUBLICATION_SELECTION_REQUIRED
- NEXT_ANIME_CORPUS_REQUIRED
- LICENSE_DECISION_REQUIRED
- GITHUB_PUBLISH_APPROVAL_REQUIRED
- OBJECT_INTEGRITY_FAILURE
- SOURCE_AMBIGUOUS
- SERVICE_UNHEALTHY
- REGRESSION_BLOCKING
- outro FAIL comprovado

Não parar apenas para pedir permissão para uma fase já autorizada aqui.

## 36. REGRA FINAL
PASS OBJETIVO → CONTINUAR.
FAIL REAL → PARAR.
SIGNS → CONSISTÊNCIA DE GRUPO.
KARAOKE → TRADUZIR SOMENTE SONG_TRANSLATION.
ROMAJI → PRESERVAR.
KARAOKE SILÁBICO INSEGURO → PRESERVAR/FAIL-CLOSED.
GLOSSÁRIO → ORIENTAÇÃO CONTEXTUAL.
TM → SOMENTE SEGMENT_APPROVED.
NOVO ANIME → GENERALIZAÇÃO, NÃO PATCH POR RELEASE.
GITHUB → SOMENTE SANITIZADO.
DOCKER → DISTRIBUIÇÃO PRINCIPAL.
OLLAMA → EXTERNO/CONFIGURÁVEL.
MODELO → NÃO HARDCODAR.
QWEN → BASELINE ATUAL.
AYA E OUTROS → MODEL BENCHMARK LAB APÓS GITHUB.
DADOS PRIVADOS → NUNCA PUBLICAR.
EVIDÊNCIA → PRESERVAR.
ROLLBACK → SEMPRE DISPONÍVEL.
