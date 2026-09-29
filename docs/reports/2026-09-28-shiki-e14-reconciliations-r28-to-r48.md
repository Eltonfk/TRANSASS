# Reconciliação operacional — Shiki E14 candidate-only (r28 a r48)

**Data:** 2026-09-28 (America/Recife)  
**Runtime:** Docker local `transass` (r28 a r48), pipeline V3, Ollama `qwen2.5:14b`  
**Estado operacional:** Registros históricos preservados de tentativas e reconciliações E14

## Histórico de tentativas, reconciliações e auditorias

- Preflight read-only E14 (`episode_id=301`) aprovado 1/1, track `FR Full ASS`,
  textual ASS interna 2. Está liberada somente candidata E14; E15+ aguarda
  fechamento estrutural/semântico da E14.
- Primeira candidata E14 r27 (`51a070003e8c4fbf8b2319fc3218e44b`) falhou em
  `V3_TRANSLATION_SOURCE_COPY:95` para `Akira!\NAkira!`, chamada repetida de nome
  próprio sem travessão. Captura 12/hash preservados no relatório; sem candidata,
  Library ou E15. Inventário de nomes não continha Akira e o detector só cobria
  trocas com travessão. GPU pico 78 °C. Correção estreita em andamento; testes,
  review, r28 e novo preflight E14 antes de retry.
- A heurística fonética ampla foi rejeitada após revisão encontrar falsos
  positivos franceses (`Chimie`, `Chinoise`, `Chienne`, entre outros). O patch
  r28 usa apenas allowlist revisada exata `Akira` e formato repetido name-only.
  A rota legada com travessões agora também exige pt-BR e identidade integral
  após normalização tipográfica estrita; termos franceses de controle são
  rejeitados mesmo se marcados como nomes protegidos. Suítes offline
  relacionadas: 133 aprovadas; smoke offline r28 passou (OCI
  `sha256:319c8d3cba1febf6d188a3586e7518b15841a0dc93a4c2522a44c28b910eb026`).
  Controles cobrem `Banane`, `Marianne`, `Papaye`, `Chanson`, `Chapeau`,
  `Chimie`, `Chimère`, `Chinoise`, `Chienne` e `Pyjama`.
  r28 ainda não está instalado; revisão final pendente. r27 continua saudável e
  ocioso, mounts RW preservados e Ollama sem restart/job. Nenhuma inferência
  nova; E14 retry requer review, recreate somente Transass, health/mounts/Ollama,
  reconciliação documental e novo preflight read-only.
- Reconciliação r28 realizada: somente `transass` foi recriado. Imagem
  `transass:v2.5.2-local-v3-candidate-only-20260928-r28`, OCI
  `sha256:319c8d3cba1febf6d188a3586e7518b15841a0dc93a4c2522a44c28b910eb026`,
  V3/2.5.2 saudável; fila ociosa. Mounts RW de mídia e state preservados; a
  captura da falha E14 anterior continua no volume com SHA conferido. Ollama
  permaneceu running, `restart_count=0`, sem inferência. Novo preflight
  read-only E14 aprovou 1/1 na track ASS francesa textual, stream interno 2.
  Nenhuma nova model call ainda. Liberada apenas uma candidata E14 isolada,
  Ollama-only/candidate-only e sem Library/publicação; E15+ aguarda fechamento
  estrutural e semântico da E14.
- Atualização posterior: a candidata E14 r28 (`3919493568dc4e4fa1d5addd83376e23`)
  falhou após 24/24 respostas em `V3_TRANSLATION_SOURCE_COPY:191`, para a
  identidade correta `Kyôko Ozaki.`; ambos os nomes estavam protegidos no prompt.
  Captura 24/SHA e GPU pico 79 °C estão no relatório; sem candidata ou publicação.
  Auditoria encontrou ainda bypasses pelo fallback francês de Title Case. O
  patch local restringe identidade a todos os tokens protegidos + pt-BR + texto
  visível exatamente igual, e faz cópias francesas capitalizadas não aprovadas
  falharem fechadas. Testes focados: 134 aprovados; review final pendente. O
  container continua r28, sem nova inferência. Próximo: review, build/recreate
  somente Transass em r29, health/mounts/Ollama, registrar reconciliação e novo
  preflight E14 antes de qualquer retry. E15+ bloqueado.
- Imagem r29 já construída offline a partir de r28 (OCI
  `sha256:4a6b01f49ccc4d919eb02f3932f0f95d5b99f386601863f5e206572040243a09`),
  `py_compile` e smoke adversarial sem rede/mounts aprovados; testes offline
  focados 134/134. O serviço permanece r28/ocioso após a falha E14; nenhuma nova
  inferência. Review independente final pendente; instalar r29, verificar
  health/V3/mounts/Ollama e fazer novo preflight E14 antes de retry. E15+ segue
  bloqueado.
- Reconciliado depois: somente Transass foi recriado em r29; health/V3/2.5.2
  saudáveis, fila vazia, mounts RW preservados; Ollama ativo sem restart/job.
  Captura E14 r28 permanece no state e SHA confere. Preflight read-only E14
  aprovou 1/1 na track ASS francesa, stream 2. Nenhuma inferência desde o job
  falho. Revisão encontrou bypass no atalho da interjeição `Hein` + Title Case;
  patch local restringiu a destino pt-BR, igualdade exata e tokens restantes
  protegidos não-franceses. 134 testes focados passaram; r29 não tem esse patch.
  Review final, r30, recreate somente Transass, verificações operacionais e
  novo preflight E14 são necessários antes de retry. E15+ permanece bloqueado.
- Auditoria adicional encontrou bypass para cópia francesa de palavra única e
  stopword francesa disfarçada de sobrenome após partícula. O patch local agora
  bloqueia qualquer sequência lexical francesa idêntica após avaliar somente as
  exceções estritas revisadas; o sobrenome após partícula também não pode ser
  stopword francesa. Testes focados passaram 134/134. Imagem r31 construída
  offline e smoke isolado aprovado (OCI
  `sha256:b8a11a3e353aec20efc9aefdf0be9529721940f76fbcfd691c9a15a05298242d`).
  Ainda está em r29, sem nova inferência; revisão final pendente. Próximo:
  review, instalar somente Transass/r31, verificar mounts/Ollama e fazer
  preflight read-only antes de um novo retry isolado E14. E15+ bloqueado.
- Reconciliação r31 concluída: somente Transass recriado; health ok, V3/2.5.2,
  fila vazia, mounts RW preservados. Ollama segue running com restart_count 0 e
  sem inferência. Captura da falha E14 r28 permanece no state com SHA verificado.
  Preflight read-only do Shiki S01E14 (ID 301) aprovou 1/1 na track `FR Full
  ASS`, stream 2, Qwen2.5:14b. Nenhuma model call neste passo. Testes focados
  134/134 e smoke offline r31 passaram; revisão final adicional foi solicitada,
  mas não respondeu. Liberada somente uma candidata isolada E14; sem Library/
  publicação. E15+ bloqueado até auditoria completa da E14.
- A candidata E14 r31 falhou em `V3_TRANSLATION_SOURCE_COPY:14`: `Atsushi !` →
  `Atsushi!`, nome romanizado isolado ausente do inventário; duas respostas
  HTTP 200 e capturas preservadas no state, sem candidata/publicação. Pico GPU
  83 °C, sem trip. Correção aceita apenas identidade exata pt-BR de um token
  fortemente romanizado e não stopword; múltiplos tokens seguem exigindo âncora
  protegida. Suítes focadas 135/135; r32 construída offline e oito probes
  aprovados (OCI
  `sha256:9615b5d758b9598cbfc3bebd89d80b60579ca88c8f99cc8a65c5e530d84df7e6`).
  App ainda r31, fila falha/ociosa; revisão independente read-only pendente.
  Próximo: instalar somente r32, validar health/mounts/Ollama, preflight e nova
  tentativa E14 isolada. E15+ bloqueado; sem Library/publicação.
- Reconciliação r32 concluída: health/V3/2.5.2, fila vazia; mounts RW
  preservados. Capturas da falha r31 verificadas, Ollama running sem restart/job.
  Preflight read-only E14 aprovou 1/1 na track `FR Full ASS` (stream 2),
  Qwen2.5:14b. Nenhuma nova model call até este ponto. Está liberada uma única
  candidata isolada E14 r32, sem Library/publicação; review independente
  permanece pendente. E15+ bloqueado até validar E14.
- A candidata E14 r32 falhou em `V3_TRANSLATION_SOURCE_COPY:98`: `Kaori !` →
  `Kaori!`; a captura 13 preservada comprova nome não marcado ausente do prompt
  (que só protegia Megumi). Sem candidata/Publicação; GPU observada até 81 °C,
  sem trip. Auditoria identificou ainda sobrenomes franceses (`France`, `Danse`,
  `Chambre`) liberados após partícula e aceitação de target por substring
  `pt-BR`; ambos corrigidos. `Kaori` foi incluído em allowlist explícita
  revisada, em regra de identidade de token único. Testes focados 136/136; r33
  construída offline e 12 probes passaram (OCI
  `sha256:a94d873dcabfb39771c58a8284298c799f8a9b73525af63600927b409719d436`).
  App ainda em r32, ocioso após falha; mounts preservados, Ollama sem restart.
  Revisão independente do patch final pendente. Próximo: instalar r33 e refazer
  preflight E14 antes de nova candidata. E15+ segue bloqueado.
- Reconciliação r33: somente Transass recriado; health/V3/2.5.2, fila vazia e
  mounts RW preservados. Captura r32 permaneceu íntegra; Ollama running,
  restart_count 0, sem inferência. Preflight read-only E14 aprovou 1/1 na track
  `FR Full ASS`, stream 2, Qwen2.5:14b. Nenhuma nova model call ainda.
  Liberada uma candidata isolada E14, sem Library/publicação; revisão final do
  patch corrigido segue pendente. E15+ aguarda auditoria E14.
- A candidata E14 r33 falhou em `V3_TRANSLATION_SOURCE_COPY:241`: fragmento
  progressivo `To...`/`Toshi...` de `Toshio`, nome reconhecido no prompt mas
  ausente do texto da unidade. Captura 31 preservada, SHA registrado; sem
  candidata/publicação, GPU pico observado 81 °C sem trip. Novo gate reconhece
  somente prefixo com reticências e correspondência única em nome protegido;
  adiciona esse nome ao contexto unitário. Suítes focadas 138/138 e 12 probes
  passaram. r34 construída offline (OCI
  `sha256:2d4718b61b046091b84d89e002532b14a69cbbcc64e200f5ec1187e22b34aca7`).
  App ainda r33, terminal/ocioso, mounts preservados, Ollama sem restart/job;
  revisão read-only pendente. Próximo: instalar r34, verificar estado e novo
  preflight E14; E15+ permanece bloqueado.
- Reconciliação r34 concluída: health/V3/2.5.2, fila vazia, mounts RW
  preservados; captura r33 revalidada por SHA. Ollama running, restart 0, sem
  inferência. Preflight read-only E14 aprovou 1/1 na track `FR Full ASS` stream
  2, Qwen2.5:14b. Sem model call ainda. Liberada somente uma candidata isolada
  E14 r34, sem Library/publicação; review read-only permanece pendente. E15+
  bloqueado até validação completa da E14.
- Reconciliação pós-r34: a candidata E14 falhou novamente em
  `V3_TRANSLATION_SOURCE_COPY:241` após 31 chamadas Qwen2.5:14b, sem fallback e
  sem artefato válido/publicação. A captura 31 tem HTTP 200, request SHA
  `c43fa84708601b18ee9fe041d57ca5640b8746c4a0b650a6dfb80834dad53168` e
  response SHA `86f06f7bf5c544abc7cff2c7887d02697ba4b215a68bd14d342818ffcfff3d16`.
  A stream ASS comprova a ambiguidade global de `To...` entre `Tokujirô` e
  `Toshio`, seguida pelos fragmentos contíguos `Toshi...` e `Toshio...`. O gate
  agora resolve somente esse padrão estrito, sem relaxar a rejeição de cópias.
  Testes focados 140/140; suíte offline 1.136 pass, 2 falhas fora do patch, 38
  deselecionados e 70 subtestes. Imagem r35 construída offline e 12 probes
  passaram (OCI `sha256:da1a6ec284f8cf8ca8765d5e6efe78df484e980a79bc363f5def0ff6d919e549`);
  revisão independente read-only ainda pendente. App segue r34; nenhuma nova
  tradução desde a falha r34. E15+ continua bloqueado.
- Auditoria independente do r35 encontrou P2: prefixo que parecia único no
  inventário ainda podia liberar identidade sem evidência progressiva. O atalho
  foi removido; a autorização de cópia agora é separada e só vem de uma janela
  completa de três eventos ASS consecutivos, mesmo lote/estilo, lacunas
  `0–2500 ms`, fragmentos em prefixo crescente e nome final protegido exato.
  Isso habilita tanto `To...` como `Toshi...` somente dentro da sequência real.
  Testes focados permanecem 140/140; r35 não foi instalada nem usada para
  inferência. Revisão final read-only do patch estrito pendente. App segue r34,
  sem novas chamadas; E15+ bloqueado.
- r36 estrita construída offline sobre r35 (OCI
  `sha256:dc58f1825b421efa492196f0e8e8d881e82d48277e02f86a3c5368481571405a`),
  `py_compile` e 17 probes isolados aprovados. Focados 140/140; suíte offline
  completa mantém 1.136 pass, 2 falhas fora do patch, 38 deselecionados e 70
  subtestes. Follow-up do review read-only ainda sem resposta (P2 original
  corrigido). Serviço continua r34, sem nova inferência. Próximo: instalar
  somente r36 e executar preflight E14; sem Library/publicação; E15+ bloqueado.
- Reconciliação r36 concluída: somente Transass recriado; health ok,
  `/version` V3/2.5.2, fila vazia, mounts RW preservados. Ollama running,
  restart 0, `ollama ps` vazio. Preflight read-only E14 ID 301 aprovou 1/1 na
  faixa francesa `FR Full ASS`, stream textual interna 2, Qwen2.5:14b. Nenhuma
  chamada ao modelo no preflight. Autorizada uma candidata E14 no app,
  candidate-only/ollama-only, sem fallback e sem Library/publicação. E15+
  permanece bloqueado até conclusão/auditoria estrutural da E14.
- A candidata isolada r36 do E14 (job `a62a1e5b28f5421d8bc8c5a823340014`,
  run `462a20c5b17afb73a966e9f5`, operação
  `adebb5245f9ae3079e8520f610667377`) completou 36 chamadas com Qwen2.5:14b,
  sem fallback, e falhou na validação estrutural final; nenhum artefato válido
  foi criado, arquivado ou publicado. O validador apontou quebras visuais nos
  eventos 197 e 252. A captura 25 mostra `L'EEG commence\Nà réagir...` →
  `O EEG\Ncomeça a reagir...`; aqui `EEG` é um token completo, e a regra
  confundia o acrônimo preservado com fragmento. Captura bruta SHA-256
  `2428edee052a97e6db13f88f7961cd48d512abe46865a3f5749dc11e7006593b`.
  A captura 32 mostra `C'est comment éliminer\Nces Shi Ki.` →
  `É como eliminar\Nces Shi Ki.`: a quebra é válida entre palavras, mas o
  demonstrativo francês `ces` ficou sem traduzir. Resposta bruta SHA-256
  `f7aa950c8701e3ee368e3b066c5f9ab5521a1088bfb76eb0886db019b7af0d1f`.
  Ambas permanecem preservadas no state, sob
  `/app/state/v3-runs/462a20c5b17afb73a966e9f5/adebb5245f9ae3079e8520f610667377/captures/`.
  A telemetria ficou abaixo do alerta térmico (pico observado ~83 °C), sem trip.
  Correção em código: detecção/reparo específico para `ces` demonstrativo em
  fonte francesa, e exceção de quebra só para token uppercase curto presente
  no mesmo evento-fonte; quebra dentro de `EEG` permanece inválida. A exceção
  também foi alinhada à auditoria read-only. Testes direcionados: 107 passaram,
  1 teste preexistente foi excluído por ser falha conhecida. Suíte offline:
  1.140 passaram, 2 falharam (fallback visual V2.3.8 e auditoria de nomes
  próprios), 38 deselecionados e 70 subtestes. Revisão independente deste patch
  e construção/reconciliação da próxima imagem ainda pendentes. Não houve nova
  inferência depois da candidata r36. E15+ continua bloqueado; sem
  Library/publicação.
- Reconciliado r37 após as correções da falha E14: OCI
  `sha256:5ce8251210b7a73493c658d6502ec4113bb16024fe6d1a6afad49e1ead12a32b`,
  health healthy, `/version` V3/2.5.2 e revision r37. Somente Transass foi
  recriado; fila vazia, mounts RW `/PICAdeiro/data/Shows -> /shows` e
  `/docker/transass/state -> /app/state` intactos. Ollama running,
  restart_count 0 e sem modelo carregado. Smoke isolado passou 9 assertions;
  preflight read-only E14 aprovou 1/1 na faixa ASS francesa, modelo
  Qwen2.5:14b. Review independente read-only ainda pendente; nenhuma chamada
  ao modelo desde a falha r36. Aguarda-se o parecer antes de nova candidata
  E14. E15+ segue bloqueado; sem Library/publicação.
- `CANONICAL_STATE_RECONCILIATION_REQUIRED`: a revisão read-only independente
  do r37 encontrou dois blockers, e a execução real E14 r37 acrescentou
  findings finais não previstos. Bloqueados novos jobs/model calls, Library,
  promoções e publicação até a reconciliação abaixo estar refletida em código,
  testes, documentação e nova imagem reconciliada.
- E14 r37 (`86e9053246de44ae9348b718d19f7e88`) completou 36 respostas duráveis
  Qwen2.5:14b sem fallback; nenhum resultado foi aprovado para catálogo ou
  publicação. O audit final apontou `LINE_BREAK_INSIDE_WORD`,
  `POSSIBLE_UNTRANSLATED_OUTPUT` e `UNBALANCED_DELIMITERS`. O pedido cooperativo
  de parada foi processado no estágio de auditoria; estado final `CANCELLED`/
  `STOPPED`, erro da reprovação preservado. Candidato diagnóstico SHA-256
  `0c57a77e600cb39296cef7b80875868f98dfb08c5c9b49efed3adb0a29595cc3`, com
  metadata SHA-256
  `dc96cb4e43659f37b9d23e21518c851812e48eece637b606e2628ebb5e36140b`.
  Arquivos imutáveis em
  `/app/state/anime-subtitle-library/diagnostics/retranslation-86e9053246de44ae9348b718d19f7e88/`;
  são diagnóstico fora do catálogo, `candidate_only=true`, `published=false`,
  sem registro de legenda. A gravação em `anime-subtitle-library/diagnostics`
  é uma escrita de state acionada pelo fail-safe do app e fica explicitamente
  documentada; não remover.
- A última resposta durável está em run
  `aa9749e63450dcbdf8bd803e`, operação
  `d37574d20d0a158b4df01f83d0df6263`, captura 36, SHA-256
  `18cf2032aa4a5cd70042f194e2071090ee336f4225cfb24665a9ce407409890b`.
  Findings concretos: evento 0 (`Les Shi Ki\Ntraquent...` →
  `Os Shi Ki\Ncaçam...`) classificado com quebra inválida junto ao token
  `Ki`; eventos 10, 148, 191, 241 e 242 foram falsamente sinalizados como
  texto não traduzido embora sejam `SHI KI`, `Kyôko`, `Kyôko Ozaki`, `To...` e
  `Toshi...`. Nos eventos 233–234 o modelo acrescentou aspas finais que a fonte
  não contém, causando `UNBALANCED_DELIMITERS` e reprovação fatal.
- O review independente encontrou ainda falso positivo na regra `ces`: uma
  saída correta com o acrônimo `CES` podia acionar o reparo francês; também
  confirmou o falso negativo global `dez\Nembro` devido à exceção de `dez` no
  léxico curto. Correções pendentes: restringir `ces` a caixa de demonstrativo
  (preservando `CES`), bloquear a divisão de `dezembro` sem perder `dez casos`,
  alinhar nomes próprios/progressivos e limites de quebra na auditoria, e
  detectar/reparar delimitadores por evento antes da auditoria final. Nenhum
  novo modelo deve ser chamado até testes, documentação e runtime candidata
  cobrirem esses casos.

### Adendo de reconciliação r37 — 2026-09-28

O código local agora incorpora os quatro pontos acima, ainda sem imagem/runtime
reconciliado e sem nova chamada ao modelo: (1) a regra francesa sinaliza `ces`
e `Ces` demonstrativos, mas não o acrônimo `CES`; (2) `dezembro` foi incluído
na proteção contra `dez\Nembro`, preservando a palavra curta independente
`dez` em `dez casos`; (3) a auditoria read-only usa evidência do evento-fonte
para siglas, nomes repetidos e fragmentos progressivos, sem uma exceção global
para texto copiado; (4) contagem de aspas/parênteses/colchetes é comparada por
evento, e o V3 solicita reparo direcionado antes da validação/escrita final.

Verificação desta revisão: 122 testes direcionados passaram (1 teste conhecido
de nomes próprios foi isolado nessa execução). A suíte offline completa passou
1.146 testes e reproduziu 2 falhas já conhecidas, com 38 testes deselecionados
e 70 subtestes: fallback de glifos visuais V2.3.8 e expectativa ampla da
auditoria para nomes próprios sem evidência suficiente. `git diff --check`
passou. A revisão independente read-only do patch atual ainda está em curso;
nenhum container/imagem r38 foi construído ou instalado. O marcador
`CANONICAL_STATE_RECONCILIATION_REQUIRED` permanece ativo: não iniciar nova
candidata nem E15+ até review, build isolado, instalação somente do Transass,
checagens de mounts/saúde/Ollama e preflight read-only E14. Sem fallback e sem
Library/publicação.

Reauditada offline a candidata diagnóstica preservada contra a faixa francesa
ASS extraída do E14: os 283/283 hashes de texto-fonte por evento coincidem com
a auditoria r37. Com o código atual, os falsos alertas de quebra e nomes
desaparecem; permanecem somente `UNBALANCED_DELIMITERS` nos eventos 233–234,
onde r37 acrescentou aspas finais. Isso confirma que a validação/reparo novo é
necessário e que o restante da candidata passou esses checks específicos; não
é aprovação da candidata, pois o defeito das aspas continua e não houve nova
inferência.

### Atualização do gate r38 — 2026-09-28

A revisão independente não retornou parecer após reenvios; portanto não é
registrada como aprovada. A revisão manual local identificou que a regra fica
limitada por evento-fonte e que a correção de delimitadores é revalidada após
uma única reparação. A imagem r38 foi construída offline sobre r37, digest
`sha256:1a453dca064b1476e443988e6600ba0fcb1bd6fd364ebe0c9850e184206ae257`, e
passou cinco assertions determinísticas em container descartável (`--network=none`,
sem mounts/modelo). A suíte segue com os resultados descritos acima.

Antes da instalação: serviço r37 healthy/ocioso, job atual nulo, fila sem
execução, mounts RW `/PICAdeiro/data/Shows -> /shows` e
`/docker/transass/state -> /app/state` intactos; Ollama running, restart_count
0 e `ollama ps` vazio. Próxima ação é atualizar somente Transass com
`--no-deps`, verificar `/health`, `/version`, `/status`, mounts e Ollama e
executar preflight read-only E14. Nenhuma model call ocorreu após r37; sem
fallback e sem Library/publicação.

### Atualização após review e candidata r38 interrompida — 2026-09-28

O revisor independente retornou três blockers: (1) saída `CES` podia ser
aceita como demonstrativo `ces` mesmo sem `CES` na fonte; (2) validar somente
contagens não detectava delimitadores em ordem inválida, como `(texto)` →
`)texto(`; (3) a exceção de sigla curta da fonte podia suprimir a regra
`dezembro` para `dez\Nembro`. O gate volta a `CANONICAL_STATE_RECONCILIATION_REQUIRED`;
nenhuma nova inferência/job deve iniciar até esses casos terem correção,
testes, review, imagem e preflight reconciliados.

Antes de receber esse parecer, foi iniciada uma única candidata E14 r38 sob a
autorização candidate-only/Ollama-only, sem fallback/publicação. Job
`da47fcedf3f84b99b3d838bb07506e94`, sessão `ad9034bda13d4040b396a5ce977c9f4b`,
run `1f9eab7e669ab942087ea175`, operação
`4abc7abb02dd8e2769ff2a12350d05f5`. A parada cooperativa foi solicitada assim
que os blockers chegaram. Resultado `CANCELLED`/`STOPPED`, código `-15`, 20
chamadas iniciadas, 19 unidades concluídas; capturas 1–19 duráveis e a captura
20 contém request/state sem resposta HTTP durável. Evidência mantida em
`/app/state/v3-runs/1f9eab7e669ab942087ea175/4abc7abb02dd8e2769ff2a12350d05f5/captures/`.
Não houve candidata final/diagnóstico, registro de Library ou publicação.

Após a parada: `current_job=null`, fila sem execução; Ollama permaneceu
running, sem restart, com Qwen2.5:14b ainda carregado até unload normal. Não
recriar Ollama nem apagar capturas. Correções pendentes: amarrar a exceção
`CES` a ocorrência/contagem comprovada na fonte; comparar a sequência/ordem de
delimitadores por evento, além da quantidade, no quality gate e na auditoria;
executar testes da combinação sigla+split para que `dez\Nembro` siga bloqueado.
E15+ permanece bloqueado; sem Library/publicação.

### Correções seguintes aos blockers r38 — 2026-09-28

O patch local agora (1) só isenta token `CES` na saída quando a fonte traz a
mesma ocorrência uppercase, mantendo resíduo `ces`/`CES` sem suporte sinalizado;
(2) compara sequência ordenada de delimitadores por evento, além das contagens,
incluindo aspas tipográficas/guillemets, no reparo V3 e na auditoria; (3) testa
palavras candidatas a split (`dezembro`) antes das exceções de token uppercase,
sem quebrar `dez casos` nem o limite autorizado `ver\N Dade`.

Regressões direcionadas: 126 passaram, 1 teste conhecido de nomes próprios foi
isolado. Suíte offline: 1.150 passaram, 2 falhas conhecidas (fallback visual
V2.3.8 e expectativa ambígua de nomes sem evidência contextual), 38
deselecionados e 70 subtestes; `git diff --check` passou. Review independente
do patch atualizado solicitado e pendente. r39 ainda não construído; r38 segue
instalado, mas não iniciar outro job/model call até review, build/smoke r39,
verificação do container e preflight read-only. Capturas r38 preservadas; sem
Library/publicação. E15+ bloqueado.

### r39 empacotado, aguardando review final — 2026-09-28

A imagem r39 foi construída offline sobre r38, digest
`sha256:b7288a493c05d42aec6ac86a1fd08083bd4c6dcd643488383dbf58feb6b2fe82`;
`py_compile` da camada passou. Container descartável `--network=none`, sem
volumes/modelo, passou 10 assertions cobrindo os três blockers, incluindo
guillemets e fronteira `ver Dade`. A revisão read-only do patch pós-correções
continua sem resposta; não está marcada como PASS. Serviço r38 segue instalado;
r39 ainda não instalada. Sem novos model calls, jobs, Library/publicação até
review final, instalação somente do Transass, checagem de health/mounts/Ollama e
preflight read-only.

### r39 instalado e preflight E14 aprovado — 2026-09-28

Depois do smoke isolado, r39 foi instalada somente no serviço Transass
(`--no-deps`), digest
`sha256:b7288a493c05d42aec6ac86a1fd08083bd4c6dcd643488383dbf58feb6b2fe82`;
health healthy, `/version` 2.5.2/V3/r39, fila vazia. Mounts RW continuam
`/PICAdeiro/data/Shows -> /shows` e `/docker/transass/state -> /app/state`.
Ollama permaneceu running, restart_count 0, `ollama ps` vazio. Preflight
read-only de E14 aprovou 1/1: ID 301, faixa `FR Full ASS`, stream 2/francês,
Qwen2.5:14b, pipeline V3. A revisão independente pós-correção segue sem
retorno; não é chamada PASS. Os blockers originais foram cobertos por testes
focados/full suite e 10 assertions do smoke. Uma única candidata E14 pode agora
prosseguir sob a autorização explícita candidate-only/Ollama-only, sem fallback
ou Library/publicação; E15+ depende de auditoria completa bem-sucedida.

### Novo blocker CES e candidata r39 interrompida — 2026-09-28

Review independente encontrou contraexemplo à isenção por contagem: fonte
`Les membres du CES commentent ces photos.` e saída `Os membros comentam CES
fotos.` preservavam o total de `CES`, mas moviam o acrônimo para a posição do
demonstrativo francês. A regra deve associar cada sigla à sua posição/contexto
na fonte; a contagem global não é evidência suficiente.

Por isso o único job E14 r39 foi parado cooperativamente: job
`8248a120217f4653b42d2b3adcbe0105`, sessão
`438f7f966e7c4b36a28e29d20d071e1c`, run `0b0d66da5a8fc9d25a369438`, operação
`915178bd5babc24fd9b39f53caa8bf54`. Estado `CANCELLED`/`STOPPED`, código `-15`,
18 chamadas iniciadas, 17 unidades concluídas. Capturas 1–17 duráveis; captura
18 com request/state e sem resposta HTTP durável, preservadas em
`/app/state/v3-runs/0b0d66da5a8fc9d25a369438/915178bd5babc24fd9b39f53caa8bf54/captures/`.
Sem candidata final/diagnóstico, escrita na Library ou publicação. Ollama não
foi reiniciado; modelo carregado deve descarregar normalmente.

Patch local agora associa ocorrência uppercase `CES` de saída a contexto
precedente comprovado (`du CES` → `do CES`) em correspondência 1:1; o
contraexemplo deslocado deve continuar sinalizado. Regressão adicionada, testes
focados 126 passaram; suíte completa estava em execução no registro deste
adendo. O gate `CANONICAL_STATE_RECONCILIATION_REQUIRED` permanece: sem novos
jobs/model calls até full suite, review read-only final, nova imagem/smoke e
preflight. Sem fallback, Library/publicação; E15+ bloqueado.

### Revisão r42 em andamento — evidência aditiva, 2026-09-28

Uma segunda revisão read-only encontrou dois riscos no pareamento bilateral de
`CES`: vizinhos idênticos quando uma ocorrência era título entre guillemets e
outra demonstrativo; e falsos bloqueios de sigla em traduções naturais. O patch
local seguinte passou a considerar posse de aspas, classificações conservadoras
de fonte em caixa alta e casos de contexto lexical traduzido. Foram adicionados
testes para o contraexemplo do título `CES Séries`, a linha integralmente
uppercase e as traduções naturais `Le groupe CES présente ces données`,
`Le CES publie ces avis` e `Les membres du CES étudient ces dossiers`.

Suíte focada de `CES`: 7 passaram; suíte offline: 1.153 passaram, 2 falhas
conhecidas não relacionadas (fallback visual V2.3.8 e expectativa de nomes
próprios), 38 deselecionados e 70 subtestes; `git diff --check` passou. A imagem
r42 foi construída offline, digest
`sha256:a87913b8dd0e3372f2a4eb13fc319d41941e26703f2b5ec4b63219646a32cb8d`, e
passou 16 assertions em container descartável sem rede/volumes. r41 é obsoleta
(digest `sha256:3bd2cbf6843809d9e18c1dc58f613bf4e1fdbaaeabe1e7380727f24b285ec245`);
r42 ainda não foi instalada. A revisão independente do patch r42 foi solicitada
e está pendente, portanto não há aprovação registrada.

O Transass em execução permanece r39, healthy e sem job atual; apenas a
tentativa E14 anterior consta como cancelada. Mounts RW de episódios e state
permanecem intactos. Ollama continua running, restart_count 0 e sem modelo
carregado. Nenhuma chamada nova ao modelo, nova candidata, escrita na Library
ou publicação ocorreu durante esta revisão. Até o parecer r42 e a reconciliação
runtime serem concluídos, manter `CANONICAL_STATE_RECONCILIATION_REQUIRED` e
bloquear novas inferências/jobs; E14 ainda não está aprovado e E15+ não foi
iniciado.

### Correções pós-review e candidata r43 — 2026-09-28

O review r42 encontrou: falso negativo em `ILS PARLENT DE CES PHOTOS`, falsos
bloqueios quando a sigla legítima aparecia em traduções naturais (`parle`,
`est`, `représente`) e outro deslocamento possível quando só um vizinho
coincidia. A correção atual limita a heurística caixa-alta a linhas sem letras
minúsculas, dá precedência ao padrão `de ces + substantivo`, aceita `Ces` de
título apenas com evidência tipográfica entre aspas, compara estilo de caixa
como desempate de contextos idênticos e usa equivalências/cognatos restritos
para detectar o deslocamento `acteurs` → `atores`. A regra só gera flag quando
a unidade-fonte contém um demonstrativo provável; exemplos naturais e fontes
somente com sigla não recebem alerta.

Testes focados CES: 7 passaram; suíte offline completa: 1.153 passaram, as
mesmas 2 falhas conhecidas não relacionadas, 38 deselecionados e 70 subtestes.
`git diff --check` passou. Nova imagem r43, construída sem rede, digest
`sha256:8b93c99a96765949ca05aca10b3a599661c562f511f3e0894ae29f915f5fda1d`,
passou 22 assertions em container descartável, sem rede/volumes/modelo. r43
não foi instalada. A revisão independente desta rodada ainda está pendente;
nenhum resultado é declarado como aprovação.

Transass segue em r39 healthy/ocioso, sem job ativo; Ollama running, sem restart
e sem modelo carregado; mounts RW permanecem intactos. Não houve novas model
calls, job/candidata, Library/state real ou publicação. `CANONICAL_STATE_RECONCILIATION_REQUIRED` permanece até review read-only final,
instalação somente do Transass, health/version/status/mount verification e
preflight read-only E14. Não iniciar E14/E15+ antes desses gates.

### r46 — desempate por contexto amplo, revisão pendente — 2026-09-28

Uma revisão posterior encontrou dois riscos no desempate por posição: mover o
título para o lugar do demonstrativo podia passar, enquanto uma tradução
legítima em voz passiva podia ser sinalizada. O patch atual acrescenta o par
lexical anterior (por exemplo, `diffusion de` → `transmissão de` versus
`sélection de` → `seleção de`) antes de considerar caixa/posição. Foi
acrescentada a regressão da forma passiva legítima e mantido o caso em que o
demonstrativo recebe `CES` no local errado. A nova revisão independente ainda
está pendente; não considerar a regra aprovada até seu retorno.

Resultado offline atualizado: 1.153 passaram, 2 falhas conhecidas não
relacionadas, 38 deselecionados, 70 subtestes. Smoke r46: 26 assertions em
container efêmero sem rede/volumes/modelo. Digest
`sha256:f2868199d1d89e2f4a18d49171c14cffb7284517faab0090f2e02d5f515f4c32`;
r46 não foi instalada. r44 e r45 são imagens locais anteriores e também não
instaladas.

Sem novos jobs/model calls desde a candidata E14 r39 cancelada; sem Library,
promoção ou publicação. Serviço r39 healthy e ocioso, Ollama running sem
restart e sem modelo carregado, mounts RW de episódios/state intactos. Manter
`CANONICAL_STATE_RECONCILIATION_REQUIRED` até parecer final e preflight E14
somente leitura; E14 ainda não tem resultado válido e E15+ permanece pendente.

### r47 — equivalência de contexto para “exibição”, revisão pendente — 2026-09-28

O review do r46 encontrou um falso bloqueio em tradução passiva legítima que
usava `exibição` como tradução de `diffusion`. A evidência de contexto agora
inclui equivalências usuais de broadcast (`transmissão`, `difusão`, `exibição`,
`divulgação`, `disseminação`, `veiculação`, incluindo formas sem acento onde
cabíveis). Adicionada regressão para `A escolha dessas séries é substituída
pela exibição de CES Séries`, que deve preservar o título e traduzir o
demonstrativo.

Após a correção: 7 testes focados CES passaram; suíte offline 1.153 passou, 2
falhas conhecidas não relacionadas, 38 deselecionados e 70 subtestes. Smoke
r47: 27 assertions; digest
`sha256:b08baa98828e3c450af7c4ae4a3c3612541311924caddbffbc7a0e795aef5397`.
Imagem sem rede, volume ou modelo; não instalada. Parecer read-only final das
duas revisões independentes ainda pendente.

Transass continua r39 healthy/ocioso, Ollama running sem restart/modelo, mounts
intactos. Nenhuma inferência, job, Library, promoção ou publicação nova. Manter
`CANONICAL_STATE_RECONCILIATION_REQUIRED`; r47 não pode ser instalada nem
usada para job enquanto os gates de review e reconciliação não concluírem.

### Reavaliação do CES e correção real pendente da E14 — 2026-09-28

Adendo corretivo aos registros r42–r47: a comparação das capturas duráveis da
E14 confirma que a frase real do evento 252, `C'est comment éliminer\\Nces Shi
Ki.`, recebeu em r37 a tradução correta `É como eliminar\\Nesses Shi Ki.`. Nas
capturas parciais r38/r39 não há ocorrências `ces/CES` correspondentes. Portanto,
os cenários mistos de sigla+demonstrativo que motivaram as heurísticas r42–r47
são contraexemplos sintéticos de revisão, não uma falha observada nessa legenda.
O defeito operacional confirmado da candidata E14 r37 continua sendo a
propriedade/quantidade de aspas nos eventos 233–234; a auditoria final reprovou
esses eventos. Essa distinção não apaga os pareceres históricos nem declara a
E14 concluída.

Para evitar inferência sintática frágil e falsos bloqueios por paráfrases, a
candidata local simplifica o gate CES para evidências determinísticas: rejeita
`ces` residual em minúsculas, `Ces` sem correspondência comprovada a título
entre aspas e quantidade excedente de `CES` em relação às siglas reconhecidas
na fonte. Em legendas totalmente maiúsculas, só classifica padrões
demonstrativos restritos; casos ambíguos não são alegados como semanticamente
resolvidos. Limitação explícita: se uma unidade contém simultaneamente uma
sigla legítima e um demonstrativo e o modelo desloca a sigla para o lugar do
demonstrativo mantendo a mesma contagem, este gate lexical não garante detectar
o deslocamento; não deve ser descrito como alinhador semântico geral.

Verificações desta versão local: os dois arquivos diretamente afetados
passaram, 97 testes (`test_translation_quality.py` e
`test_pipeline_v3_integration.py`); a suíte offline completa executou 637 testes
e falhou em 1 regressão já conhecida e não relacionada, a expectativa de
fallback visual V2.3.8 (`VISUAL_GLYPH` versus
`VISUAL_GLYPH_BASE_FALLBACK`). `git diff --check` passou. A revisão independente
da versão simplificada ainda está pendente; nenhuma imagem desta versão foi
construída/instalada e nenhum modelo foi chamado.

Último estado operacional registrado segue sendo serviço Transass r39
healthy/ocioso, Ollama running sem restart, mounts RW de episódios e state
preservados. Desde a candidata E14 r39 cancelada, não houve nova tradução,
model call, escrita na Library, promoção ou publicação. E10–E13 permanecem
aprovadas como candidatas isoladas; E14 ainda não tem PASS estrutural. Manter
`CANONICAL_STATE_RECONCILIATION_REQUIRED`: aguardar parecer read-only, smoke de
imagem sem rede/volumes/modelo, instalação somente do Transass, checagens de
saúde/mounts/Ollama e preflight read-only E14. Depois, executar uma candidata
E14 de cada vez; E15+ só após a auditoria estrutural da E14. Sem fallback e sem
Library/publicação.

### Aspas curvas corrigidas e revisão delimitadora concluída — 2026-09-28

Revisão independente encontrou lacuna estrutural concreta: `‘Libres ?` →
`‘Livres?’’` passava porque a tabela central não reconhecia aspas curvas
simples. Não é erro observado na candidata E14, mas poderia afetar qualquer
episódio. Correção local em `ass_engine.py`: `‘`/`’` são contados como
delimitadores somente em fronteiras de palavra, sem confundir apóstrofos em
`l’heure`/`d’água`. Testes cobrem o helper central, reparo direcionado no V3 e
reprovação pela auditoria final `audit_record`; aspas abertas que cruzam eventos
continuam permitidas somente quando contagem e sequência por evento são
preservadas.

Nos quatro módulos direcionados, 132 testes passaram e 1 teste conhecido de
nomes próprios falhou
(`test_audit_does_not_mark_exact_proper_names_as_untranslated_dialogue`). Ao
excluí-lo, 132 passaram e 1 foi deselecionado. Na suíte offline completa: 637
testes, 1 falha conhecida não
relacionada, fallback de glifo visual V2.3.8. `git diff --check` passou. A
revisão independente passou os cinco cenários solicitados (5/5), sem blocker de
delimitadores, e confirmou a limitação CES descrita abaixo. A revisão desta
correção está concluída; r48 foi construída e passou o smoke isolado descrito
no adendo seguinte, mas ainda não foi instalada. Sem job ou model call.

Limitação CES reproduzida pelo reviewer: `Le CES publie ces avis.` →
`O publica CES avis.` pode omitir a sigla e copiar o demonstrativo sem mudar a
contagem de `CES`; legendas totalmente maiúsculas fora dos padrões reconhecidos
também são ambíguas. O detector lexical não prova completude semântica; a
auditoria estrutural e a revisão do conteúdo continuam obrigatórias.

Checagem read-only desta sessão confirmou Transass r39 healthy/V3, sem job
ativo, uma entrada cancelada e nenhuma tarefa pendente; mounts RW de episódios
e state intactos. Ollama está running, sem modelo carregado e com
`restart_count=0`. Nenhuma inferência ocorreu. Manter
`CANONICAL_STATE_RECONCILIATION_REQUIRED` até review final, build/smoke
isolado, instalação somente do Transass e novo preflight read-only E14.

### r48 construído e smoke isolado aprovado — 2026-09-28

Após o review delimitador sem blockers, foi construída offline a imagem
`transass:v2.5.2-local-v3-candidate-only-20260928-r48`, sobre r47, copiando
somente `ass_engine.py` e `translation_quality.py`; digest
`sha256:2c274b3ee1aaa771998476913a5d6647e1a6800c785c522e30b49cd369dcce2c`. O
container descartável (`--network=none`, sem volumes, sem modelo) passou o
smoke determinístico: tradução correta `ces`/Shi Ki aceita; saída `CES` sem
sigla-fonte sinalizada; sequência/contagem de aspas curvas excedentes detectada;
apóstrofos em `l’heure d’água` ignorados. Nenhuma inferência foi executada.

r48 ainda não está instalada. O Transass ativo continua r39 healthy/ocioso; os
mounts RW, fila e estado do Ollama registrados acima permanecem sem alteração.
Próximo gate: atualizar somente o serviço Transass com `--no-deps`, então
confirmar health/version/status/mounts/Ollama e executar preflight read-only
E14. Só uma candidata E14 pode seguir após esses checks, sem fallback ou
Library/publicação; E15+ depende da auditoria estrutural da E14.

### r48 instalada e preflight E14 elegível — 2026-09-28

Depois do smoke, somente o serviço `transass` foi recriado com
`--no-deps --force-recreate --pull never`; Ollama não foi reiniciado. Serviço
healthy, `/version` 2.5.2/V3/r48, `running=false`, `current_job=null`, fila
vazia. Mounts permanecem `/PICAdeiro/data/Shows -> /shows` RW e
`/docker/transass/state -> /app/state` RW. Ollama running, `restart_count=0`,
sem modelo carregado.

O preflight read-only de E14 aprovou 1/1: ID 301, episódio 14 de Shiki, faixa
`FR Full ASS`, stream embutido 2/francês, pipeline V3, Qwen2.5:14b. Nenhuma
model call ocorreu nesse preflight. Está liberada a candidata isolada E14 sob
as instruções explícitas da sessão: `candidate_only=true`, `ollama_only=true`
(runner `--no-fallback`), uma de cada vez; não arquivar nem publicar na Library.
Somente depois de auditoria estrutural aprovada avançar à E15. As capturas e
qualquer artefato candidato ficam em `/app/state`, conforme autorizado.

### Candidata E14 r48 em andamento — 2026-09-28

Foi iniciada uma única candidata pelo app: session
`5a2d643a856d4cb994905e74c2d67deb`, job
`a3cbd31404964b0fa375506e036d4a63`, episode 14/ID 301. O POST confirmou
`queued=1`, pipeline V3, francês, Qwen2.5:14b e `published=false`; a requisição
usou `candidate_only=true` e `ollama_only=true` (o runner adiciona
`--no-fallback`). O status público confirmou `TRANSLATING`, sem outro job
ativo. Telemetria inicial: GPU 58–59°C; contagem de chamadas ainda não foi
exposta pelo status consultado. Não há PASS estrutural, Library write ou
publicação até o momento. Preservar todos os artefatos/capturas em state e
aguardar auditoria antes de enfileirar E15.

### Falha da candidata E14 r48 — validação de delimitadores — 2026-09-28

A única candidata terminou `FAILED` durante a correção de qualidade. O log
registra evento 233 com
`V3_TRANSLATION_QUALITY_RISK:233:ASS_DELIMITER_TOKEN_COUNT_MISMATCH,ASS_DELIMITER_SEQUENCE_MISMATCH:REPAIR_FAILED`.
O run V3 é `125c90fcf8858f847fc77c74`, operação
`e0b98ef52bd4cb54626922997989941a`; a pasta de capturas contém requests
`000001` a `000029`. A sessão continua `5a2d643a856d4cb994905e74c2d67deb`, job
`a3cbd31404964b0fa375506e036d4a63`. Nenhuma saída estruturalmente aprovada foi
produzida; `candidate_only` manteve a execução fora da Library e a resposta do
app indica `published=false`.

Preservar integralmente as capturas e qualquer diagnóstico em `/app/state`.
Não iniciar E14 novamente nem avançar E15 até inspecionar fonte, primeira
resposta, resposta de reparo e regra por evento 233; corrigir e testar offline,
revisar de forma independente, reconstruir/smoke, instalar somente Transass e
repetir preflight. Ollama/modelo não foram reiniciados. Manter
`CANONICAL_STATE_RECONCILIATION_REQUIRED`; sem nova chamada, fallback, Library
ou publicação durante a investigação.

### Reconciliação das capturas E14 r48 e fotografia de armazenamento — 2026-09-28

Correção aditiva ao registro anterior: a pasta do run contém 31 capturas, não
29 (`000001`–`000031`). As capturas 1–30 são lotes de tradução; a 31 é o reparo
de qualidade. O lote de tradução abrangendo eventos 232–239 gerou em 233–235
aspas ASCII de fechamento que não existem na fonte: `"Libres ?`,
`"Et où irais-tu,\nsi tu l'étais ?` e `"Les villageois\ncomptent sur nous.`.
O reparo direcionado repetiu as mesmas saídas, e V3 bloqueou corretamente por
divergência de contagem/sequência. Nenhuma candidata válida foi criada. As
capturas originais permanecem preservadas e imutáveis.

Medição somente leitura do state local: 923 MB no total — `v238-runs` 414 MB,
`failure-ledger` 339 MB, `anime-subtitle-library` 144 MB e `v3-runs` 26 MB
(40 execuções). Capturas da E14 r48 ocupam cerca de 748 KB. Portanto, o grosso
do state medido vem de execuções V2.3.8 e evidência histórica, não das
candidatas V3 recentes. Sem limpeza ou remoção de evidências.

O relatório global `docker system df` mostrou imagens 33.67 GB, containers
362 MB, volumes locais 973.5 MB e build cache 4.195 GB; estes números abrangem
o Docker local inteiro, não só Transass. Há 49 tags locais V3 `r*`; a r48 mostra
333 MB de tamanho virtual, dos quais 332.6 MB compartilhados e 117 KB exclusivos
(r47: 0 B exclusivos). Não somar o tamanho virtual de cada tag como espaço
consumido nem executar prune sem inventário/decisão específica. Espaço de disco
do host na medição: 78 GB usados de 110 GB, 27 GB disponíveis.

Fotografia do worktree em `main`/HEAD `5b541ca`: 48 arquivos versionados
alterados (+8,126/−515) e 19,543 linhas de Python em testes no conjunto
versionado. Esse diff é cumulativo e antecede esta reconciliação; não representa
somente o trabalho das revisões r40–r48 nem pode ser atribuído integralmente a
elas. Nenhum arquivo, imagem, cache ou captura foi removido.
