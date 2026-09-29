# Reconciliação operacional — Shiki E08 / V3

**Data da observação:** 2026-09-27 (America/Recife)  
**Escopo:** leitura do runtime Docker local e preparação de canário; nenhum job,
chamada de modelo ou escrita na Library foi executado antes deste registro.

## Evidência observada

- O container `transass` estava saudável e ativo havia aproximadamente 9 horas.
- A API `/version` informou versão `2.5.2`, pipeline `v2_3_8` e imagem
  `transass:v2.5.2-local-v3-unified@5b541ca`. `/pipeline-config` também informou
  `v2_3_8`; portanto, o runtime não refletia o V3 padrão descrito no estado
  ativo do repositório.
- O container montava `/PICAdeiro/data/Shows` em `/shows` e
  `/docker/transass/state` em `/app/state`. Ambos são mounts bind RW e devem ser
  preservados durante a atualização autorizada.
- O estado da fila estava ocioso. Havia um job falho persistido para Shiki
  S01E06, ID `7fc585f1a7e44c5abc6b0046a5c9e861`, com razão `worker_exception` e
  erro `name 'retain_staging' is not defined`. O estado persistido não foi
  apagado nem alterado. A árvore de trabalho atual contém importação e tratamento
  defensivo de `retain_staging`; esta correção ainda não estava carregada no
  container observado.
- Shiki está cadastrado como série ANIME ID `9`. S01E08 é o episódio ID `295`,
  arquivo `Shiki - S01E08 - Eighth Night Bluray-1080p.mkv`, sob
  `Shiki/Season 1/`.
- O endpoint de episódios marcou E08 como `ALREADY_TRANSLATED`, apontando para o
  sidecar `Shiki - S01E08 - Eighth Night Bluray-1080p.pt-BR.ass`. A Library não
  retornou registros de legenda para esse episódio. A fonte textual detectada é
  a track ASS francesa interna 2, título `FR Full ASS`; a fonte está disponível
  e a retradução aparece disponível no runtime consultado.
- A documentação histórica E07–E12 localizada no repositório identifica a
  sequência antiga de Zombieland S01; não foi encontrada nela uma decisão
  específica que bloqueie Shiki S01E08.
- A configuração Compose resolvida com `/docker/transass/.env` seleciona a
  imagem `transass:v2.5.2-local-v3-unified`, pipeline `v3`, modelo Ollama
  `qwen2.5:14b`, idioma fonte `francês` e batch `8`. Os dois mounts resolvidos
  coincidem exatamente com os mounts do container atual. O Compose não declara
  `build:`; a imagem será construída explicitamente pelo `deploy/Dockerfile`
  antes de recriar somente o serviço `transass`.

## Decisão e autorização

Em 2026-09-27, o usuário autorizou reconstruir/recriar somente o container local
`transass`, preservar os mounts existentes e iniciar uma retradução candidata de
Shiki S01E08 a partir da track francesa, arquivando a candidata na Library sem
substituir nem publicar sobre o sidecar PT-BR já existente.

Na implementação atual, a opção de API `candidate_only=true` significa manter
um artefato temporário fora da Library. Para cumprir a autorização de arquivar
uma nova versão com lineage, será usado o fluxo de retradução arquivado; como o
sidecar PT-BR já existe, a publicação deve permanecer bloqueada e a versão nova
fica somente na Library. O fluxo também registra a fonte e importa o sidecar
anterior para que o lineage seja explícito.

Esta reconciliação é aditiva: preserva a falha histórica e distingue o runtime
V2.3.8 observado do código V3 no repositório. A autorização não inclui promover
a candidata, publicar/sobrescrever o sidecar, apagar evidência, alterar outros
containers, fazer push ou modificar volumes.

## Próximas ações dentro do escopo aprovado

1. Construir a imagem a partir da árvore de trabalho canônica atual (`main`,
   HEAD `5b541ca`), sem commit ou push.
2. Recriar somente o serviço `transass` usando `deploy/compose.yaml` e os mesmos
   mounts; confirmar a versão V3 e a preservação do estado.
3. Executar novamente o preflight read-only para E08.
4. Se o preflight aprovar a fonte e a identidade, iniciar somente a candidata
   autorizada. Parar se houver divergência, ambiguidade ou qualquer gate novo.

Nenhum resultado de tradução ou teste de modelo é afirmado por este relatório.

## Resultado da atualização do container

- A imagem foi construída como `transass:v2.5.2-local-v3-unified`, com digest
  `sha256:6af2e57764e5a93c869f4ac648b9172ff72925f2f4069c498a7da23ca0af9a89`.
  A proveniência embutida identifica o worktree como
  `5b541ca-dirty-worktree-20260927` e a imagem como
  `transass:2.5.2-local-v3-canary@5b541ca-dirty-worktree-20260927`.
- Foi recriado somente o serviço `transass`, com `--no-deps --force-recreate`;
  nenhum `down`, remoção de volume ou ação em outros serviços foi executado.
- Após a recriação, `/health` respondeu `ok`, `/version` e `/pipeline-config`
  informaram `v3`, e os módulos `pipeline_v3` e `v3_runtime` importaram.
  Configuração operacional reportada: Ollama `qwen2.5:14b`, sem fallback
  configurado, idioma `francês`, batch `8`.
- Os mounts continuam iguais: `/PICAdeiro/data/Shows:/shows` e
  `/docker/transass/state:/app/state`.
- O `/status` reiniciado mostra fila vazia. A evidência do job E06, porém,
  continua preservada: o ID está em `/app/state/jobs.json` e nos arquivos
  `failure-ledger/jobs/7fc585f1a7e44c5abc6b0046a5c9e861/manifest.json` e
  `snapshot.json`. A fila em memória não foi tratada como substituta do ledger.
- Antes de qualquer tradução, o sidecar E08 existe e seu SHA-256 é
  `ca710207f638066bfa2c4800088d16d8fd72d64a7da2d882d44bf2bd7b0f452f`.

Até este ponto não houve chamada de modelo nem nova escrita na Library.

## Resultado da primeira tentativa e reconciliação operacional

- O preflight repetido após a reconstrução aprovou exatamente um episódio:
  Shiki E08, fonte `francês`, faixa ASS textual interna 2 (`FR Full ASS`),
  pipeline `v3`, modelo `qwen2.5:14b`; `blocked=0` e
  `skipped_current_validated=0`. O endpoint de tags do Ollama confirmou o
  modelo disponível.
- A tentativa autorizada criou o job
  `2b939cd0073a440ca871af91eb4c5b54` na sessão
  `61dac86b2db7468eb898670da0fcedad`. O job terminou em aproximadamente 3,4 s
  como `FAILED`, razão `retranslation_failed`; não foi criada candidata.
- O traceback do runner apontou `UnboundLocalError` em
  `web_retranslation_runner.py`, na geração de `operation_id`: `hashlib` era
  importado apenas dentro do ramo V2.3.8. Esse import local faz o Python tratar
  `hashlib` como variável local em `_run_pipeline`, deixando-a não inicializada
  quando o ramo V3 tenta usá-la.
- O job registrou `calls=0` e `semantic_calls=0`; portanto, nenhuma chamada ao
  modelo foi feita. O runner falhou antes de criar a árvore de captura V3. O
  job reportou `ledger_available=false`; não foi encontrado diretório de
  failure ledger para esse ID.
- O fluxo arquivou, antes de iniciar o worker, a fonte francesa como registro
  Library `114` (`fre`, `EXTRACTED`, SHA-256
  `27812c7f8b7dbc9d42dc6861fd9674b25ef6840c42657868a2715e3ad2403dd3`) e
  importou a legenda PT-BR já existente como registro `115`
  (`IMPORTED_EXISTING`). Não foi criada nova versão traduzida. O sidecar no
  disco permanece com o SHA-256 original
  `ca710207f638066bfa2c4800088d16d8fd72d64a7da2d882d44bf2bd7b0f452f`.
- Essa falha de execução não constava na documentação ativa antes do teste.
  Fica registrada aditivamente como
  `CANONICAL_STATE_RECONCILIATION_REQUIRED`: não repetir chamadas de modelo nem
  iniciar novo batch até corrigir e testar o runner offline, atualizar o
  runtime local e obter autorização explícita para uma nova tentativa.

## Correção local e testes offline

- O import de `hashlib` foi movido para o escopo do módulo do runner, removendo
  o sombreamento local que impedia o ramo V3 de calcular sua identidade.
- Foi adicionado um teste de regressão que executa o ramo V3 com transporte e
  pipeline simulados, verifica a identidade estável e garante que esse caminho
  não depende do ramo V2.3.8 nem faz chamada de rede/modelo.
- A suíte focal de retradução/V3/integridade passou: `70 passed, 2 subtests
  passed`; `git diff --check` também passou.
- Naquele ponto, a imagem/container ainda precisava ser reconstruída com essa
  correção. Nenhum segundo job foi criado; a única tentativa continua sem
  chamada ao modelo e o sidecar publicado continua com o hash original acima.
  Após atualizar e verificar o runtime, uma nova execução real permanece
  condicionada à autorização explícita do usuário.

## Runtime local após a correção

- A imagem foi reconstruída como `transass:v2.5.2-local-v3-unified`, build
  `2.5.2-local-v3-hashlib-fix`, proveniência
  `5b541ca-dirty-hashlib-fix-20260927`, digest
  `sha256:dd57ed0936ccc57c656abb7966e7c97c2ac34226c97d45781ceff0d42314d9c8`.
- Foi recriado somente o serviço local `transass`. `/health` respondeu `ok`,
  `/version` e `/pipeline-config` identificam pipeline `v3`, e importar o
  `web_retranslation_runner` no container confirma o módulo `hashlib` no escopo
  correto.
- Os mounts RW foram conferidos após a recriação e permanecem exatamente
  `/PICAdeiro/data/Shows:/shows` e `/docker/transass/state:/app/state`.
- O job falho `2b939cd0073a440ca871af91eb4c5b54` segue persistido em
  `/app/state/jobs.json` como `FAILED`; não há diretório de failure ledger para
  ele. A fila ativa está vazia. Os registros Library `114` e `115` continuam
  presentes, e o hash do sidecar no disco continua idêntico ao original.
- O preflight somente leitura foi repetido no runtime corrigido: ainda aprova
  um job, sem bloqueios ou versões atuais validadas, usando a track francesa
  interna 2, pipeline `v3` e `qwen2.5:14b`. Nenhum novo job foi enfileirado nem
  chamada de modelo feita depois da correção. A próxima chamada real requer
  autorização explícita antes de enfileirar uma segunda tentativa, mesmo que a
  primeira tenha falhado antes do transporte.

## Segunda tentativa autorizada — resposta V3 e falha estrutural

- Após autorização explícita do usuário, o preflight permaneceu elegível e foi
  enfileirado somente Shiki E08, fonte francesa, Ollama exclusivo (`qwen2.5:14b`),
  sem publicação automática. Job `c2141e911187401cba1a77c03f131a8d`.
- O job terminou `FAILED` após aproximadamente 288 s, com razão
  `retranslation_failed`. A exceção final foi `PipelineV3Error` na validação
  estrutural, não falha de conexão: eventos 122, 250, 257 e 293 acusaram
  `LINE_BREAK_INSIDE_WORD`; evento 203 acusou alteração de tags, duplicação de
  tag inline ASS e alteração da quebra ASS.
- Foram preservadas 39 capturas V3 em estado `RESPONSE_DURABLE`, numeradas
  `000001`–`000039`, cada uma com payload, resposta bruta, resposta parseada e
  estado. Essas capturas são evidência de 39 respostas duráveis do transporte;
  não reutilizar nem reenviar chamadas automaticamente. O resumo público do job
  reportou zero chamadas/unidades, divergindo materialmente das 39 capturas —
  lacuna de telemetria a investigar.
- Não foi criado novo registro traduzido na Library: para o episódio continuam
  apenas os registros `114` (`fre`, fonte extraída) e `115` (`pt-BR`, sidecar
  legado importado). O staging foi retido em
  `/app/state/failure-ledger/web-jobs/c2141e911187401cba1a77c03f131a8d/staging`,
  mas está vazio porque a validação falhou antes da gravação do arquivo. O
  sidecar publicado continua com SHA-256
  `ca710207f638066bfa2c4800088d16d8fd72d64a7da2d882d44bf2bd7b0f452f`.
- Este resultado de runtime não constava na documentação ativa antes da
  execução. Fica reconciliado aditivamente aqui como
  `CANONICAL_STATE_RECONCILIATION_REQUIRED`. Não executar novas chamadas de
  modelo, batches ou alterações no runtime/Library/state real até analisar as
  capturas já existentes e reconciliar o diagnóstico. A autorização dada cobriu
  esta tentativa; não autoriza automaticamente uma terceira geração.
- O conteúdo foi reproduzido offline em `/tmp` a partir de cópias das mesmas 39
  capturas, com `_http_post` explicitamente bloqueado. O replay validou as 39
  capturas e reproduziu os mesmos sete erros estruturais, sem criar artefato de
  saída nem alterar as capturas originais.
- A resposta capturada para o evento 203 foi `"Vamos ver...\\n{\\n}"`. A
  normalização de quebras literais em `semantic_orchestrator` a transforma em
  `Vamos ver...\\N{\\N}`, que é ASS inválido e foi corretamente rejeitado. O
  prompt V3 atual pede para “manter tags ASS” embora os itens fornecidos sejam
  texto visível sem essas tags; isso pode induzir a saída espúria.
- As quatro ocorrências de `LINE_BREAK_INSIDE_WORD` são falsos positivos do
  heurístico atual para limites lexicais válidos: `colocá-lo|na`, `fui|tão`,
  `sei|quando` e `explicá-la|de`. O detector não trata compostos hifenizados e
  deixa de reconhecer algumas palavras curtas portuguesas. A validação deve
  continuar bloqueando divisões reais, como `vi|da`.

## Ajustes offline após a segunda tentativa

- O prompt V3 de lote e de fallback foi corrigido: como a unidade entregue ao
  modelo já é texto visível, agora proíbe inventar tags/chaves/escapes ASS e
  pede preservar a quantidade de quebras, sem dividir palavras. A validação
  estrutural continua rejeitando saída que introduza marcação ASS; foi incluído
  teste específico para o caso `Vamos ver...\\N{\\N}`.
- O detector de quebras em `ass_engine` agora trata compostos hifenizados como
  unidades lexicais e reconhece `fui`, `tão` e `sei` como palavras completas.
  Testes cobrem os quatro limites válidos observados e mantêm rejeição para
  divisões reais (`vi\\Nda` e `colocá-\\Nlo`). Não foi removido o gate estrutural.
- A telemetria do app foi ampliada para ler capturas em `v3-runs` e usar o
  número de requisições persistidas quando o runner encerra antes do resumo.
  Isso corrige o `calls=0` público apesar das 39 respostas V3 duráveis.
- Suíte offline completa passou: `1044 passed, 38 deselected, 70 subtests
  passed`. Nenhuma chamada adicional ao modelo foi feita durante o replay,
  análise ou testes.
- Antes da reconstrução seguinte, a imagem ativa ainda continha apenas a
  correção anterior do import `hashlib`; os ajustes acima precisavam ser
  incorporados no container local. Não havia candidata PT-BR traduzida.

## Runtime local após os ajustes offline

- A imagem foi reconstruída e aplicada como
  `transass:v2.5.2-local-v3-unified`, build
  `2.5.2-local-v3-ass-guard-telemetry`, proveniência
  `5b541ca-dirty-v3-ass-guard-20260927`, digest
  `sha256:f174e4648e71c8ffe9702486b54aacf54a3e6b299e157a04021e8769ba291703`.
- Foi recriado apenas o serviço local `transass`. `/health` está `ok`,
  `/version` e `/pipeline-config` informam pipeline `v3`; os mounts RW seguem
  exatamente `/PICAdeiro/data/Shows:/shows` e `/docker/transass/state:/app/state`.
- O endpoint `/inbox` agora deriva do ledger V3 que o job
  `c2141e911187401cba1a77c03f131a8d` fez 39 chamadas, com 39 respostas duráveis,
  apesar do resumo do subprocesso ausente. O job continua `FAILED` por validação
  estrutural; o número não é tratado como sucesso de tradução.
- A fila ativa está vazia. O job, as 39 capturas e os registros Library `114` e
  `115` permanecem no volume; não existe novo registro traduzido. O sidecar
  mantém o SHA-256 original acima.
- Nenhuma chamada de modelo ocorreu depois da segunda tentativa. A atualização
  local está pronta para novo teste, mas uma terceira geração precisa de
  autorização explícita.

## Terceira tentativa autorizada — replay V3 e reconciliação

- Em autorização posterior, foi enfileirado somente Shiki E08 com fonte
  francesa, pipeline `v3` e Ollama exclusivo (`qwen2.5:14b`), sem fallback,
  promoção ou publicação. Job `5f08f204f51849e8b2cffa272f88382a`.
- O job terminou `FAILED` após 290 s: `PipelineV3Error` por
  `evento 195: LINE_BREAK_INSIDE_WORD`. Foram feitas 38 chamadas semânticas;
  as 38 respostas ficaram duráveis, sem retry. O gate estrutural interrompeu a
  criação de candidata após o processamento.
- A resposta capturada para o evento 195 é
  `eles são mais de dez\ncontando os Kirishiki.`; a fonte é
  `ils sont plus de dix\nen comptant les Kirishiki.`. A quebra separa duas
  palavras completas e acompanha a quebra de linha da fonte. O falso positivo
  ocorreu porque o heurístico tratava `dez` (três letras) como provável
  fragmento de palavra.
- A correção foi limitada a reconhecer `dez` como palavra portuguesa curta
  completa, com teste de regressão para o caso capturado. A regra continua
  rejeitando divisões reais, incluindo `pala\Nvra` e `colocá-\Nlo`; nenhum gate
  estrutural foi removido.
- Foi feito replay integral em cópia temporária das 38 capturas duráveis, com
  `_http_post` bloqueado para falhar caso houvesse tentativa de rede. Com a
  correção local, o pipeline `v3_0_0` completou e validou os 298 eventos sem
  issues, consumindo 38 respostas do replay e zero chamadas físicas, de modelo
  ou de rede. O artefato existiu somente no diretório temporário e foi removido
  ao final do teste; as capturas originais não foram alteradas.
- O runtime real permanece como falha histórica (`FAILED`), sem novo registro
  traduzido na Library: seguem apenas os registros `114` (fonte francesa) e
  `115` (sidecar PT-BR importado). O sidecar continua com SHA-256
  `ca710207f638066bfa2c4800088d16d8fd72d64a7da2d882d44bf2bd7b0f452f`, e
  `published=false`. Não se converteu falha operacional em sucesso; o PASS é
  somente do replay offline das respostas capturadas após a correção.
- O monitor térmico registrou apenas `k10temp:sensor0`, com pico de 64,9 °C;
  leitura de GPU, RPM de ventoinha e potência não estavam disponíveis no
  container. Nenhum trip térmico ocorreu.
- A frase acima sobre não haver chamadas após a segunda tentativa descreve o
  estado registrado naquele momento. Esta seção adiciona a terceira execução
  autorizada e reconcilia o histórico sem editar as evidências anteriores.
- A imagem Docker local ainda precisa receber a correção de `dez`. Não houve
  reconstrução nem nova chamada ao modelo após o replay offline.

## Aplicação da correção no Docker local

- A imagem local foi reconstruída com a correção e identidade explícita:
  tag `transass:v2.5.2-local-v3-unified`, versão de build
  `2.5.2-local-v3-shiki-e08-fix-20260927`, revisão
  `5b541ca-dirty-v3-shiki-e08-linebreak-20260927`, digest
  `sha256:ea3b86d90871b256c00d8c8b613a966f36fcb283b7b1e7f07ab98a1b55fe69e7`.
  O primeiro build sem argumentos explícitos foi substituído; não permanece
  como a imagem ativa.
- Foi recriado somente o serviço `transass`, sem dependências e sem remover
  volumes. O container está `healthy`, `/health` retorna `ok`, `/version` e
  `/pipeline-config` informam pipeline `v3`. Os bind mounts RW continuam
  `/PICAdeiro/data/Shows:/shows` e `/docker/transass/state:/app/state`.
- Uma checagem determinística dentro da imagem confirmou que `dez` é aceito
  como palavra completa, enquanto `dez\Nena` e `pala\Nvra` continuam
  rejeitados como cortes lexicais.
- A fila está ociosa. Após o restart, `/status` iniciou uma nova visão em
  memória sem jobs/logs ativos; a Caixa de Entrada ainda exibe o job E08 como
  `FAILED`, e as 38 capturas duráveis seguem no volume. Na Library permanecem
  apenas os registros `114` e `115`; o sidecar conserva o mesmo SHA-256.
- Nenhuma chamada de modelo ocorreu após a terceira tentativa. O Docker agora
  contém a correção, mas o job real continua historicamente falho; a evidência
  positiva é o replay integral offline, não uma nova tradução live.
- Verificação final: suíte offline `1045 passed, 38 deselected, 70 subtests
  passed`; `tests/offline/test_ass_engine.py` passou com 7 testes após a
  organização final da lista; `git diff --check` passou. Nenhum commit ou push
  foi feito.

## Quarta tentativa live autorizada — V3 concluído; publicação a reconciliar

- Após nova autorização explícita, o preflight aprovou somente Shiki E08, fonte
  francesa da track ASS textual 2, pipeline `v3`, Ollama exclusivo
  `qwen2.5:14b`. O nome de serviço `ollama` não resolve entre as redes Compose,
  mas a URL já configurada `host.docker.internal:11434` respondeu a `/api/tags`
  e confirmou o modelo. Não foi alterada a topologia de rede.
- Job `42f8d2db88004690bc5820a677e8280e`, sessão
  `53b894e34183489db2bc09c706e4b194`, terminou `COMPLETED` após 286 s, com 38
  chamadas semânticas concluídas, zero retries e sem fallback. O resumo de
  `v3_0_0` validou 298 eventos (`valid=true`, `issues=[]`).
- O audit posterior marcou `REVISÃO RECOMENDADA`, com flags não bloqueantes
  `LINE_BREAK_INSIDE_WORD` e `POSSIBLE_UNTRANSLATED_OUTPUT`; `blocking_flags=[]`
  e `eligible_for_archive=true`. Portanto, conclusão estrutural V3 não equivale
  a aprovação linguística integral.
- Foi criado o registro Library `116` (PT-BR, `TRANSLATED`, pipeline `v3`, hash
  `4cb661fd09612a91fb9ea934682ba045dac42bc5c6eae44d5a1bd7f5ce1eecbc`), com
  lineage `TRANSLATED_FROM` do registro francês `114` e `RETRANSLATED_FROM` do
  antigo PT-BR `115`. Ele aparece como `preferred=0`; os registros anteriores
  permanecem.
- Há divergência operacional a reconciliar antes de qualquer nova escrita:
  a resposta inicial de enfileiramento informou `published=false`, enquanto o
  job persistido terminou com `published=true`, razão
  `library_record_created_and_published`, e o log contém
  `Publicado: source-114.pt-BR.ass`. Por outro lado,
  `GET /library/records/116/publications` retornou lista vazia e o sidecar em
  `/shows/Shiki/Season 1/Shiki - S01E08 - Eighth Night Bluray-1080p.pt-BR.ass`
  continua com SHA-256
  `ca710207f638066bfa2c4800088d16d8fd72d64a7da2d882d44bf2bd7b0f452f`. A
  evidência confirma que o sidecar
  anterior não foi sobrescrito, mas não permite tratar o campo `published` e a
  mensagem de log como equivalentes a “sem publicação”. Estado operacional:
  `CANONICAL_STATE_RECONCILIATION_REQUIRED`; preservar o registro 116 e não
  iniciar novo job, publicação ou mutação até reconciliar essa semântica.
- Reconciliação estritamente read-only da divergência acima: o job persistido
  tem `source_abs=/app/state/anime-subtitle-library/staging/source-114-vu1h_rwm/source-114.ass`
  e `source_staging_path=/app/state/anime-subtitle-library/staging/source-114-vu1h_rwm`.
  O worker deriva o destino de publicação aplicando `with_suffix()` a
  `source_abs`; portanto, moveu a saída para um nome `.pt-BR.ass` no staging da
  fonte, não ao lado do vídeo. O `finally` remove `source_staging_path`, então
  esse arquivo temporário já não existe. Isso explica `published=true` e o log
  incorreto sem implicar publicação efetiva.
- A API confirmou para o registro 116: `publications=[]`,
  `publication_status=NOT_PUBLISHED`, `published=false`, `target_present=true`,
  `target_record_id=115` e `target_sha256=ca710207f638066bfa2c4800088d16d8fd72d64a7da2d882d44bf2bd7b0f452f`.
  O objeto Library do registro 116 existe e seu SHA-256 confere com o hash do
  registro. O sidecar antigo manteve o mesmo hash. Logo, a tradução V3 foi
  arquivada na Library, mas não publicada no acervo de arquivos; nenhum
  sidecar existente foi sobrescrito.
- A causa documentalmente reconciliada é um bug de destino/telemetria no worker
  de retradução: ele confunde o caminho temporário da fonte com o caminho do
  vídeo e pode anunciar uma publicação que não ocorreu. Nenhuma outra chamada
  ao modelo, publicação, alteração de Library/state ou restart foi realizada.
  O estado operacional permanece bloqueado para novas operações com efeitos
  até a correção offline e verificação desse bug; esta constatação não muda nem
  promove o registro 116.
- A telemetria disponível foi `k10temp:sensor0` (CPU); o container não expôs
  leitura específica de GPU, RPM ou potência. Não ocorreu trip térmico.
- Esta seção preserva a falha anterior como histórico e adiciona o resultado
  live autorizado. Não houve nova chamada ao modelo depois deste job.

### Atualização aditiva — correção offline da publicação enganosa

- O diagnóstico foi reconciliado documentalmente: `source_abs` é o caminho
  temporário da fonte arquivada, e o job terminou sem publicação no acervo de
  arquivos. O `published=true` persistido é uma telemetria incorreta histórica;
  não editar esse registro nem o snapshot do job.
- Em `src/subtranslate/app.py`, a finalização passou a delegar a publicação a
  `AnimeSubtitleLibrary.publish(record_id, allow_replace=False)`. Assim o alvo
  vem do episódio registrado, uma publicação real ganha registro durável na
  Library, e colisões preservam o sidecar existente e deixam o job como não
  publicado. A lineage e o registro 116 não foram modificados.
- Foram adicionados dois testes offline do worker: conflito com sidecar
  existente (sem sobrescrita/publicação falsa) e destino ausente (publicação
  Library efetiva e registrada). `tests/offline/test_p2b3b_integrity.py` passou
  com 8 testes e 2 subtestes; a suíte offline completa passou com
  `1047 passed, 38 deselected, 70 subtests passed`; `git diff --check` passou.
- A correção existe somente no worktree canônico e não foi commitada, enviada
  nem instalada no Docker. O container e seu state não foram reiniciados ou
  alterados; não foi feita outra chamada ao modelo. Qualquer nova prova no
  runtime aguarda autorização explícita para reconstruir/recriar o container,
  e uma nova tradução continua exigindo autorização própria.

### Atualização aditiva — auditoria comparativa do Shiki E08

- Comparação read-only dos registros 114 (fonte francesa) e 116 (PT-BR): ambos
  têm 298 eventos; `[Script Info]`, estilos, metadados de cada evento, tags e
  quebras de linha permanecem estruturalmente compatíveis. A AST V3 aprovou a
  saída e a auditoria final não encontrou flags estruturais/de conteúdo. Quatro
  cópias exatas são nomes/título (`Sunako`, `SHI KI`, `YÛKI - KOIDE`), não falas
  francesas não traduzidas. A busca de resíduo francês forte não encontrou
  correspondências adicionais.
- Uma quebra `\N` ausente foi identificada no evento da fala “Qu'est-ce qui vous
  tracasse chez moi ?” e restaurada na cópia corrigida como
  “O que te incomoda\Nem mim?”. Também foram corrigidos, nessa cópia, erros
  selecionados de sentido, pronome, urgência, vocabulário e espaçamento; entre
  eles, `pieu` como estaca, `lui` referente a Natsuno e a ordem para entrar.
- A cópia corrigida está em `/tmp/transass-e08-116.pt-BR.ass`, SHA-256
  `aada15351afdf16880f1c39c19926b44c953f74de2830ab743c036df634d9779`.
  Registro 116, Library e sidecar não foram alterados; essa edição manual não é
  publicação nem prova de uma nova tradução por modelo.
- No código V3, o prompt agora explicita preservação de agente/paciente,
  negação, tempo verbal, gênero/número e intenção; respostas vazias, unidades
  ausentes, cópia integral conhecida e resíduos fortes em inglês/francês falham
  sem produzir saída final. A auditoria deixou de sinalizar quebras legítimas
  aceitas pelo validador V3; a checagem de palavra dividida ignora tags ASS ao
  avaliar o texto visível.
- Revisão de arquitetura confirmou que o adapter de execução padrão é V3, mas
  ainda há dependências ativas de `pipeline_v2_1_3.py`: classificador usado pelo
  planejador V3, indicadores/regras de resíduo e validadores usados pela
  auditoria web. Isso é reutilização de helpers legados, não seleção do plano
  V2.1.3 para o job. `docs/PIPELINES.md` agora explicita que a extração ainda
  não terminou; módulos antigos continuam necessários para replay histórico.
- Verificação offline após as correções: `1052 passed, 38 deselected, 70
  subtests passed`; `git diff --check` passou. Nenhum modelo, Docker, Library,
  state real, commit ou push foi acionado neste trabalho.
