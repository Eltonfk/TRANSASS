# Reconciliação operacional — Shiki E10 candidate-only

**Data:** 2026-09-27 (America/Recife)  
**Runtime:** Docker local `transass` r7, pipeline V3, Ollama `qwen2.5:14b`  
**Estado operacional:** `CANONICAL_STATE_RECONCILIATION_REQUIRED`

## Escopo e estado anterior

O usuário autorizou retraduzir as legendas de Shiki a partir de S01E10,
corrigindo defeitos reproduzíveis e seguindo até a conclusão da temporada. O
fluxo adotado foi candidato: não arquivar na Library nem publicar/substituir
sidecars, como na tentativa candidata do E09. O preflight read-only identificou
S01E10–E21 elegíveis (IDs 297–308), todos com fonte textual francesa na track
ASS interna 2 (`FR Full ASS`); não há E22 catalogado.

## Incidente observado

- Foi enfileirado somente Shiki S01E10 (episódio ID 297), job
  `307ca6fb42c7466e93451020626e34d3`, com `candidate_only=true`,
  `ollama_only=true`, fonte `français` e modelo `qwen2.5:14b`.
- A implementação executou `resolve_episode_source(..., materialize=True)` e
  importou o sidecar PT-BR legado antes de iniciar o worker. Esse caminho criou
  registros Library apesar do modo candidato:
  - registro 119: fonte `français`, `EXTRACTED`, track 2, hash
    `b3ea1a408d5d05e9ec87802933f8a1192f47ea135bcd28eeeeba9110269d08e1`;
  - registro 120: PT-BR, `IMPORTED_EXISTING`, hash
    `38c420381883794b063a82c2b176a1232511d47c542f14c6d5a4b29c9b131d1d`.
- A consulta de Library confirmou ambos como `published=false`; o sidecar
  anterior não foi substituído. Não foi criada candidata final.
- O job foi parado pelo endpoint de parada e está terminal `CANCELLED` /
  `STOPPED`, com erro de encerramento cooperativo (`código -15`). O `/status`
  confirmou `running=false`. Foram observadas quatro chamadas/capturas no
  momento da interrupção (três concluídas e uma em andamento); as capturas
  existentes em `/app/state` devem ser preservadas, não reutilizadas nem
  removidas automaticamente.
- Nenhum outro episódio foi enfileirado. Não houve promoção ou publicação.
  Os registros 119/120, sidecar, estado do job e capturas são evidência
  histórica e não devem ser apagados ou reescritos.

## Causa-raiz e bloqueio

`candidate_only` era verificado somente depois de a tradução e a auditoria
terminarem. Na preparação da fila, a extração/importação escrevia na Library;
além disso, a validação inicial do worker exigia IDs de registros. Portanto, a
opção impedia o arquivamento da candidata final, mas não era realmente
“sem escrita na Library”. A documentação anterior do E08 descrevia essa
semântica de forma ambígua e não constitui prova de ausência de mutação.

Em conformidade com a política de runtime evidence, ficam bloqueados novos
batches/chamadas de modelo e novas escritas na Library, state real ou produção
até que o caminho candidate-only seja corrigido/testado offline, esta evidência
seja reconciliada na documentação ativa e o runtime local corrigido seja
verificado somente por leitura. O cancelamento já ocorrido não autoriza nova
tentativa retroativamente.

## Correção requerida

Implementar a preparação de fonte candidata em staging isolado de `/app/state`,
sem `ingest_file`, sem importação de sidecar e sem dependência de IDs Library;
validar o arquivo staged por caminho confiável e hash. Adicionar testes offline
que falhem se candidate-only tentar qualquer escrita/ingestão na Library e que
confirmem a limpeza do staging em conclusão/falha. Manter intactos os modos
arquivados existentes.

Após a correção, reconstruir somente o Docker local autorizado, confirmar por
leitura a versão/configuração, mounts, estado ocioso e contagem/IDs Library
inalterados. Só então retomar, um episódio por vez, a partir do E10 e manter
candidatas fora da Library/publicação. Registrar cada resultado sem apagar
capturas históricas.

## Correção no worktree e evidência offline

- A extração de track e a cópia de sidecar agora têm modo de staging isolado
  sob `STATE_DIR/candidate-source-staging/<job-id>/`; nesse modo não chamam
  `ingest_file` e não escrevem objetos/registros na Library. O sidecar PT-BR
  legado é apenas revalidado, nunca importado para candidate-only.
- O worker candidato não exige IDs de origem/alvo Library. Ele confirma o
  episódio ANIME, limita o caminho ao staging confiável e valida tamanho e
  SHA-256 antes de iniciar o runner. A finalização candidate-only segue antes
  do código de ingestão/publicação. O staging de fonte é removido ao terminar,
  falhar ou cancelar; a limpeza é confinada a essa raiz e não toca as capturas
  duráveis nem o artefato candidato.
- Foram adicionados testes offline para cópia de sidecar e extração de track
  sem ingestão, enfileiramento sem importar o sidecar, rejeição de fonte
  adulterada, limite seguro de limpeza e conclusão do worker sem arquivo ou
  registro Library.
- Testes focais: `50 passed`. Suíte completa: `1088 passed, 38 deselected,
  70 subtests passed`; há uma falha não relacionada a esta alteração em
  `test_visual_glyph_primary_tokens_cleanup_and_fallback`: resultado
  `VISUAL_GLYPH` versus expectativa `VISUAL_GLYPH_BASE_FALLBACK`. O teste e o
  módulo V2.3.8 correspondente não foram alterados no worktree desta correção.
- A documentação ativa agora descreve a semântica isolada de candidate-only.
  O código ainda não foi instalado no Docker durante esta etapa. Nenhuma nova
  chamada de modelo ou job foi iniciado; o estado operacional continua
  bloqueado até a verificação read-only do runtime atualizado.

## Correção aditiva da contagem de capturas e verificação do runtime

- A leitura posterior do volume persistente, agrupada pelo token do job
  `d02f3f8e5673b4d5f17d0bdc`, encontrou 13 `capture_state.json`: 12 em
  `RESPONSE_DURABLE` e uma em `TRANSPORT_IN_PROGRESS`. A observação inicial de
  quatro chamadas descrevia apenas o instante anterior à parada, não o estado
  final das capturas. A captura em andamento não prova que houve resposta nem
  deve ser repetida automaticamente.
- A imagem corrigida foi construída como
  `transass:v2.5.2-local-v3-candidate-only-20260927-r8`, digest
  `sha256:07c646f6f62e9bb6a567e4d4c7a7eb1ef4a2c54479286ffd1a38b23ac3ae5a1f`,
  revisão `5b541ca-dirty-candidate-only-20260927`. A tag local consumida pelo
  Compose foi apontada para essa imagem, e somente o serviço `transass` foi
  recriado com `--no-deps --force-recreate`; Ollama não foi reiniciado.
- `/health` responde `ok`; `/version` e `/pipeline-config` informam `v3` e a
  identidade r8. Os mounts permanecem `/PICAdeiro/data/Shows:/shows` e
  `/docker/transass/state:/app/state`. A fila está ociosa e `ollama ps` não
  mostra inferência/modelo ativo.
- Consulta read-only após o restart confirmou que E10 ainda possui somente os
  registros históricos 119 e 120, com os mesmos hashes registrados acima; não
  existe nova candidata, registro translated ou publicação. O job cancelado
  continua em `/app/state/jobs.json`; a interface em memória reiniciada não o
  usa como job da sessão atual. As 13 capturas permanecem no state.
- O Compose resolvido publica `5050` em `0.0.0.0`, embora o padrão documentado
  seja loopback. Nenhuma alteração de binding foi feita porque isso poderia
  interromper acesso remoto intencional; a configuração atual deve ser
  confirmada pelo operador como decisão separada de exposição de rede.
- Nenhuma tradução/chamada de modelo foi iniciada após a correção. A próxima
  etapa é repetir o preflight read-only para E10–E21 no runtime r8; só com os
  12 episódios elegíveis e fila ociosa será retomada a execução candidata,
  episódio por episódio, sem Library/publicação.

## Preflight read-only no runtime r8

- O endpoint `/retranslate/preflight` aprovou E10–E21 (IDs 297–308):
  `eligible=12`, `blocked=0`, `skipped_current_validated=0`, pipeline `v3`,
  modelo `qwen2.5:14b`, idioma fonte `francês`. Todos usam a track ASS textual
  interna 2, `FR Full ASS`; o preflight não criou job nem chamou modelo.
- A reconciliação documental, testes focais e verificação de runtime estão
  concluídos. A continuação operacional autorizada permanece candidata,
  sequencial, Ollama sem fallback e sem Library/publicação; a fila estava
  ociosa ao fim do preflight.

## Tentativa candidata E10 no r8 e correção V3

- Foi iniciada uma nova tentativa isolada apenas para E10, job
  `35a7155298314a0a89564a847fe2b5e8`, mantendo `candidate_only=true`,
  `ollama_only=true`, fonte francesa e `qwen2.5:14b`. O job terminou em falha
  com `V3_TRANSLATION_SOURCE_COPY:130`; não gerou candidata final e não
  enfileirou E11.
- As 17 chamadas dessa tentativa têm `RESPONSE_DURABLE`; foram copiadas para
  análise offline e mantidas no state original. A fonte usada continua com
  SHA-256 `b3ea1a408d5d05e9ec87802933f8a1192f47ea135bcd28eeeeba9110269d08e1`.
- Na unidade 130, a resposta repetiu literalmente `Shizuka Matsuo\nde
  Sakaimatsu...`. As palavras são nomes próprios reconhecidos e `de` é uma
  partícula francesa presente no nome; o detector antigo confundiu a
  preservação do nome com uma fala não traduzida. Isso é um falso positivo
  específico, não motivo para enfraquecer a validação geral de cópia/resíduo.
- O pipeline V3 agora recebe os nomes próprios reconhecidos daquela unidade
  no detector. Só aceita, para fonte francesa, a forma integralmente
  título-casada composta por ao menos dois desses nomes, uma partícula nominal
  (`de/del/da/do/dos/van/von`) e conectores; texto corrente continua sujeito
  ao bloqueio. Foi adicionado teste de integração para esse caso e mantido o
  teste que rejeita diálogo francês copiado.
- Replay determinístico, sem rede/modelo e sem escrever saída candidata,
  validou as 17 respostas duráveis (136 unidades) e atravessou o ponto que
  falhava. Parou deliberadamente na unidade 136 porque não há captura para as
  chamadas seguintes. Resultado: prefixo capturado aprovado; não equivale a
  tradução completa nem a candidata validada.
- Testes focais da regressão: `2 passed`. Suíte offline após a correção:
  `1089 passed, 38 deselected, 70 subtests passed`, com uma falha conhecida e
  não relacionada: `test_visual_glyph_primary_tokens_cleanup_and_fallback`
  continua esperando `VISUAL_GLYPH_BASE_FALLBACK` e recebe `VISUAL_GLYPH`;
  nem o teste nem `v238_full_translation_stage.py` foram alterados nesta
  correção.
- **Estado de reconciliação atual:** o código atualizado e a correção do
  detector ainda precisam ser reconstruídos e confirmados no Docker. Portanto,
  não iniciar nova chamada de modelo, retry de E10 ou E11+ até verificar a
  identidade V3 da nova imagem, saúde, mounts preservados, fila ociosa,
  ausência de inferência ativa e integridade das evidências/Library. Depois
  disso, retry autorizado somente da E10, ainda sem Library/publicação.

## Reconciliação read-only da falha r8

- O contêiner ativo responde na porta interna `5000` (publicada no host como
  `5050`; o primeiro probe errou ao usar `5050` dentro do contêiner). `/health`
  está `ok`, versão `2.5.2`, pipeline `v3`, identidade r8. O status confirma
  `running=false`, E10 `FAILED`, sem job corrente; `ollama ps` está vazio.
- Permanecem os mounts `/PICAdeiro/data/Shows:/shows` e
  `/docker/transass/state:/app/state`. A consulta read-only da Library para
  episódio 297 mostra apenas os registros históricos 119/120 com os hashes
  anotados no incidente. O state contém as 17 capturas desta tentativa, todas
  `RESPONSE_DURABLE`, com zero divergências SHA-256. Nenhum arquivo candidato
  foi produzido.
- Essa evidência reconcilia a falha e confirma que é seguro substituir somente
  o container da aplicação; não altera o bloqueio para chamadas de modelo até
  a reconstrução r9 e nova checagem read-only.

## Reconciliação read-only do runtime r9

- A correção foi construída como
  `transass:v2.5.2-local-v3-candidate-only-20260927-r9`, imagem
  `sha256:ca41fb53472d1484cadc233b60a261a693183334a4e603cccbbde636eef94f3c`,
  identidade `5b541ca-dirty-candidate-only-v3-namefix-20260927`. A tag local já
  consumida pelo Compose foi apontada para essa imagem. Somente `transass` foi
  recriado com `--no-deps --force-recreate`; Ollama e volumes não foram
  recriados.
- `/health` respondeu `ok`; `/version` informa 2.5.2, pipeline `v3` e identidade
  r9; `/pipeline-config` confirma V3 e `qwen2.5:14b`. `/status` confirmou fila
  vazia, sem job corrente e sem execução. `ollama ps` continua vazio.
- Os dois mounts foram confirmados iguais: `/PICAdeiro/data/Shows:/shows` e
  `/docker/transass/state:/app/state`. Após o recreate, a Library da E10
  continua com os mesmos registros 119/120 e hashes; as 17 respostas duráveis
  anteriores continuam no state, sem divergência SHA-256. Nenhuma candidata
  foi criada pelo rebuild.
- Preflight read-only somente da E10 aprovou `eligible=1`, `blocked=0`,
  `skipped_current_validated=0`, fonte francesa, track textual ASS interna 2,
  V3 e `qwen2.5:14b`. O resultado não criou job nem chamou o modelo. A
  continuação autorizada neste ponto é uma nova tentativa apenas de E10 em
  `candidate_only`, Ollama exclusivo/sem fallback e sem escrita/publicação na
  Library; E11 aguarda auditoria de sucesso da E10.

## Tentativa candidata E10 no r9: quebra válida com “há”

- A tentativa isolada no r9, job `b3500198e8fc43b883a6520a2fe688f5`, terminou
  em `FAILED` durante a validação estrutural: `evento 260:
  LINE_BREAK_INSIDE_WORD`. O job estava marcado `candidate_only=true`,
  `ollama_only=true`, `published=false`; não há URL de candidata nem saída
  final. Nenhum registro da Library mudou e E11 não foi enfileirado.
- As 38 respostas do run `6699c0cbdc8a166843938927` estão duráveis e foram
  validadas offline quanto a estado e SHA-256. Replay local, sem nova chamada
  ao modelo, reproduziu as 296 unidades e localizou o par exato:
  - fonte: `dans lequel tu vis\Ndepuis un an ?`;
  - tradução: `em que você vive\Nhá um ano?`.
- A tradução não divide palavra. O falso positivo vinha da regra de quebra,
  que exige que tokens curtos adjacentes à quebra pertençam ao léxico de formas
  legítimas; `há` (verbo/advérbio completo e comum em pt-BR) faltava na lista
  canônica `ass_engine.LEGITIMATE_SHORT_WORDS`.
- A lista foi corrigida com `há` e a regressão entrou no teste de
  `line_break_inside_word`; continuam válidos os testes que rejeitam cortes
  reais, como `vi\Nda`. O teste focal de `ass_engine` passou: `8 passed`.
- Replay offline completo após a correção terminou `COMPLETED`: 296 eventos na
  origem e 296 na saída, validador estrutural válido, zero divergências de
  contagem de quebras e nenhum issue. O evento 260 ficou exatamente como
  `em que você vive\Nhá um ano?`. Nove eventos mantêm texto visível idêntico,
  compatível com conteúdo que deve ser preservado. O artefato de replay foi
  gravado apenas em `/tmp`, não na Library nem no Docker state como candidata.
- Suíte offline completa após a correção lexical: `1089 passed, 38 deselected,
  70 subtests passed`; a única falha continua sendo o teste visual V2.3.8 já
  identificado (`VISUAL_GLYPH` versus expectativa
  `VISUAL_GLYPH_BASE_FALLBACK`), fora do parser/validador ASS alterado.
- **Estado atual:** a correção `há` existe apenas no worktree; o Docker ainda
  executa r9 sem essa mudança. Não iniciar nova chamada/modelo nem E11 até
  executar os testes offline, construir uma nova imagem r10, recriar apenas
  `transass`, confirmar health/V3/mounts/fila/Library/Ollama e repetir o
  preflight read-only. Após isso, retry autorizado somente da E10.

## Reconciliação read-only do runtime r10

- A imagem r10 foi construída com digest
  `sha256:e2a7b0bc81de6eea8ff6172b0a4613d8edfbdc46e023cd794fbd526be437f476`,
  identidade `5b541ca-dirty-candidate-only-v3-hafix-20260927`. O Compose foi
  atendido pela tag local já existente; somente o serviço `transass` foi
  recriado, com `--no-deps --force-recreate`.
- Health `ok`, V3 e modelo `qwen2.5:14b` confirmados; fila vazia, sem job
  corrente, Ollama sem inferência. Os dois bind mounts permanecem idênticos.
  E10 mantém apenas registros históricos 119/120 e seus hashes; as 38 capturas
  duráveis do r9 seguem no volume com zero divergências SHA-256.
- Preflight read-only da E10 no r10 aprovou `eligible=1`, `blocked=0`, fonte
  francesa e faixa textual `FR Full ASS` (track 2). Está reconciliado o
  bloqueio: é permitido iniciar somente uma nova candidata E10, `candidate_only`
  e `ollama_only`; E11 fica condicionada à auditoria completa de E10.

## E10 concluída como candidata no r10

- Job `65e6799fdc7048a8b64fd6b387da38c5`: `COMPLETED`, motivo
  `candidate_only_no_library_no_publication`, pipeline V3, Ollama exclusivo
  `qwen2.5:14b`, 38 respostas duráveis. A candidata está disponível no endpoint
  de download do próprio job; SHA-256
  `d622e34431d6b27f9033518ab090e6257ff77938b718a630efbf607f9cc2402b`, 26.290
  bytes.
- A comparação independente com a fonte (SHA-256
  `b3ea1a408d5d05e9ec87802933f8a1192f47ea135bcd28eeeeba9110269d08e1`) encontrou
  296 eventos em ambos, estrutura válida, zero issues, zero diferenças na
  contagem de quebras, zero cópias integrais de diálogo e zero resíduos
  franceses de alta confiança. As regras lexicais de qualidade também não
  levantaram flags.
- Nove eventos mantêm o texto visível: cartões/títulos (`SHI KI`, `YAMAIRI`) e
  chamadas compostas por nomes próprios (`Natsuno`, `Megumi`, `Shizuka Matsuo
  de Sakaimatsu`, `Tooru`) com reticências. Não são frases francesas de diálogo;
  a preservação de nomes é intencional.
- Confirmação read-only após concluir: a Library da E10 continua exatamente
  com os registros antigos 119/120 e hashes já anotados; não houve registro de
  candidata nem publicação. O app está ocioso. Ainda não enfileirei E11: a
  revisão independente do patch do validador está pendente, e cada episódio
  seguirá apenas depois de auditoria individual.

## Restrição adicional do detector de cópia após revisão independente

- A primeira auditoria independente encontrou um blocker no bypass de nomes:
  a condição por capitalização permitia um diálogo copiado em caixa alta com
  dois nomes protegidos. A evidência e a candidata E10 não foram apagadas; E11
  permaneceu bloqueada.
- O bypass foi restringido a frases nominais: pelo menos dois tokens de nomes
  protegidos, partícula de nome, sem caixa alta integral nem pontuação de
  oração; todos os tokens precisam ser nomes protegidos, exceto no máximo um
  sobrenome título-casado imediatamente após a partícula. Texto adicional como
  `PARTONS!` ou `Partons!` volta a ser rejeitado como cópia.
- Regressões adicionadas: diálogo copiado em caixa alta e diálogo copiado
  capitalizado com os mesmos nomes devem falhar; a introdução nominal da E10
  continua permitida. Para `há`, há agora tanto o caso válido
  `vive\Nhá` quanto o corte inválido `viv\Nhá`.
- Replay offline das 38 capturas E10 no código mais restrito terminou
  `COMPLETED`, com 296 unidades; o arquivo resultante tem exatamente o mesmo
  SHA-256 da candidata que o app já guardou (`d622e344…c2402b`). Portanto, a
  candidata E10 foi revalidada sob o novo detector sem nova inferência e sem
  alteração do candidato persistido.
- Suíte offline completa após esse ajuste: `1090 passed, 38 deselected, 70
  subtests passed`; permanece a falha conhecida, fora do escopo, do fallback
  visual V2.3.8 (`VISUAL_GLYPH` vs. `VISUAL_GLYPH_BASE_FALLBACK`).
- **Bloqueio vigente:** aguardar a segunda revisão independente do diff. Não
  iniciar E11 nem novas chamadas até o revisor confirmar que o bypass agora é
  realmente nominal e que o teste negativo cobre o achado anterior. Depois da
  revisão, reconstruir a imagem corrigida, verificar o runtime, e só então
  retomar a sequência candidata.

## Estado r11 e preflight E11 (sem chamada)

- A correção estreita foi construída como imagem
  `transass:v2.5.2-local-v3-candidate-only-20260927-r11`, digest
  `sha256:21339fd6f60e6e587738b2e6de3986a15660f3045474501d25582ccd144e8b4a`,
  identidade `5b541ca-dirty-candidate-only-v3-nameguard-20260927`. Somente o
  container `transass` foi recriado; health `ok`, V3, fila vazia, mounts
  preservados e Ollama ocioso.
- Após o restart, o download read-only da candidata E10 continua com SHA-256
  `d622e34431d6b27f9033518ab090e6257ff77938b718a630efbf607f9cc2402b`. A
  Library segue apenas com IDs 119/120 e hashes anteriores; as capturas
  históricas continuam presentes (13 da tentativa interrompida, 17 da falha
  inicial r8, 38 da falha estrutural r9, 38 do candidato E10).
- Preflight read-only de Shiki E11 (ID 298) aprovou `eligible=1`, `blocked=0`,
  track `FR Full ASS` interna 2, fonte francesa, V3 e `qwen2.5:14b`. **Nenhum
  job E11 foi enfileirado e nenhuma inferência ocorreu.** Aguarda-se o parecer
  de follow-up independente sobre a exceção nominal antes de retomar chamadas.

## Follow-up independente concluído

- O auditor confirmou que o blocker anterior foi resolvido nos dois casos
  reproduzidos e não encontrou outro blocker. A fala em caixa alta com nomes e
  a fala capitalizada com `Partons!` são novamente classificadas como cópia;
  a sequência nominal real da E10 segue aceita. Confirmou também os testes
  positivo e negativo de `há`.
- O auditor não editou arquivos. Com r11 implantada, replay offline E10
  idêntico ao candidato, suíte e testes focais registrados acima, e preflight
  E11 elegível, está liberado prosseguir com **apenas Shiki E11** como
  candidata via Ollama, sem fallback, Library ou publicação. Continuar uma por
  vez e interromper para auditoria/correção a cada falha.

## Incidente E11 r11: “Dieu” confundido com nome próprio

- Job E11 `502cb89d811a4eabae833c8ac29e1ce5` falhou em
  `V3_TRANSLATION_QUALITY_RISK:78:PROTECTED_NAME_NOT_PRESERVED:Dieu:REPAIR_FAILED`.
  As 12 capturas estão `RESPONSE_DURABLE`; nenhuma candidata foi criada, não
  houve publicação, E11 não recebeu registros Library e E12 não foi
  enfileirada.
- Os próprios payloads dão o contexto: `l'abandonnée de Dieu ?` foi traduzido
  como `a abandonada por Deus?`, e `Mon Dieu, quelle tête tu as !` como
  `Meu Deus, que cara você está!`. O detector de capitalização incluiu `Dieu`
  entre nomes protegidos e acionou correções indevidas; a resposta corretiva
  passou a preservar literalmente `Dieu`, falhando no gate.
- `Dieu` foi adicionado às palavras francesas que não devem ser inferidas como
  nomes próprios. É substantivo religioso comum no idioma de origem e a forma
  localizada correta no contexto é “Deus”; a mudança não relaxa o gate de
  cópia ou de resíduo. Teste novo cobre as duas construções observadas. O teste
  focal `test_translation_quality.py` passou (`10 passed`).
- Replay offline da parte coberta pelas capturas: 10 respostas de lote, 80
  unidades aprovadas, os dois reparos obsoletos não foram aplicados, e a
  reprodução parou em ID 80 por ausência intencional de captura desse lote.
  Não gravou arquivo de saída nem chamou modelo. A track ASS foi extraída
  novamente da mídia; seu hash interno original confere com o do job, e os
  textos das 80 unidades capturadas batem com o AST extraído.
- **Estado atual:** a correção `dieu` está só no worktree; o Docker continua
  r11. Não iniciar novo modelo/E11 nem E12 até concluir a suíte offline,
  revisão independente do ajuste, reconstruir r12, verificar runtime/mounts/
  Library e repetir preflight read-only. Depois, retomar apenas E11 como
  candidata Ollama-only.

## Verificação offline da correção “Dieu”

- Em 2026-09-27, a suíte focal `tests/offline/test_translation_quality.py`
  passou: `10 passed`; `git diff --check` também passou.
- A suíte offline ampla terminou em `1091 passed, 38 deselected, 70 subtests
  passed` e uma falha conhecida no teste legado
  `test_visual_glyph_primary_tokens_cleanup_and_fallback`: esperava
  `VISUAL_GLYPH_BASE_FALLBACK`, recebeu `VISUAL_GLYPH`. Não toca no módulo de
  qualidade nem no novo caso de “Dieu”; permanece registrada, sem ser ocultada
  ou convertida em aprovação.
- A revisão independente do ajuste `dieu` foi solicitada e segue pendente.
  Até o parecer, nenhuma imagem foi reconstruída e nenhuma inferência nova foi
  iniciada; E11 continua o único próximo episódio autorizado após reconciliação.
- Follow-up do auditor concluído: sem blocker concreto para o uso comum/religioso
  observado; o risco residual é um personagem/sobrenome literalmente chamado
  `Dieu` não ser protegido por esta heurística. Foram confirmados 10 testes
  focais, reconhecimento de `Nao` preservado e escopo da detecção restrito a
  francês. O auditor não fez tradução real nem acessou o Docker. Prosseguir
  somente com o rebuild local e a validação/preflight E11 já autorizados.

## Reconciliação do runtime r12 e liberação da retry E11

- A imagem local r12 foi construída como
  `transass:v2.5.2-local-v3-candidate-only-20260927-r12`, imagem
  `sha256:ba264eba7640eab2e5c7de7f4ad4bb4acfe6e64cbe7790fca4b3a84a718a0c9b`,
  identidade `5b541ca-dirty-candidate-only-v3-dieu-stopword-20260927`. A tag
  consumida pelo Compose foi atualizada e somente `transass` foi recriado com
  `--no-deps --force-recreate --pull never`.
- `/health` respondeu `ok`; `/version` confirma 2.5.2/V3 e identidade r12;
  `/pipeline-config` confirma V3 e modelo `qwen2.5:14b`. O serviço está healthy,
  `/status` ocioso e `ollama ps` vazio. Os mounts RW seguem
  `/PICAdeiro/data/Shows:/shows` e `/docker/transass/state:/app/state`.
- A candidata E10 do job `65e6799fdc7048a8b64fd6b387da38c5` segue baixável com
  SHA-256 `d622e34431d6b27f9033518ab090e6257ff77938b718a630efbf607f9cc2402b`.
  Os registros antigos E10 119/120 e hashes permanecem; E11 (episódio ID 298)
  ainda tem `records=[]`. Nenhuma escrita Library ou publicação ocorreu no
  rebuild.
- Preflight read-only da E11 aprovou 1/1, `blocked=0`, `skipped_current_validated=0`,
  fonte francesa track `FR Full ASS` (ASS textual interno 2), pipeline V3. Ollama
  está sem inferências pendentes. Está liberada somente a retry candidata da
  E11 via app, `candidate_only=true`, `ollama_only=true`, sem Library/publicação;
  não enfileirar E12 até comparar estrutura e qualidade da candidata E11.

## Incidente da retry E11 r12: locução latina copiada

- Job `7150aeeba5e34b7d9d0920ad848e0b0c` terminou `FAILED` após 38 chamadas,
  sem retries, em `V3_TRANSLATION_SOURCE_COPY:289`. As 38 respostas de
  transporte e capturas estão duráveis no run
  `ff22542f2d1e3edbcf37ee8c/c3de80cea20b26a223e687e7bc031e71`; não foi gerada
  candidata. E11 segue com `records=[]` na Library, nenhum episódio seguinte
  foi enfileirado e a GPU permaneceu abaixo do alerta térmico.
- A captura 38 localiza o item 289: fonte `Vade retro !`; saída Qwen
  `Vade retro!`. O gate detectou corretamente a cópia integral, mas faltava
  reconhecer que se trata da locução latina de afastamento e acionar reparo
  lexical específico antes do contrato final. Não é erro do gate nem motivo
  para liberar cópias indiscriminadamente.
- Correção planejada: adicionar risco/hint restritos à expressão exata
  `Vade retro` em tradução francês→pt-BR, levando o reparo V3 existente a pedir
  uma forma localizada natural (por exemplo, “Para trás!”/“Afaste-se!”).
  Resposta que permanecer copiada continuará rejeitada. Cobrir com teste
  offline de reparo e de fail-closed, revisar independentemente, reconstruir
  runtime e só então repetir E11 em candidata Ollama-only.

## Correção V3 local para “Vade retro”

- O código agora detecta a locução somente quando a fonte inteira é `Vade
  retro` (com pontuação opcional) e a saída ainda contém essa forma. O risco
  fica limitado a francês→pt-BR, orienta tradução contextual e usa o reparo V3
  já existente; não altera nem enfraquece o gate global de cópia.
- Teste ponta a ponta determinístico comprova: resposta inicial copiada aciona
  reparo e `Para trás!` completa o pipeline; se o reparo também copiar, o
  pipeline falha fechado e não grava saída. Testes focais: `45 passed`.
- Suíte offline ampla após a mudança: `1093 passed, 38 deselected, 70 subtests
  passed`, com uma falha conhecida e não relacionada no teste legado de fallback
  visual V2.3.8 (`VISUAL_GLYPH` vs `VISUAL_GLYPH_BASE_FALLBACK`). `git diff
  --check` passou.
- As 38 capturas E11 foram verificadas com hashes canônicos de request e raw
  response: 38/38 `RESPONSE_DURABLE`, nenhum SHA divergente. Não houve chamada
  nova ao modelo; Docker segue r12 e nenhum candidato foi criado. A revisão
  independente deste patch ainda está pendente; não repetir E11 nem iniciar E12
  até o parecer, rebuild, health/mounts e preflight read-only.

## Parecer independente do reparo “Vade retro”

- Auditoria read-only concluída, sem blocker. O risco da fonte é ancorado para
  a expressão inteira `Vade retro` com pontuação opcional e está restrito a
  francês→pt-BR. A correção permanece no reparo de qualidade, sem bypass dos
  contratos V3; se persistir, falha antes de qualquer arquivo final. O auditor
  executou seis testes/probes focados sem acessar modelo ou Docker.
- Risco residual baixo: a detecção do alvo busca a expressão em qualquer ponto
  da saída; por isso, até uma resposta que acrescente a forma portuguesa junto
  da locução latina ainda será reparada/rejeitada. Variantes da fonte com
  travessão/aspas podem não receber o reparo específico; a cópia integral segue
  sujeita ao gate geral. Aceito porque a fala observada é somente essa locução.
- Próximo passo autorizado: construir r13 com o patch revisado, recriar somente
  `transass`, validar health/mounts/Library, preflight E11 e repetir somente a
  candidata E11 no Ollama sem fallback/publicação. E12 permanece bloqueada até
  comparação completa da E11.

## Reconciliação local r13 e retry E11 liberada

- Imagem local construída como
  `transass:v2.5.2-local-v3-candidate-only-20260927-r13`, digest
  `sha256:ef232f5b42942dace36820d17fc1ac044890a996fb25efff2baef63521cb3e51`,
  identidade `5b541ca-dirty-candidate-only-v3-vade-retro-repair-20260927`.
  Somente o serviço `transass` foi recriado; Ollama não foi recriado.
- Serviço healthy, `/health=ok`, V3/Qwen2.5:14b confirmados, fila vazia e Ollama
  ocioso. Bind mounts RW seguem exatamente `/PICAdeiro/data/Shows:/shows` e
  `/docker/transass/state:/app/state`.
- A candidata E10 continua íntegra (`d622e344…c2402b`); E11 segue `records=[]`
  na Library. As 38 capturas da falha anterior mantêm estados duráveis e hashes
  íntegros após o restart.
- Preflight read-only E11 ID 298 novamente elegível 1/1, fonte francesa, track
  textual ASS `FR Full ASS` interna 2. Nenhuma inferência estava ativa. Pode ser
  enfileirada agora somente a retry candidata E11, Ollama exclusivo, sem
  Library/publicação; manter E12 bloqueada até análise integral da candidata.

## Falha estrutural da retry E11 r13 e replay integral

- Job `43a1f8a3145e4c31a50f03afc2fd2846` completou 43 chamadas em 350 s, sem
  retries, e falhou na validação estrutural. As 43 capturas ficaram
  `RESPONSE_DURABLE` e os hashes canônicos de request/raw response passaram
  (`43/43`). Não há candidata, E11 continua `records=[]` na Library e E12 não
  foi enfileirada.
- Replay offline da faixa extraída do mesmo MKV conferiu SHA-256 da fonte
  `f233f73472c087750088dc77db472d9d971f37495915765f8455b70596cf5455`, 332
  eventos e correspondência dos 43 requests às capturas. Reproduziu exatamente
  cinco issues:
  - eventos 11 e 16: quebra válida entre palavras inteiras (`mim / quando`,
    `aí / que`) rejeitada porque faltavam `mim` e `aí` no léxico curto;
  - evento 250: quebra movida para antes de `Nao`; o validador não conhecia o
    nome próprio preservado ao avaliar tokens curtos na fronteira;
  - evento 19: a fonte “Puisque vous insistez...” recebeu “Como você
    insiste...\NTudo bem...”, acrescentando conteúdo e uma quebra;
  - evento 233: “Parce que ce sont des okiagari !” recebeu “Ouça-me!\NSe não
    os caçarmos,”, fala alheia e quebra inventada.
- Correção em preparação, sem relaxar validação: incorporar ao reparo de
  qualidade V3 o mismatch da contagem de quebras fonte/rascunho como risco
  reparável e revalidá-lo após a correção; manter o hard gate se persistir.
  Tornar a checagem de fronteira consciente apenas dos nomes próprios que
  ocorrem no próprio evento e acrescentar os tokens completos comuns `mim` e
  `aí`. Cobrir os cinco casos com testes offline, revisão independente, rebuild
  local e retry somente E11 após health/mounts/preflight.

## Replay corretivo e reconciliação do tag local

- O patch local identifica mismatch de contagem de quebras como risco
  reparável, mantém rejeição se o reparo persistir e permite que o validador
  considere somente nomes curtos presentes naquele evento. Acrescenta regras
  estreitas para as duas distorções semânticas observadas (`Puisque vous
  insistez` e `okiagari`).
- As capturas r13 foram reproduzidas offline: 43/43 respostas originais do
  Qwen foram consumidas, 332 eventos passaram tanto na validação AST quanto na
  validação após serialização. O replay exigiu 8 pedidos de reparo para 18
  unidades; os dois reparos semânticos foram fixtures determinísticas, e os
  demais apenas mantiveram o texto já capturado ao restaurar a quantidade de
  linhas. Isso valida integração/gates, não comprova qualidade de reparo do
  modelo. SHA-256 do artefato efêmero de replay:
  `3b7968a68f1b1df2c9e07eae62ea727077f25f23db729365cc5cce30db11ad24`.
- Para não esgotar a correção global antes de lotes posteriores, o máximo de
  reparos de qualidade V3 por tarefa subiu de 3 para 64; o teto físico geral do
  provedor continua sendo a proteção final. Um teste offline força quatro lotes
  sucessivos com mismatch e outro confirma que o limite permanece fechado.
- Testes focados: `58 passed`; suíte offline: `1098 passed`, `38 deselected`,
  `70 subtests passed` e a mesma falha não relacionada do fallback visual legado
  (`VISUAL_GLYPH` vs. `VISUAL_GLYPH_BASE_FALLBACK`).
- A consulta ao Docker inicialmente mostrou o alias de imagem `...thermal-...-r7`.
  `docker image inspect` confirmou que esse alias e o tag candidate-only r13
  apontam para o mesmo digest `sha256:ef232f5b42942dace36820d17fc1ac044890a996fb25efff2baef63521cb3e51`,
  com label OCI r13. O container está healthy, fila sem execução, job E11 r13
  falho já documentado, Ollama ocioso e bind mounts RW preservados. Era uma
  discrepância de nomenclatura de tag, não de conteúdo de imagem.
- A revisão independente deste novo patch ainda está pendente. Não foi feito
  rebuild, chamada nova ao modelo, escrita na Library nem enfileiramento do E12.

## Hardening após a primeira revisão independente

- Re-review requested after these adjustments: the 64-repair ceiling now applies
  only when a shared `operation_budget` is attached; unmetered direct transports
  retain a three-repair ceiling. Production V3 injects the durable provider and
  shared budget. A separate test verifies the unmetered limit; the four-batch
  test below exercises the durable provider and actual shared-budget reservations.
- The `Puisque vous insistez` risk now matches the observed added-concession
  rendering, not every occurrence of “tudo bem”; the valid translation “Tudo
  bem, já que você insiste” is explicitly accepted. Both French source rules
  tolerate a leading subtitle dash. Integration tests prove each semantic rule
  triggers repair without any line-break mismatch, and that persistent errors
  remain fail-closed.
- Added a regression rejecting the actual split-word counterexample
  `possibili\\Ndade` and `ver\\Ndade`, via narrow checks for suffix fragments
  that are not ordinary standalone PT-BR words. Ambiguous boundaries such as
  `Minha\\Nidade` and `qual\\Nidade` remain accepted: “idade” is a valid word,
  so inferring a split there would reject legitimate dialogue.
- Re-run: 58 focused tests passed; full offline suite `1098 passed`, `38
  deselected`, `70 subtests passed`, with the same unrelated legacy visual
  fallback assertion failure. `git diff --check` passed.
- The four-batch budget/replay test now uses the real `create_v3_live_transport`
  and durable provider with a simulated HTTP endpoint: 4 translations + 4
  repairs reserve the shared budget; the second pass reads all 8 captures and
  makes no physical call, yielding the same output hash.
- Replayed the 43 original E11 Qwen responses once more under the revised code:
  all 332 events passed AST and serialized validation; 8 deterministic repair
  fixtures were used for 18 flagged units. This is offline integration evidence,
  not a real-model retry.
- Final read-only review found no blocker. Event-local protected names are now
  exempted before split-word reconstruction. The accepted residual is explicit:
  `qual\\Nidade` can mean the valid phrase “qual idade” or the split word
  “qualidade”; without broader lexical/context analysis, the validator accepts
  this ambiguous boundary. `felicida\\Nde` is now rejected. Docker r13 remains
  untouched, E11 has no candidate, and E12 has not been queued.

## Build local r14 e reconciliação após restart

- Build local r14 concluído: imagem
  `transass:v2.5.2-local-v3-candidate-only-20260927-r14`, digest
  `sha256:cadcd7007dcc20e7d40d1d6cbda809a574c327e750d725651de598d095fe1761`,
  OCI revision `5b541ca-dirty-candidate-only-v3-breakrepair-20260927-r14`.
  Somente `transass` foi recriado; Ollama permaneceu intocado.
- Container r14 healthy e `/health=ok`; V3 com Qwen2.5:14b; mounts RW
  `/PICAdeiro/data/Shows:/shows` e `/docker/transass/state:/app/state` intactos.
  Ollama sem processos de inferência.
- Após restart, `/status` apresenta a sessão corrente vazia porque filtra por
  `session_id`; o `jobs.json` persistente continua contendo os 371 jobs
  históricos. A falha E11 r13 (`43a1f8...`) permanece registrada, sem URL/hash
  de candidata; GET do endpoint de download retornou 404. A candidata E10
  (`65e6799...`) continua `CANDIDATE_READY`, `published=false`. Os jobs antigos
  `634a6f...` (E10) e `ecc237...` (E11), datados de 2026-09-07, já estavam
  marcados `service_restarted`; não são uma nova execução desta sessão.
- Preflight read-only r14 E11 ID 298: `ELIGIBLE`, fonte ASS francesa interna
  2 (`FR Full ASS`), modelo Qwen2.5:14b, sem record anterior a substituir.
  Ainda sem nova chamada de modelo, candidata E11 ou escrita Library após o
  build. Próximo passo permitido: apenas retry E11 candidata Ollama-only; E12
  bloqueada até comparar a candidata final.
