# Reconciliação operacional — Thermal Guard NVIDIA / Shiki E09

**Data:** 2026-09-27 (America/Recife)  
**Escopo autorizado:** alinhar a imagem do Docker local à imagem ativa, habilitar
telemetria NVIDIA via capacidade `utility`, recriar somente `transass` mantendo
os mounts e traduzir somente Shiki S01E09.

## Evidência anterior à atualização

- O repositório canônico está em `main`, HEAD `5b541ca`, com alterações locais
  preexistentes. Elas foram preservadas; esta atualização não faz commit, push
  nem reescrita de histórico.
- O container ativo usa
  `transass:v2.5.2-local-v3-thermal-20260927-r2`
  (`sha256:732132ba144077b8728d97e5d811a3e6724cbe1575fab40e67814c60ce12f640`).
  Os hashes de `app.py`, `web_retranslation_runner.py`, `pipeline_v3.py` e
  `ass_engine.py` dentro dele coincidem com os arquivos correspondentes do
  worktree.
- O `/docker/transass/.env` tinha uma única seleção de imagem, apontando para
  `transass:v2.5.2-local-v3-unified`. Essa imagem é anterior e não coincide com
  a imagem ativa. O usuário confirmou usar a imagem ativa `r2`; o alinhamento
  será limitado à variável `TRANSASS_IMAGE`, preservando as demais configurações.
- Um container descartável, sem rede, volumes ou geração, recebeu apenas a
  capacidade NVIDIA `utility` e leu `NVIDIA GeForce RTX 3060`, 54 °C, fan 0 RPM
  e 16,06 W. Não foi feita inferência. A API do Transass, antes do override,
  ainda enxergava somente o sensor de CPU.
- O Compose padrão resolve os mounts existentes como
  `/PICAdeiro/data/Shows:/shows` e `/docker/transass/state:/app/state`, ambos
  RW. O override NVIDIA não substitui nem altera esses mounts.
- O E09 corresponde ao episódio Library ID `296`, arquivo
  `Shiki - S01E09 - Ninth Coffin Bluray-1080p.mkv`. O preflight read-only
  aprovou exatamente um episódio (`blocked=0`, `skipped=0`), fonte francesa,
  faixa ASS textual interna 2 (`FR Full ASS`). Já existe sidecar PT-BR, sem
  registro de versão na Library; seu hash inicial é
  `b4cb02f075cce3721a5aec841fa5bb3b1f0c4f1118d7bbc99d8c4d34f3fa2b8d`.
- Como já há sidecar, a operação autorizada é uma retradução isolada do E09,
  sem sobrescrever o sidecar e sem promovê-lo/publicá-lo automaticamente. O
  estado histórico de Shiki E08 permanece no relatório
  `2026-09-27-shiki-e08-v3-canary.md` e não será editado.

## Resolução autorizada e gates

O usuário confirmou alinhar `TRANSASS_IMAGE` à imagem ativa `r2`, aplicar o
override NVIDIA e recriar o container; autorizou em seguida traduzir o E09.
Antes da chamada de modelo, o container deverá estar saudável e ocioso, o
provedor efetivo do Thermal Guard deverá ser NVIDIA, a temperatura deve estar
abaixo do alerta, e os dois mounts devem continuar idênticos. Se qualquer gate
falhar, a tradução não será enfileirada.

O resultado e a telemetria do job serão registrados aditivamente abaixo após
a execução; nenhum resultado é presumido neste registro.

## Gate aprovado após atualização do container

- `/docker/transass/.env` foi alinhado para a imagem ativa `r2`; todas as
  demais variáveis foram preservadas. O Compose validado seleciona a mesma
  imagem, `NVIDIA_DRIVER_CAPABILITIES=utility` e mantém os dois mounts RW
  exatamente nos mesmos caminhos.
- Foi recriado somente o serviço `transass` (`--no-deps --force-recreate`),
  sem `down`, remoção ou migração de volumes. O container está saudável, informa
  pipeline `v3`, imagem `r2`, Ollama `qwen2.5:14b` e nenhum fallback.
- Os registros Library `114`, `115` e `116` do trabalho anterior continuam
  legíveis após a recriação. A fila permanece vazia antes do novo job.
- Dentro do container, `ThermalGuardFactory` selecionou
  `NvidiaThermalProvider` via `nvidia-smi`: GPU `gpu0` a 54 °C; warning 90 °C,
  parada efetiva 100 °C, retomada 85 °C, polling 1 s e janela configurada de
  override 60 s. Esta leitura é da GPU, não do sensor CPU fallback.
- O preflight read-only repetido após a recriação aprovou somente o E09
  (`eligible=1`, `blocked=0`, `skipped=0`), pipeline `v3`, fonte francesa na
  faixa ASS textual 2. O sidecar PT-BR mantém o hash inicial registrado acima.
- O gate térmico, a elegibilidade, a identidade do runtime e a preservação dos
  mounts/state foram confirmados. A chamada de modelo para E09 ainda não foi
  iniciada neste checkpoint.

## Modo autorizado para E09

O E09 já tem um sidecar PT-BR, logo a fila normal o rejeita para impedir
sobrescrita. Será usado o endpoint de retradução para o único episódio 296, com
fonte francesa e Ollama exclusivo. O fluxo padrão arquiva uma versão gerada na
Library com `preferred=false`; publicação é separada e usa
`allow_replace=False`. Como o sidecar de destino já existe, qualquer conflito
mantém o sidecar original intacto e a nova versão não é publicada. Não haverá
promoção, substituição do PT-BR existente nem batch de outros episódios.

## Resultado da única tentativa autorizada

- Job `d3158638fd004ec682fd1b2f899736a1` terminou como **FAILED** durante a
  tradução V3. Foram feitas 14 chamadas HTTP 200 ao Ollama; as 14 respostas
  foram capturadas como `RESPONSE_DURABLE`, sem retries. A execução parou com
  `V3_TRANSLATION_SOURCE_COPY:109`. Não houve registro de legenda traduzida na
  Library nem publicação.
- A reprodução offline da validação identificou o unit 109 da fonte como
  `- Hein ?\n- Kyôko.` e a saída do modelo como `- Hein?\n- Kyôko.`. A regra
  `_is_untranslated_source_copy` normaliza pontuação e considera o conteúdo
  uma cópia. Neste caso, “Hein?” é uma interjeição compartilhada válida e
  “Kyôko” é nome próprio: a evidência aponta para falso positivo do detector,
  não para uma tradução efetivamente idêntica. Isso não valida a qualidade
  linguística dos demais units.
- A preparação da retradução criou os registros Library 117 (fonte francesa)
  e 118 (PT-BR importado já existente). Ambos são evidência e devem ser
  preservados. O sidecar PT-BR não foi alterado: seu hash continua
  `b4cb02f075cce3721a5aec841fa5bb3b1f0c4f1118d7bbc99d8c4d34f3fa2b8d`.
- Uma amostragem externa read-only da GPU durante o job registrou, em sete
  pontos, temperaturas `81, 80, 80, 80, 68, 63, 60 °C`; ventoinha em 100%
  nas cinco primeiras leituras e 89% na última; potência de 134,01 W até
  40,59 W. Não houve alerta de 90 °C nem trip térmico nesta amostra.
- A retranslation child process não conecta o callback `on_sample` do Thermal
  Guard. Portanto o guard faz polling/decisão, mas este fluxo não expõe nem
  persiste a telemetria de temperatura por segundo no log do job. É uma lacuna
  offline confirmada por inspeção do código, separada da causa da falha.

## Estado e limite de autorização

A tentativa autorizada foi consumida. O estado de runtime demonstrou um
comportamento material não documentado pelo código: falso positivo em
`V3_TRANSLATION_SOURCE_COPY:109`, além da lacuna de telemetria do subprocesso.
Operacionalmente, marcar
`CANONICAL_STATE_RECONCILIATION_REQUIRED`: preservar as 14 capturas e os
registros 117/118, e não iniciar outra chamada de modelo, batch, publicação ou
escrita operacional até reconciliar a evidência e obter nova autorização.

Próxima etapa segura: corrigir o detector com um teste offline determinístico
para este caso e conectar a amostragem do Thermal Guard no fluxo de
retranslation, evitando escrita durável a cada segundo. Isso requer mudança em
`main`, reconstrução/recriação local e uma nova tentativa de modelo, portanto
fica pendente de autorização explícita. Nenhuma dessas ações foi feita neste
checkpoint.

## Segunda tentativa autorizada: resultado e evidências

O usuário autorizou a correção offline, a telemetria, a reconstrução local e
uma única nova tentativa do E09. A correção do falso positivo `hein` e a
telemetria foram implementadas, testadas offline e incluídas na imagem
`transass:v2.5.2-local-v3-thermal-20260927-r3`. O serviço foi recriado sem
alterar os mounts de mídia e state; permaneceu saudável, com pipeline V3 e
Ollama exclusivo. O preflight selecionou somente o episódio 296/E09.

- A única tentativa autorizada criou o job
  `ddc7c600495c4d0383f39a3e55beb501` e terminou **FAILED** na validação
  estrutural. Houve 40 chamadas ao Ollama e 40 respostas capturadas como
  concluídas/duráveis, sem retries. Não foi gerado novo registro de saída nem
  houve publicação. A fila ficou sem job em execução.
- O processo filho falhou com `LINE_BREAK_INSIDE_WORD` nos eventos 36 e 144.
  O pai apresentou a mensagem resumida
  `retradução terminou sem resultado válido (código 1)`.
- No evento 36, a fonte francesa contém `mais est morte le 15\Nen dépérissant
  peu à peu.` e a resposta foi `mas morreu no dia 15, morrendo lentamente.`.
  A reprodução offline de `replace_source_payload` reconstrói o texto como
  `mas morreu  no dia\N15,  morrendo  lentamente.`, deslocando a quebra para
  dentro da sequência traduzida e produzindo o erro estrutural.
- No evento 144, a fonte é `Mme Yasumori\Nest hospitalisée` e a resposta
  `Sra. Yasumori\Nest hospitalizada`. Além da quebra reconstituída entre o
  nome e a palavra curta `est`, a resposta conserva `est` em francês. A
  detecção atual de resíduo não sinalizou esse caso por causa do limiar de
  sobreposição lexical. Relaxar apenas a regra de quebra poderia deixar passar
  uma tradução incompleta; a correção deve tratar a reconstrução de quebras e
  a detecção de resíduo sem afrouxar a validação estrutural.
- As 40 capturas permanecem no state, em
  `/app/state/v3-runs/446b8d875600dc4733927e21/3fb05666a20ba45f44d0305cdd93edea/captures`.
  Os registros Library 117 (fonte francesa) e 118 (legenda PT-BR anterior)
  foram preservados. O hash do sidecar PT-BR continua
  `b4cb02f075cce3721a5aec841fa5bb3b1f0c4f1118d7bbc99d8c4d34f3fa2b8d`; não
  existe registro novo de saída.
- A integração da telemetria funcionou: o job transmitiu 301 amostras de um
  segundo entre 16:34:24 e 16:39:35 UTC, com leituras entre 54 °C e 83 °C. Não
  houve warning nem trip do Thermal Guard. O provider integrado não fornece
  RPM da ventoinha nem potência, então esses campos aparecem indisponíveis na
  amostra do app. Leituras externas pontuais via `nvidia-smi` indicaram GPU a
  80 °C, ventoinha a 100% e 131,08 W; depois 80 °C, 99% e 128,66 W. Após o
  job: 48 °C, ventoinha a 30%, 15,90 W e utilização 0%. Os percentuais são a
  leitura `fan.speed`, não RPM.

O conjunto offline focado passou antes da chamada: `140 passed, 9 subtests
passed`; `git diff --check` passou. A execução completa da suíte não foi feita.
O container local continua na imagem r3 saudável. A autorização para a
chamada única foi consumida; não houve terceira chamada, nova correção,
rebuild ou alteração da Library. Para continuar, é necessária nova autorização
para corrigir os dois problemas evidenciados, adicionar fixtures offline e,
somente depois dos gates, reconstruir e tentar o E09 novamente.

## Reconciliação após autorização e correção offline

O usuário autorizou corrigir o detector e a telemetria, reconstruir o Docker
local e fazer uma única nova tentativa do E09. As frases acima registram o
estado anterior à nova autorização e são preservadas como histórico.

- A correção do detector é estreita: reconhece a interjeição compartilhada
  francês/PT-BR `hein` somente quando os demais tokens são nomes próprios
  capitalizados e não contêm indicadores franceses. Cópias francesas claras,
  como `Je viens.` e frases longas, continuam bloqueadas. O teste reproduz o
  unit `- Hein ?\n- Kyôko.` → `- Hein?\n- Kyôko.` tanto no helper como no
  caminho V3 offline.
- O filho V3 agora emite `V3_THERMAL_SAMPLE` como JSON de linha única. O pai
  valida o marcador/payload, atualiza `job.thermal_guard` e `state.thermal_guard`,
  registra nível `thermal` e notifica SSE, sem chamar `_persist_locked` por
  amostra. Eventos trip são reconhecidos por prefixo reservado e continuam no
  fluxo durável normal. Na interface, a leitura térmica de rotina mantém o
  controle de exibição opt-in na aba de Diagnósticos.
- Revisões independentes somente leitura confirmaram a causa do falso positivo
  e recomendaram o canal em memória/SSE para telemetria; nenhum auditor alterou
  código.
- Verificação offline: `140 passed, 9 subtests passed` nos cinco módulos
  relevantes (`pipeline_v3`, retradução, segurança da app, Thermal Guard e
  transporte); `git diff --check` passou. Não houve chamada de modelo nesta
  etapa.

A reconciliação documental e os gates offline estão concluídos. Antes da única
nova chamada autorizada, ainda é obrigatório reconstruir/recriar somente o
serviço local, confirmar imagem/health, mounts RW idênticos, pipeline V3,
Ollama exclusivo, GPU NVIDIA detectada e fila/preflight elegíveis para somente
o E09. Se algum gate falhar, não iniciar o modelo.

## Correção dos eventos 36/144 e gates da candidata r4

Após nova autorização explícita, foram corrigidos os dois defeitos sem retirar
as validações estruturais nem permitir que `est` passe como português:

- Ao restaurar quebras ASS omitidas pelo modelo, um número único que encerra o
  segmento original é usado como âncora quando também aparece na tradução. No
  evento 36, isso mantém `15,` na primeira linha. Espaços horizontais extras
  criados por slots de origem sem palavra traduzida são reduzidos, preservando
  espaçamento duplo que já existia na origem e sem normalizar dentro de tags.
- O validador reconhece números completos como tokens curtos independentes,
  mas continua rejeitando uma quebra entre dígitos adjacentes (`1\N5`) e
  palavras partidas (`vi\Nda`).
- A detecção de resíduo agora analisa o texto visível, sem confundir o `N` de
  `\N` com conteúdo lexical. Para o marcador francês inequívoco `est`, o
  limiar conservador de sobreposição é 0,50 quando o nome próprio foi
  preservado; `está` continua aceito como português. O teste de integração
  confirma bloqueio antes de gravar saída quando a resposta mantém `est`.

Os testes relevantes passaram: `181 passed, 9 subtests passed` em oito módulos
offline, incluindo parser/validador ASS, pipeline V3, resíduo francês,
telemetria e retradução. `git diff --check` passou. Na varredura adicional de
`test_v238_canonical_enforcement.py`, um teste V238 legado falhou esperando
`VISUAL_GLYPH_BASE_FALLBACK`, mas recebeu `VISUAL_GLYPH`. Esse teste usa o
reconstrutor V238 `rc4_replace_source_payload`, não o `ass_structure` alterado
nesta correção; o achado permanece registrado e não foi expandido para fora do
escopo.

A candidata foi construída como
`transass:v2.5.2-local-v3-thermal-20260927-r4` (image ID
`sha256:c7bd8c00113e6c4a343ca23e65f8ae58c9d79b1da25135a494d8395edd9cccc6`) a
partir de `5b541ca-dirty`. O serviço local está saudável e usa exatamente os
mesmos mounts RW: `/PICAdeiro/data/Shows -> /shows` e
`/docker/transass/state -> /app/state`. Hashes de `ass_structure.py`,
`ass_engine.py` e `pipeline_v3.py` dentro do container coincidem com o
worktree. O status confirma pipeline `v3`, modelo `qwen2.5:14b`, fila ociosa;
o transporte é Ollama sem fallback. `ThermalGuardFactory` detectou
`NvidiaThermalProvider`, leitura inicial 47 °C.

O preflight read-only selecionou somente o E09 (episódio 296), fonte francesa
na faixa ASS textual interna 2, registro PT-BR anterior 118; `eligible=1`,
`blocked=0`, `skipped=0`. Nenhuma chamada de modelo da próxima tentativa havia
sido feita quando estes gates foram registrados. A tentativa autorizada será
uma única execução `candidate_only` e `ollama_only`, sem arquivar ou publicar
uma nova versão na Library.

## Tentativa r4 ainda não iniciada

O POST para enfileirar o E09 foi recusado pelo gate de risco da ferramenta,
que não reconheceu autorização explícita atual para iniciar uma chamada real
ao modelo e gravar state/capturas. Não houve nova rota ou método alternativo.
Uma consulta GET posterior confirmou `running=false`, `current_job=null`,
`jobs=[]` e `session_id=null`; portanto, nenhuma nova chamada nem gravação de
captura ocorreu após a r4. O serviço continua saudável na r4, e o preflight
read-only continua válido. É necessária autorização direta e específica na
sessão corrente para executar uma única tradução candidata do E09 com Ollama
`qwen2.5:14b`, sem fallback e sem Library/publicação.

## Adendo — resultado da tentativa candidata r4 autorizada

Este adendo preserva o registro anterior, que era correto antes da autorização
seguinte, e atualiza o estado após a execução autorizada pelo usuário em
2026-09-27. Foi enfileirado somente o Shiki E09 (episódio 296), em modo
`candidate_only`, pipeline V3, Ollama `qwen2.5:14b`, sem fallback de provedor.
O job `843f466bdce24c21a5cb4fc88be97330`, na sessão
`433be9130afc4e7dbc45f1aef5322682`, iniciou às 17:30:10 UTC e terminou às
17:34:52 UTC. O runtime registrou 19 chamadas semânticas e zero retries.

O job falhou com `V3_TRANSLATION_SOURCE_RESIDUE:144`; não foi criado candidato
válido. A captura V3 da chamada 19 está em
`/app/state/v3-runs/ce5fef74979f9262f66586a3/f5c81c49b1d47b312045e5fa8d64dfea/captures/v3-retranslate-ce5fef74979f9262f66586a3-000019/`, com
`request_payload.json` e `parsed_response.json`. Portanto, corrige-se aqui a
observação operacional anterior de que não havia resposta bruta capturada: o
`failure-ledger/web-jobs/<job-id>/staging` estava vazio, mas a captura durável
V3 fica em uma árvore separada.

Análise offline da captura, conferida com a fonte francesa já existente
(registro 117; SHA-256
`aca82c55ca0b33df70416c9cd7ed6a08b47d124c1f5a5dd0a8e4521363945bf4`): o AST
tem 314 eventos e a unidade de índice 144, estilo `Default`, cobre
00:09:58.590–00:10:00.420. A fonte visível é `Mme Yasumori\N` seguida de
`est hospitalisée`; a resposta capturada foi `Sra. Yasumori\N` seguida de
`est hospitalizada`. O modelo traduziu o tratamento e o particípio, mas deixou
o verbo francês `est` sem traduzir (em português, neste contexto, “está”).

O detector aplicado pelo V3 age como projetado: os tokens relevantes dão
overlap de 2/4 (`Yasumori` e `est`), e `est` é um marcador francês forte; o
limiar específico de 0,50 o identifica sem tratar `está` como resíduo. O teste
offline existente
`test_pipeline_v3_rejects_french_copula_residue_with_preserved_proper_name`
reproduz esse par fonte/resposta e passou (`1 passed`). A evidência não justifica
relaxar a validação; a falha é de tradução gerada pelo modelo, não um falso
positivo demonstrado do guard.

Após a falha, a Library permaneceu sem alteração: seguem apenas os registros
117 (fonte francesa) e 118 (PT-BR anterior), sem novo registro candidato e sem
publicação. A telemetria observada chegou a 81 °C, abaixo do alerta de 90 °C;
não houve trip térmico. Uma leitura NVIDIA excedeu o timeout de 3 s durante a
execução e depois voltou a responder.

Nenhum código foi alterado nem nova chamada ao modelo foi feita durante esta
análise. A causa imediata está identificada; eventual ajuste de prompt ou nova
tentativa exige uma autorização operacional própria. O estado da execução r4
agora está reconciliado com este relatório; permanecem as demais alterações
locais preexistentes no worktree, sem commit ou push.

## Adendo — orientação preventiva V3 (somente worktree)

Na continuação autorizada, foi adicionada em `src/subtranslate/pipeline_v3.py`
uma orientação condicional para entradas francesas: traduzir também verbos e
auxiliares curtos, com exemplo explícito de `est hospitalisée` →
`está hospitalizada` quando o destino for pt-BR. A orientação aparece tanto no
prompt de lote quanto na chamada unitária interna para itens ausentes. Usa os
aliases franceses localmente e não acrescenta uma nova dependência da pipeline
legada.

O detector de resíduo e a rejeição fail-closed não foram afrouxados; não houve
mudança de transporte, fallback de provedor ou número de retries. A suíte offline
V3 + resíduo francês passou (`41 passed`), `git diff --check` passou, e uma
revisão independente não encontrou bloqueios. Nenhuma chamada real ao modelo
foi feita.

A mudança existe apenas no worktree `main`, ainda sujo e sem commit/push. A
imagem/container Docker r4 não foi reconstruída nem recriada, portanto ainda não
contém esta orientação. Uma validação operacional dessa versão requer autorização
separada para atualizar o Docker; uma nova chamada de tradução também requer
autorização própria.

## Adendo — tentativa candidata r5 e auditoria estrutural/conteúdo

Após autorização explícita, a imagem foi reconstruída como
`transass:v2.5.2-local-v3-thermal-20260927-r5` (image ID
`sha256:ce6d9e8f3314538cdb967c176a032784d954863e3501e62188caa9e7f0d02aed`, revisão
`5b541ca-dirty`). Somente o serviço `transass` foi recriado; os mounts RW
`/PICAdeiro/data/Shows -> /shows` e `/docker/transass/state -> /app/state`
permaneceram iguais. A imagem contém a orientação condicional francesa do V3 e
foi verificada localmente sem rede antes da execução.

Foi enfileirada exatamente uma retradução candidata do episódio 296 / Shiki E09,
fonte francesa, pipeline V3, Ollama `qwen2.5:14b`, sem fallback. Job
`1a3c60f1ea134029aa737eff3a69dc23`, sessão
`ec8ae19fa6574e9e93789eb7e6e74b6c`, de 18:31:51 a 18:36:50 UTC. Terminou como
`COMPLETED` / `CANDIDATE_READY`, com 40 chamadas semânticas, 40 lotes concluídos,
zero retries, zero falhas e sem interrupção térmica. O artefato candidato é
`Shiki - S01E09 - Ninth Coffin Bluray-1080p.mkv.pt-BR.ass`, SHA-256
`07a4ce849f3dcd3e5cbe4d71cb9d2161ca1348bef4c9f18b4f07798831e36dca`, sob
`/app/state/staging/retranslation-1a3c60f1ea134029aa737eff3a69dc23/`.

A validação independente carregou os registros 117 (fonte francesa, SHA-256
`aca82c55ca0b33df70416c9cd7ed6a08b47d124c1f5a5dd0a8e4521363945bf4`) e a
candidata no `ASSDocumentAST` do container e executou
`validate_document_structure`. Resultado: **PASS estrutural** — 314 eventos em
ambos os arquivos, todos `Dialogue`; nenhum campo estrutural divergente
(tipo/layer/tempos/style/name/margens/effect), nenhuma alteração de tags,
quebras ASS ou espaços rígidos; cabeçalho `[Script Info]` igual, dois estilos
iguais, zero evento vazio e zero issue do validador. Quatro textos visíveis
permanecem iguais: `SHI KI`, `Yasuyo.` (duas ocorrências) e `Seishin...`, nomes/
título que não indicam por si só falha de tradução.

Isso **não** equivale a aprovação linguística. A leitura dos 314 pares encontrou
erros claros, entre eles (índices AST iniciados em zero): evento 17 `uma balde`
(concordância); 48 `um parada cardíaca`; 50 `os vítimas`; 81 `devolver a guarda`
para `rester de garde` (ficar de plantão); 106 muda uma ordem dirigida à outra
pessoa para primeira pessoa (`Avalio ... e vou embora`); 113 transforma `Honte
à toi` em `Você tem vergonha de mim`; 152 fica agramatical (`É com a sua mulher
de se ocupar disso`); 190 traduz o nome `Nao` como `Não`; 255 traduz `panne de
courant` (falta de energia) como `blefe de energia`; 258 troca a capacidade de
enxergar no escuro por `notívagos`; 269 também interpreta `Nao` como negação; e
288 traduz `Belle-maman` como `Cunhada`, em contradição com o tratamento da mesma
personagem em outros eventos. Há ainda escolhas pouco naturais para pt-BR e
problemas menores de registro/acentuação. Assim, o resultado automático
`SEM PROBLEMAS DETECTADOS` não detectou qualidade semântica/gramatical: ele não
substitui revisão linguística.

A candidata continua isolada, não foi editada, arquivada nem publicada. Uma
consulta GET confirmou 118 registros na Library; os registros 117 e 118 mantêm
os hashes anteriores, e o SHA da candidata não aparece na Library. Portanto a
execução técnica concluiu, mas a candidata **falha na revisão de conteúdo** e não
deve ser promovida. Não houve segunda chamada ao modelo ou correção manual, em
respeito à autorização limitada a uma tentativa. O container está executando a
imagem r5; `/docker/transass/.env` permaneceu sem edição e ainda referencia r4,
então um futuro `compose up` sem override pode selecionar r4. Não houve commit,
push ou escrita em produção/Library.
