# Shiki E13 — resposta Ollama incompleta (r22)

**Data:** 2026-09-28 (America/Recife)  
**Episódio:** Shiki S01E13, ID 300  
**Runtime observado:** `transass:v2.5.2-local-v3-candidate-only-20260928-r22`, V3, `qwen2.5:14b`  
**Escopo:** candidata isolada; Ollama exclusivo, sem fallback, sem promoção/publicação.

## Evidência operacional

O preflight read-only aprovou o episódio e identificou a faixa ASS textual
francesa `FR Full ASS`, índice interno 2. Foi enfileirado somente o E13 como
candidata. A resposta da fila registrou `published=false`.

O job `f5540755c3974025a7837e219cfb9e97` falhou após duas chamadas físicas.
Nenhuma delas produziu uma resposta candidata utilizável. As capturas duráveis
foram preservadas em:

`/app/state/v3-runs/d7ab115da1c361f26a3f1505/fb10f32b5dd8d8b69a343ed9361d64ce/captures/`

- `...-000001`: HTTP 200, estado `RESPONSE_DURABLE`, `done=false`, conteúdo
  vazio, 96 bytes; SHA-256
  `c48a0716fc6f4ce9758b10d8732bf182719c3d9f404e1b1833dc89caf02eea6a`.
- `...-000002`: HTTP 200, estado `RESPONSE_DURABLE`, `done=false`, conteúdo
  parcial `"{\n"`, 129 bytes; SHA-256
  `7a71422fff4c04c61cfbff37ee0fa229416a2e609eb6cb7c6802efe4162868b6`.

Os logs do Ollama registraram duas exceções do llama.cpp:
`Unexpected empty grammar stack after accepting piece: ? (30)`. A resposta
parcial foi corretamente rejeitada pelo contrato JSON estrito V3 como
`V3_BATCH_RESPONSE_NOT_JSON`. A leitura de telemetria no fim do job estava em
62 °C; não houve parada térmica. A API do Ollama define `done` como indicador
de que a resposta terminou e aceita `format` como `json` ou JSON Schema
([documentação da API Chat](https://docs.ollama.com/api/chat)).

## Correção preparada no código

O r22 repetiu a chamada uma vez, mas preservou o JSON Schema que estava no
pedido original; a segunda resposta incompleta mostra que esse retry idêntico
não resolveu o incidente. A causa provável é incompatibilidade/erro interno
do caminho de gramática estruturada do runner Ollama; essa relação é uma
inferência apoiada pela coincidência entre as chamadas e a exceção nos logs,
não uma causa universalmente demonstrada.

O worktree agora contém uma correção estreita e fail-closed:

- `OllamaTransport` classifica qualquer envelope explícito `done=false` como
  resposta incompleta, mesmo que contenha alguns caracteres.
- Em `LIVE_CAPTURED`, apenas `OLLAMA_EMPTY_CONTENT` ou
  `OLLAMA_INCOMPLETE_RESPONSE` permitem uma única tentativa adicional. Ela
  usa novo capture ID, consome nova reserva do orçamento físico e muda
  `format` para `json`, mantendo o mesmo prompt e modelo.
- O parse V3 ainda exige JSON válido, todos os IDs/unidades e as validações
  semânticas/estruturais existentes. Não há aceitação de conteúdo parcial,
  retry de timeout/rede, repetição de capture ID ou troca de modelo.

Os testes offline focados de V3, transportes e integração passaram:
`129 passed`; `git diff --check` passou. O build/runtime r22 não contém essa
correção ainda. Até a instalação/verificação do r23, o estado operacional é
`CANONICAL_STATE_RECONCILIATION_REQUIRED`; não iniciar outro job/model call.

## Próxima ação permitida

Construir um overlay r23 offline sobre o r22, recriar somente o serviço
`transass`, e verificar health, V3/Qwen2.5:14b, fila inativa, os mounts
persistentes e que o Ollama não foi reiniciado. Depois dessas verificações,
retomar somente o E13 como candidata, ainda sem Library/publicação. E14+ fica
bloqueado até a candidata E13 passar validação estrutural e semântica.

## Follow-up: r23 e falso positivo de nomes (2026-09-28)

O overlay r23 foi instalado no serviço `transass` apenas. O container ficou
healthy em V3/Qwen2.5:14b, fila ociosa antes do preflight, mounts RW de mídia e
state preservados; o contador de restart do Ollama continuou em zero. O
preflight read-only do E13 aprovou 1/1.

A nova tentativa candidata, job `97ca013c0a74431685a736be75758ce9`, recebeu
11 respostas V3 completas e terminou sem candidata no fechamento semântico:
`V3_TRANSLATION_SOURCE_COPY:83`. A captura 11, preservada em
`/app/state/v3-runs/a7331c34914391ce27ff9470/4860fe13632d4d4ef1e816e0a48aa13c/captures/v3-retranslate-a7331c34914391ce27ff9470-000011/`,
tem SHA-256
`6f591c6136d27139c12023c53639b90f736cf150d039fe78fd176c02ad177643`.

A unidade 83 é um diálogo de chamada de nomes, fonte `- Shinmei !\n- Tokujirô...`,
resposta `- Shinmei!\n- Tokujirô...`. Só `Tokujirô` estava
na lista de nomes protegidos para esse lote; a saída manteve ambos os nomes e
normalizou apenas pontuação/espaços. A regra antiga comparava todos os tokens
visíveis e bloqueou incorretamente a unidade. Isso é um falso positivo
reproduzido pela captura; não é razão para desativar o bloqueio geral de cópias.

O patch local adiciona exceção estrutural estreita: duas ou mais linhas
iniciadas por travessão/hífen, cada uma com um único nome título-casado que
passa por uma análise conservadora de sílabas Hepburn (mínimo de três sílabas,
duas com ataque consonantal e marcador de romanização japonesa como `sh`, `ts`
ou circunflexo) e não consta como palavra/interjeição/imperativo francês comum;
ao menos um dos nomes precisa já estar protegido. Sem âncora protegida, ou com
uma fala como `Bonjour`, `Attends`, `Vite`, `Chérie` ou `Tsunami`, a cópia
continua bloqueada. Testes V3, qualidade, transporte e integração: `144 passed`;
`git diff --check` passou. Essa correção ainda não está no container r23.
Estado operacional: `CANONICAL_STATE_RECONCILIATION_REQUIRED`
até instalar/verificar r24; não iniciar chamadas adicionais nem E14+ antes
disso. Após o r24, repetir apenas o E13 como candidata e validar seu fechamento.

## Reconciliação read-only do r24

O overlay r24 foi construído offline sobre r23 e instalado recriando somente
`transass`: imagem
`transass:v2.5.2-local-v3-candidate-only-20260928-r24`, ID OCI
`sha256:36ac157c764a716659534ed8f97fabea03047856c860bf93baf45f5b580212f9`,
revisão `5b541ca-dirty-v3-name-call-romaji-20260928-r24-overlay-from-r23`.
`/health` responde `ok`; `/version` informa V3 e 2.5.2; `/status` mostra fila
vazia, `running=false`, sem job corrente, plano V3 e modelo Qwen2.5:14b.

Os mounts permanecem `/PICAdeiro/data/Shows:/shows` e
`/docker/transass/state:/app/state`, ambos RW. Ollama continua running, com
`restart_count=0` e sem inferência em `ollama ps`. Nenhuma chamada de modelo
foi feita após o recreate. Isso reconcilia o estado anterior do r23 e libera
somente o preflight read-only e, se elegível, uma nova candidata isolada do E13.

O teste isolado da imagem r24 passou para o caso capturado e para os controles
franceses `Bonjour`, `Bonsoir`, `Attends`, `Vite`, `Chérie` e `Tsunami`; a suíte
focada passou `144/144`. A re-review independente da heurística não retornou
durante a execução. O critério continua conservador e não constitui dicionário
universal de nomes: o gate geral de cópia segue ativo e qualquer caso de nome
ambíguo deve continuar falhando fechado até evidência/teste específico.

## Follow-up: r24 e nome após preposição compartilhada (2026-09-28)

A tentativa candidata isolada do E13 no r24, job
`19016489ff924d2b87262ea5e3da52f8`, terminou `FAILED` após 14/14 respostas
completas, em `V3_TRANSLATION_SOURCE_COPY:106`. Não produziu candidata nem
escreveu/publicou na Library. A GPU ficou abaixo de 80 °C e não houve parada
térmica.

A captura durável 14 está em
`/app/state/v3-runs/6d2361144becd77cd86f7e96/7ae672b4a80190b7238f6c6f2c643b0d/captures/v3-retranslate-6d2361144becd77cd86f7e96-000014/`.
O corpo HTTP preservado tem SHA-256
`3dd0bb2d50629bdbd0cfe2489854571f3545f32b260ce83087ab1d82ebd82736` e contém
a resposta `De Seishin ?` para a fonte francesa `De Seishin ?`; o prompt da
unidade incluía `Seishin` entre os nomes a preservar. Os tokens são idênticos,
mas `de` é uma preposição válida tanto em francês quanto em pt-BR, tornando
esta ocorrência específica uma tradução semanticamente válida que o gate
genérico classificou como cópia. O espaço anterior ao `?` é tipografia francesa,
não deve ser mantido em pt-BR.

Correção local em preparação: remover deterministicamente espaços horizontais
franceses antes de `?`, `!`, `:` e `;` somente na direção francês → português,
sem tocar em quebras de linha; aceitar identidade somente para `de` seguido de
um único nome protegido que também passe a regra conservadora de romanização
japonesa. Controles negativos cobrem `De quoi?`, `De Seishin, partez!`, `De
Dieu?` e ausência de nome protegido. Essa mudança ainda requer testes offline,
revisão independente, build/reconciliação r25 e novo preflight E13 antes de
qualquer chamada adicional. E14+ permanece bloqueado.

### Validação local do reparo

Os testes focados de pipeline V3, qualidade e resíduo francês passaram `95/95`.
Um caso adversarial mostrou que o marcador `ch` pode classificar palavras
francesas comuns como `Chanson`/`Chapeau` como romaji; a exceção desta frase
passou a exigir um marcador mais distintivo (`sh`, `ts`, `ky`, etc.) ou
circunflexo/macron, e os dois controles ficaram bloqueados mesmo quando
informados como nomes protegidos. Quebras de linha não são normalizadas.

A suíte offline completa resultou em `1128 passed, 38 deselected, 70 subtests`
e uma falha já conhecida, independente deste patch:
`CanonicalV238EnforcementTests.test_visual_glyph_primary_tokens_cleanup_and_fallback`
(esperado `VISUAL_GLYPH_BASE_FALLBACK`, obtido `VISUAL_GLYPH`). `git diff
--check` passou. As duas tentativas de revisão independente desta alteração não
retornaram; portanto este relatório não reivindica aprovação independente. A
revisão manual adicionou os controles adversariais acima. O código ainda não
está no Docker: r24 permanece ativo, sem chamadas adicionais após a falha E13.
Próximo passo autorizado: build/reconciliação do r25, validar health/V3/mounts/
Ollama sem restart e preflight somente do E13; nenhum E14 ou publicação antes
do fechamento validado de E13.

### Re-review: blockers e correção adicional

Uma revisão independente posterior encontrou dois blockers na primeira versão
do bypass: ele não estava condicionado ao idioma de destino e não exigia que a
resposta preservasse a forma/pontuação da fonte. Também apontou o espaço
inseparável estreito U+202F usado tipograficamente em francês. Essa re-review
chegou depois do r25 e antes de qualquer nova inferência; por isso o r25 não é
liberado para tradução e o preflight anterior não basta para retomar.

A correção local atual exige explicitamente destino pt-BR, igualdade exata da
resposta com a fonte após apenas retirar espaços horizontais antes de
`?!;:`, nome protegido e marcador romaji distintivo. Pontuação ausente ou
alterada, destino inglês, nome não protegido e a forma sem pontuação
`De Seishin` sem proteção continuam bloqueados. A normalização inclui NBSP e
U+202F e não toca quebras de linha; o bypass genérico de title-case não cobre
mais a forma francesa sem pontuação `de + um token`. Testes focados atualizados:
`95 passed`; nenhuma chamada de modelo ocorreu. Uma segunda auditoria do estado
final foi solicitada e ainda está pendente.

O código final está somente no worktree; a imagem ativa continua r25, que não
contém esses reforços. Estado operacional: `CANONICAL_STATE_RECONCILIATION_REQUIRED`.
Próximo passo: revisão final, build/recreate somente Transass como r26, validar
health/V3/mounts/Ollama, novo preflight read-only E13 e só então uma candidata
isolada. E14+, Library e publicação permanecem bloqueados.

### Reconciliação r25 antes do preflight

O overlay local r25 foi construído sobre r24 e recriou apenas `transass`.
Imagem `transass:v2.5.2-local-v3-candidate-only-20260928-r25`, OCI ID
`sha256:617d2d94fc57148ea8f7c0baddf9e498032775e27473203b653b5cdd474cf7cb1`,
revision `5b541ca-dirty-v3-shared-de-name-20260928-r25-overlay-from-r24`.
`/health` respondeu `ok`; `/version` e `/status` confirmam versão 2.5.2,
pipeline V3, modelo Qwen2.5:14b, serviço disponível, fila vazia e sem job
corrente. O smoke offline sem rede da imagem e uma checagem no container ativo
confirmaram a correção `De Seishin?` e os negativos `De quoi?`, `De Chanson?`
e cláusula adicional.

Os mounts RW continuam `/PICAdeiro/data/Shows:/shows` e
`/docker/transass/state:/app/state`. Ollama permanece running com
`restart_count=0` e `ollama ps` vazio. Nenhuma inferência foi feita após o
recreate. Isso reconcilia o estado e libera somente o preflight read-only de
E13; se elegível, uma candidata isolada E13 Ollama-only. E14+ permanece
bloqueado até validação completa; Library/publicação continuam fora de escopo.

### Resultado do preflight r25 e bloqueio posterior

Antes da re-review, o preflight read-only do r25 aprovou E13 (`episode_id=300`)
1/1 como elegível, usando a track `FR Full ASS`, textual, codec ASS, índice
interno 2. O revisor independente encontrou os blockers descritos acima depois
desse preflight, e não foi enfileirada tradução no r25. Portanto essa aprovação
está supersedida: após instalar r26 será necessário um novo preflight E13 antes
da próxima chamada. O estado final desta etapa é r25 saudável e ocioso, patch
corrigido apenas no worktree, nenhum novo modelo chamado e E14+ bloqueado.

### Revalidação offline após os blockers

Com os blockers e U+202F corrigidos, os testes focados foram executados de
novo: `95 passed`. A suíte offline completa foi repetida no mesmo estado final:
`1128 passed, 38 deselected, 70 subtests`; a única falha continua sendo o teste
legado não relacionado `CanonicalV238EnforcementTests.test_visual_glyph_primary_tokens_cleanup_and_fallback`
(`VISUAL_GLYPH` vs. esperado `VISUAL_GLYPH_BASE_FALLBACK`). Execução completa:
222,99 s. `git diff --check` passou. A re-review final foi solicitada ao
revisor que encontrou os blockers e ainda não retornou. Nenhuma chamada Ollama
foi feita; r25 segue ativo, ocioso e sem a correção final.

### Restrição final do destino

A re-review encontrou mais um caso de borda: a função de idioma canônico também
classificava `pt` e `português` genéricos como pt-BR. A exceção agora aceita
somente rótulos com `pt-BR`/`pt_BR` ou igualdade explícita `Português do Brasil`
(`Portugues do Brasil` sem acento também); os destinos genéricos continuam
rejeitando cópia. Os testes focados após esse último ajuste passaram `95/95` e
`git diff --check` passou. A suíte completa `1128 passed` registrada acima foi
executada antes dessa restrição final; sua única falha foi a conhecida de
V2.3.8, mas não foi repetida depois desta pequena mudança. A auditoria final da
restrição foi solicitada; sem tradução enquanto não houver resposta e r26.

### Re-review final: title-case, dois-pontos e quebra ASS

A auditoria independente encontrou outra rota de falso negativo no bypass
genérico: `De Seishin :` e `De\nSeishin` podiam ser aceitos como cópia
title-case, mesmo fora da exceção estrita. O código local agora bloqueia essas
formas por padrão. A exceção protegida pode aceitar uma única quebra entre `de`
e o nome apenas se destino pt-BR, nome protegido/romaji distintivo e conteúdo
com quebra exatamente preservada forem todos verdadeiros; dois-pontos e
ponto-e-vírgula não entram na exceção. Foram adicionados testes para os
exemplos, incluindo a saída que remove a quebra. Testes focados após este patch:
`95 passed`; `git diff --check` passou. A suíte completa registrada acima não
foi repetida após os últimos deltas de escopo/delimitadores; ela permanece
evidência do estado anterior, com a única falha V2.3.8 conhecida.

A re-review final foi solicitada ao auditor que apontou o risco. Até o parecer,
r25 continua ativo, sem o patch final e sem novas inferências; r26 + novo
preflight E13 seguem necessários. E14+, Library e publicação continuam
bloqueados.

### Reconciliação r27 e preflight E13

Overlay r27 construído offline sobre r26 e instalado recriando somente
`transass`: imagem `transass:v2.5.2-local-v3-candidate-only-20260928-r27`, OCI
`sha256:d65bd45364e5543e976716424052053ef06da6fde7fc907c673fec881c72b85a`,
revision `5b541ca-dirty-v3-french-shared-mal-20260928-r27-overlay-from-r26`.
Health OK, versão 2.5.2/V3, Qwen2.5:14b, fila vazia e sem job corrente. Smoke
adversarial da imagem e do container ativo aprovou o singleton `mal` e
continuou rejeitando pontuação alterada, destino `pt` e frase composta.

Mounts RW `/PICAdeiro/data/Shows:/shows` e `/docker/transass/state:/app/state`
preservados; Ollama running, `restart_count=0`, sem inferência ativa. Novo
preflight read-only E13 aprovou 1/1, track `FR Full ASS`, ASS textual interna
2. Liberada somente uma tentativa candidata E13 em Ollama-only, sem
Library/publicação; E14+ bloqueado até o fechamento validado.

### Parecer independente final

O revisor independente confirmou que os blockers anteriores estão fechados e
não encontrou outro blocker nos pontos revisados: destino pt-BR, igualdade após
normalização limitada, preservação de pontuação, U+202F, `:`/`;`, quebra ASS e
tentativas de escape via title-case. O parecer foi somente leitura e não
executou testes; a evidência de testes segue sendo `95 passed` focados e o
resultado amplo documentado antes dos últimos deltas. Está liberado o build r26
e a reconciliação read-only; ainda é necessário preflight novo, uma candidata
E13 e validação completa antes de E14.

### Reconciliação final r26 e novo preflight E13

O overlay r26 foi construído offline sobre r25 e instalado recriando apenas
`transass`: imagem `transass:v2.5.2-local-v3-candidate-only-20260928-r26`, OCI
`sha256:d144b78c5b435ca9788332c42f668cf7b95bf3fcdd3a5868f07449e4a0c73e83`,
revision `5b541ca-dirty-v3-shared-de-name-strict-20260928-r26-overlay-from-r25`.
`/health` respondeu `ok`, `/version` informa 2.5.2/V3 e `/status` confirma fila
vazia, sem job corrente e modelo Qwen2.5:14b. O smoke sem rede da imagem e os
controles no container ativo confirmaram as condições auditadas, inclusive
`:`, destino genérico `pt` e quebra ASS sem nome protegido.

Os mounts RW de mídia/state permanecem intactos. Ollama segue running com
`restart_count=0` e `ollama ps` vazio. O novo preflight read-only E13 aprovou
1/1 elegível, fonte textual `FR Full ASS` interna, track 2. Nenhuma inferência
ocorreu após o recreate; está liberada somente uma candidata isolada do E13 em
`candidate_only` + `ollama_only`. Sem Library/publicação; E14+ permanece
bloqueado até validação estrutural e semântica completa do E13.

### Falha E13 r26: singleton compartilhado francês/pt-BR

A tentativa candidata r26, job `9232d446cf434727ace53c9d9c5f4481`, terminou
`FAILED` depois de 15/15 respostas duráveis e foi bloqueada em
`V3_TRANSLATION_SOURCE_COPY:116`. Nenhuma candidata válida foi criada, nem
houve escrita/publicação na Library. A captura 15 está em
`/app/state/v3-runs/18c99be8ac94f174c1e5ecac/8666b22de6cdfe53af0015760936d3c6/captures/v3-retranslate-18c99be8ac94f174c1e5ecac-000015/`;
SHA-256 do corpo HTTP:
`67f480014b811a1a3b1d0a8e52eef699e91c1ff2327b47786c0753e010cc0772`.

A unidade 116 tem fonte e saída `Mal.`. No lote, ela responde à pergunta
`Et comment va-t-elle ?` / `E como ela está?`; `Mal.` é uma resposta natural
em francês e pt-BR. O dicionário geral de indicadores franceses continha
`mal` e gerou um falso positivo de cópia. Temperatura máxima observada: 80 °C,
sem trip térmico.

Foi preparado um reparo local extremamente estreito: aceitar identidade apenas
para o singleton `mal`, idioma-alvo explicitamente pt-BR e igualdade integral
depois da normalização autorizada de espaço francês antes da pontuação. Uma
mudança de pontuação, outro destino ou texto maior continua bloqueado. Os testes
focados atuais passaram `96/96`; `git diff --check` passou. Revisão
independente deste novo bypass está pendente. A suíte ampla anterior não foi
repetida após esse delta. O container continua r26 e ocioso; antes de qualquer
nova chamada, registrar/revisar o patch, instalar r27, verificar mounts/Ollama,
fazer novo preflight E13 e então uma candidata isolada. E14+ continua bloqueado.

### Re-review do bypass `mal`

O revisor independente confirmou sem blocker: a exceção requer fonte francesa,
destino pt-BR, exatamente um token `mal` e igualdade integral após a
normalização tipográfica estreita. Pontuação diferente continua rejeitada.
Parecer somente leitura, sem alteração de arquivos nem execução de testes. Isso
libera o build/reconciliação r27; ainda não libera nova chamada até health,
mounts/Ollama e preflight E13 atuais.

### E13 r27 concluído e candidato auditado

O retry E13 r27, job `50fc25d098e9446b8e5c11459318e8f3`, terminou `COMPLETED`
em `CANDIDATE_READY` após 36/36 chamadas semânticas e 294,7 s. O aplicativo
marcou `SEM PROBLEMAS DETECTADOS`, sem blocking/review flags, elegível para
arquivamento, mas `candidate_only_no_library_no_publication`; não foi arquivado
nem publicado. SHA-256 da candidata
`Shiki - S01E13 - Thirteenth Tragedy Bluray-1080p.mkv.pt-BR.ass`:
`75cbef244a625b2abb994918679d7e40acd5de4da5ea013e6eb1730440ca3d55`.
Telemetria máxima: 79 °C; sem parada térmica.

Auditoria externa read-only: a track original do MKV é o stream 2 (`ass`,
`fre`, título `FR Full ASS`). Fonte e candidata contêm 286/286 eventos. Foram
comparados começo/fim, estilo, nome/ator, layer, margens, efeito e comentário:
0 divergências. Contagem de quebras visíveis por evento: 0 divergências em
286/286. SHA do download local bate com o registrado pelo job. E13 está fechado
como candidata validada; sem Library/publicação. O próximo passo permitido é
somente preflight read-only E14.

O preflight read-only do E14 (`episode_id=301`) aprovou 1/1 elegível na mesma
track textual `FR Full ASS` (stream interno 2, ASS). Esse resultado não inicia
tradução; libera somente a candidata isolada do E14, ainda sem publicação.

### Falha E14 r27: nome próprio repetido em linhas sem travessão

A primeira candidata E14 no r27, job `51a070003e8c4fbf8b2319fc3218e44b`,
falhou depois de 12/12 respostas em `V3_TRANSLATION_SOURCE_COPY:95`; não houve
candidata, Library/publicação ou E15. Captura 12:
`/app/state/v3-runs/d7bff21f3c631ae680d779a9/82a8632999d1a1991eaaa96af6735d10/captures/v3-retranslate-d7bff21f3c631ae680d779a9-000012/`;
SHA-256 do corpo HTTP `3b8fe225165ebe31fdf9e6dd7c31316a2c54d979040df2e26cc261cd541cb158`.

A unidade 95 é fonte `Akira !\NAkira !` e saída `Akira!\nAkira!`, duas chamadas
do mesmo nome próprio. O prompt desse lote só tinha Kirishiki e Megumi no
inventário protegido; a extração do ASS inteiro também não promoveu Akira,
pois ele ocorre apenas nesse evento e não aparece em contexto de frase. O
detector existente de chamadas de nomes aceita trocas com travessões, não
repetições sem travessão. Temperatura máxima 78 °C, sem trip térmico.

Correção local em preparação: reconhecer apenas eventos compostos por duas ou
mais linhas, cada uma contendo exatamente o mesmo token romaji de aparência
japonesa, com pontuação opcional; exigir igualdade integral após a normalização
tipográfica permitida. Interjeições/palavras francesas comuns (incluindo
`chanson` e `chapeau`), falas com texto adicional, tokens diferentes e quebra
alterada continuam bloqueados. Testes offline e revisão independente são
necessários; r28 e novo preflight E14 antes de qualquer retry. E15+ bloqueado.

### E14 r28: gate restrito para nome repetido (antes de novo retry)

A proposta inicial de aceitar qualquer token com segmentação fonética romaji
foi descartada após revisão adversarial: palavras francesas como `Banane`,
`Chimie`, `Chinoise` e `Chienne` também podem satisfazer essa forma. O gate
final não infere nomes pela fonética nem pelo inventário automático: aceita
somente o token exato revisado `Akira`, repetido em pelo menos duas linhas
compostas apenas pelo mesmo nome e pontuação opcional, de francês para pt-BR,
quando a resposta inteira permanece idêntica após remover somente o espaço
francês antes da pontuação. Diferença de pontuação, quebra, token, capitalização,
frase adicional ou destino continua sujeita ao bloqueio V3.

Também foi fechada a rota legada de chamadas com travessão: ela agora exige
destino pt-BR e identidade integral da resposta depois apenas da normalização
permitida de espaços franceses; palavras francesas da lista de controles são
rejeitadas mesmo se aparecerem no conjunto de nomes protegidos. O caso conhecido
`- Shinmei ! / - Tokujirô...` permanece permitido somente com a saída idêntica
após a normalização prevista.

Foram adicionados controles para `Banane`, `Marianne`, `Papaye`, `Chanson`,
`Chapeau`, `Chimie`, `Chimère`, `Chinoise`, `Chienne` e `Pyjama`, além de testes ASS
integrados para ambas as rotas. As suítes offline relacionadas passaram
`133/133`; `git diff --check` passou. A imagem r28 foi construída offline a
partir de r27, compilou os dois módulos e passou smoke sem rede/mounts. OCI:
`sha256:319c8d3cba1febf6d188a3586e7518b15841a0dc93a4c2522a44c28b910eb026`;
revision `5b541ca-dirty-v3-repeated-name-call-20260928-r28-overlay-from-r27`.

Neste registro, r28 ainda não está instalado; r27 continua saudável, fila
parada após a falha E14, mounts `/shows` e `/app/state` RW preservados, Ollama
ativo sem restart/job. Ainda não houve nova chamada ao modelo. A revisão
independente encontrou blockers nas propostas iniciais; ambos foram corrigidos.
A revisão independente do patch final está pendente. Antes da candidata E14:
fechar a revisão,
recriar somente Transass em r28, confirmar saúde/V3/mounts/Ollama, registrar a
reconciliação e fazer novo preflight read-only. E15+ continua bloqueado.

### Reconciliação operacional r28 e preflight E14

Foi recriado somente o serviço local `transass` com a imagem
`transass:v2.5.2-local-v3-candidate-only-20260928-r28`, OCI
`sha256:319c8d3cba1febf6d188a3586e7518b15841a0dc93a4c2522a44c28b910eb026`,
revision `5b541ca-dirty-v3-repeated-name-call-20260928-r28-overlay-from-r27`.
`/health` respondeu `ok`; `/version` reporta V3/2.5.2 e essa revision. O status
do app ficou ocioso, sem job corrente ou fila. Os mounts
`/PICAdeiro/data/Shows -> /shows` e `/docker/transass/state -> /app/state`
permanecem RW. O Ollama permaneceu `running`, `restart_count=0`, sem processo
de inferência (`ollama ps` vazio).

A captura da falha anterior E14 continua preservada no volume e seu SHA-256
confere com o ledger registrado:
`3b8fe225165ebe31fdf9e6dd7c31316a2c54d979040df2e26cc261cd541cb158`.
O preflight read-only de E14 (`episode_id=301`) aprovou 1/1 elegível: stream ASS
textual francês `FR Full ASS`, índice interno 2. Não houve model call nessa
reconciliação. Está liberada somente uma candidata isolada E14, Qwen2.5:14b,
Ollama-only e candidate-only; sem arquivo na Library ou publicação. E15+ segue
bloqueado até E14 ser concluído e auditado.

### E14 r28: nome protegido em linha simples e fallback Title Case

A candidata r28, job `3919493568dc4e4fa1d5addd83376e23`, consumiu 24/24 respostas
semânticas em 208,97 s e falhou em `V3_TRANSLATION_SOURCE_COPY:191`. A captura
24 está em
`/app/state/v3-runs/e7b8c7981f21e2baee7086a5/cfb55e795d1c8aeb97118c46c5ed0c2a/captures/v3-retranslate-e7b8c7981f21e2baee7086a5-000024/`;
SHA-256 do corpo HTTP `e879bf5c1407b660d90ddd750cf00793115a50a76cc6a4e9d5b6f8f2d832e5dc`.
Unidade 191: fonte e resposta `Kyôko Ozaki.`. Ambos os nomes constavam na
instrução do modelo como nomes próprios protegidos; não havia fala francesa
para traduzir. O gate rígido não cobria nome-only com vários nomes numa linha.
O job não gerou candidata nem publicou. Pico GPU 79 °C, sem trip.

A revisão adversarial também encontrou que o fallback genérico de Title Case
podia liberar cópias como `Banane\nBanane`, destinos genéricos e alterações de
espaço/pontuação quando a verificação estrita falhava. O patch local agora
fecha o fallback para cópias francesas: só permite identidade de nomes se todos
os tokens visíveis forem protegidos, nenhum for palavra francesa da lista de
bloqueio, o destino for pt-BR e a resposta visível inteira for idêntica após a
normalização tipográfica limitada. Inclui teste de integração em que o
inventário dos nomes Kyôko/Ozaki é extraído de contexto e permite `Kyôko
Ozaki.`; cópias francesas capitalizadas sem nomes protegidos são bloqueadas.
Também cobre os probes adversariais documentados pelo auditor. Suítes focadas:
`134 passed`; `git diff --check` limpo. O overlay r29 foi construído offline
sobre r28, compilou os dois módulos e passou smoke sem rede/mounts para esses
probes. OCI `sha256:4a6b01f49ccc4d919eb02f3932f0f95d5b99f386601863f5e206572040243a09`,
revision `5b541ca-dirty-v3-titlecase-protected-names-20260928-r29-overlay-from-r28`.
O review independente final continua pendente. O serviço ainda está em r28,
fila ociosa, sem nova inferência. Antes de outra model call: instalar/reconciliar
r29, registrar mounts/V3/Ollama, preflight read-only E14 e somente então uma
nova candidata isolada. E15+ bloqueado.

### Reconciliação r29 e novo blocker compartilhado `Hein`

Depois do registro acima, somente o serviço `transass` foi recriado com r29.
Imagem OCI `sha256:4a6b01f49ccc4d919eb02f3932f0f95d5b99f386601863f5e206572040243a09`,
revision `5b541ca-dirty-v3-titlecase-protected-names-20260928-r29-overlay-from-r28`;
health `ok`, V3/2.5.2, fila ociosa. Mounts RW de mídia/state continuam iguais;
Ollama segue running, restart 0, sem inferência. A captura E14 anterior ainda
existe no volume com SHA `e879bf5c1407b660d90ddd750cf00793115a50a76cc6a4e9d5b6f8f2d832e5dc`.
O preflight read-only E14 aprovou 1/1 na track francesa ASS textual (stream 2).

Antes de um retry, a revisão do gate identificou outro atalho no caso de
interjeição compartilhada: `Hein Banane` idêntico podia ser aceito só por Title
Case. O patch local agora exige, nessa exceção, destino pt-BR, igualdade visível
integral após normalização tipográfica limitada e que todos os outros tokens
sejam nomes protegidos não franceses; o fallback francês Title Case continua
falhando fechado. Testes focados continuam `134 passed`, `git diff --check`
limpo. r30 e revisão independente desse patch estão pendentes; r29 permanece
ativo sem nova model call. Fazer rebuild/recreate somente Transass, health/
mounts/Ollama, documentar reconciliação e repetir preflight E14 antes de retry.
E15+ bloqueado.

### E14: cópias francesas em caixa arbitrária e nome seguido de palavra francesa

Na revisão final do gate, a auditoria independente apontou duas brechas que
precediam o fallback Title Case: uma cópia francesa de palavra única ausente da
lista de indicadores (por exemplo, `Banane`) podia escapar; e o nome não
protegido após partícula podia ser confundido com sobrenome mesmo sendo uma
palavra francesa comum. O segundo caso é agora rejeitado quando o token é
stopword francesa. Após as exceções estreitas já verificadas (nomes protegidos,
chamadas de nome revisadas e interjeição compartilhada), qualquer sequência
francesa lexicalmente idêntica é bloqueada, independentemente de maiúsculas,
minúsculas ou vocabulário heurístico. A exceção de nome com partícula segue
exigindo destino pt-BR e texto visível idêntico após a normalização tipográfica
limitada.

Regressões adicionadas para cópias francesa em caixa alta/baixa, singleton
`Banane`, `Kyôko Ozaki de Banane.` e as exceções legítimas de nomes/interjeição.
As suítes offline focadas passaram `134/134`; `git diff --check` passou. A imagem
r31 foi construída offline, derivada de r30, compilou os módulos V3 e passou
oito probes sem rede nem mounts: singleton/caixa alta bloqueados, palavra
francesa após partícula bloqueada, e exceções estritas para nome protegido,
introdução nominal, `Hein` compartilhado e troca de nomes revisada preservadas.
OCI `sha256:b8a11a3e353aec20efc9aefdf0be9529721940f76fbcfd691c9a15a05298242d`,
revision `5b541ca-dirty-v3-french-identity-fail-closed-20260928-r31-overlay-from-r30`.

No momento deste registro, r31 ainda não foi instalada; Transass continua em
r29, ocioso, sem nova inferência. O review independente do snapshot corrigido
está pendente. Próxima etapa autorizada: concluir essa revisão, recriar somente
Transass em r31, confirmar V3/saúde/mounts/Ollama e repetir o preflight
read-only de E14; somente então executar uma candidata isolada. E15+ continua
bloqueado até fechamento e auditoria da E14. Nenhuma Library/publicação.

### Reconciliação r31 e preflight E14

Foi recriado somente o serviço local `transass` em
`transass:v2.5.2-local-v3-candidate-only-20260928-r31`, OCI
`sha256:b8a11a3e353aec20efc9aefdf0be9529721940f76fbcfd691c9a15a05298242d`,
revision `5b541ca-dirty-v3-french-identity-fail-closed-20260928-r31-overlay-from-r30`.
Health `ok`; `/version` confirma V3/2.5.2; status mostra fila vazia. Os mounts
RW permanecem `/PICAdeiro/data/Shows -> /shows` e
`/docker/transass/state -> /app/state`. Ollama continua running,
`restart_count=0`, `ollama ps` vazio. A captura da falha E14 r28 foi preservada
após a recriação e o SHA-256 continua
`e879bf5c1407b660d90ddd750cf00793115a50a76cc6a4e9d5b6f8f2d832e5dc`.

O novo preflight read-only para Shiki S01E14 (`episode_id=301`) aprovou 1/1
elegível: stream ASS textual francês `FR Full ASS`, índice interno 2; modelo
Qwen2.5:14b; pipeline V3. Não houve model call neste preflight. A revisão
independente encontrou e relatou os dois bypasses agora corrigidos; a última
leitura do snapshot corrigido foi solicitada, mas permanece sem resposta. Os
testes focados (134/134), build e probes isolados passaram. Prosseguir somente
com uma tentativa candidata isolada E14, Ollama-only, sem Library/publicação;
E15+ permanece bloqueado até validação completa da E14.

### E14 r31: nome romanizado isolado não inventariado

A única candidata E14 em r31 falhou no gate `V3_TRANSLATION_SOURCE_COPY:14`
após duas respostas HTTP 200 capturadas pelo transporte. Job
`720700ac755d4807a58ba5ffd8b77a4c`; captura raiz
`/app/state/v3-runs/558e2b31ef0c8017a037cb67/d2c7616840d54f792f0cd22cf2c9281d/captures/`.
Os corpos brutos SHA-256 são `d046a5265804024cf477e259a1fdecaffbff4ae765869b1d72fa506b31743e36`
(batch IDs 0–7) e `f77ad6c5583e12ae6254e77e9231976ed14f5c080774ce232ad6ec24190a241e`
(batch IDs 8–15). Unidade 14: fonte francesa `Atsushi !`, resposta `Atsushi!`.
O contexto do job só tinha `Ki` e `Shi` no inventário protegido; o gate bloqueou
corretamente cópia francesa lexical em geral, mas não tinha uma exceção para o
nome japonês isolado. Nenhuma candidata válida foi produzida ou publicada.
Telemetria atingiu pico de 83 °C; sem trip térmico.

A correção permite identidade exata em pt-BR para um único token japonês que
passa a heurística forte de romanização (`sh` em `Atsushi`) e não é stopword
francesa. Para sequências com vários tokens, ainda exige pelo menos um nome
protegido como âncora e todos os tokens como nomes protegidos ou romanizações
fortes. O primeiro teste revelou que ampliar a heurística para toda sequência
liberava trocas de nomes sem âncora; o patch foi estreitado. Regressão end-to-end
adicionada para `Atsushi !` → `Atsushi!`, com controles para frases francesas,
stopwords forçadas como nomes e destino incorreto. Suítes offline focadas:
`135 passed`; `git diff --check` limpo.

Imagem r32 construída offline sobre r31, `py_compile` e oito probes sem rede ou
mounts passaram, incluindo nome isolado, frases/stopwords francesas e troca de
nomes com/sem âncora. OCI
`sha256:9615b5d758b9598cbfc3bebd89d80b60579ca88c8f99cc8a65c5e530d84df7e6`,
revision `5b541ca-dirty-v3-unlisted-romaji-name-gate-20260928-r32-overlay-from-r31`.
Uma nova revisão independente read-only foi solicitada e permanece pendente.
Transass está ocioso em r31 após a falha; Ollama não foi reiniciado. Próximo:
revisar, instalar somente r32, confirmar mounts/saúde/Ollama e refazer preflight
E14 antes de uma nova candidata isolada. E15+ segue bloqueado.

### Reconciliação r32 e preflight E14

Após a falha r31, somente Transass foi recriado em r32. Imagem
`transass:v2.5.2-local-v3-candidate-only-20260928-r32`, OCI
`sha256:9615b5d758b9598cbfc3bebd89d80b60579ca88c8f99cc8a65c5e530d84df7e6`,
revision `5b541ca-dirty-v3-unlisted-romaji-name-gate-20260928-r32-overlay-from-r31`.
Health `ok`; versão 2.5.2/V3; fila vazia. Os mounts RW permanecem os mesmos
(`/PICAdeiro/data/Shows -> /shows` e `/docker/transass/state -> /app/state`).
As duas capturas r31 foram revalidadas no volume, com SHA preservados. Ollama
continua running, `restart_count=0`, `ollama ps` vazio.

Novo preflight read-only de Shiki S01E14 (ID 301) aprovou 1/1 elegível, track
ASS textual francesa `FR Full ASS`, stream interno 2, modelo Qwen2.5:14b e V3.
Nenhuma chamada foi feita pelo preflight. Com os 135 testes focados, oito probes
da imagem e a reconciliação concluídos, está autorizada somente a próxima
candidata isolada E14 em r32, Ollama-only/candidate-only, sem Library ou
publicação. O review independente read-only do patch r32 continua pendente;
E15+ aguarda fechamento e auditoria da E14.

### E14 r32: nome isolado `Kaori` e dois novos bypasses do gate

A candidata r32 falhou em `V3_TRANSLATION_SOURCE_COPY:98` depois de 12 lotes
reportados completos e uma resposta final rejeitada; a captura 13 contém a
unidade. Job `eff0387aaaf64ed6a2ec50a1f57ecb63`; captura
`/app/state/v3-runs/9ffe1d197f3724a88b38f3b0/4b83f06b3eb18d4582ca0131cb705300/captures/v3-retranslate-9ffe1d197f3724a88b38f3b0-000013/`.
Fonte `Kaori !`, resposta `Kaori!`; contexto do prompt havia protegido apenas
`Megumi`. SHA-256 do corpo HTTP `7cf8471464e2c6bcc5ba0aecb75c50d009c7070d0d62196dea9453f4ece423f9`.
Nenhuma candidata validada ou publicação. GPU observada até 81 °C; sem trip.

A captura sustenta a inclusão de `Kaori` como token japonês não marcado na
allowlist revisada, limitada a uma única palavra, texto exato e destino pt-BR.
Na revisão adversarial, também foram achados: `France`, `Danse` e `Chambre`
passavam como sobrenome após `de` apesar de não serem romanizações japonesas;
qualquer target contendo a substring `pt-BR` podia ativar a exceção. Agora o
token não protegido após partícula precisa satisfazer a heurística forte de
romanização; o destino só reconhece aliases pt-BR por igualdade exata. Foram
adicionados testes para os três sobrenomes, targets ambíguos, a introdução
válida `Sakaimatsu`, `Kaori` e frases francesas contendo o nome.

As suítes offline focadas passaram `136/136`; `git diff --check` limpo. Imagem
r33 construída offline sobre r32, compilação e 12 probes sem rede/mounts
aprovados (nomes, stopwords, sobrenomes, targets válidos/inválidos e chamadas
com/sem âncora). OCI
`sha256:a94d873dcabfb39771c58a8284298c799f8a9b73525af63600927b409719d436`,
revision `5b541ca-dirty-v3-reviewed-kaori-strict-target-gate-20260928-r33-overlay-from-r32`.
O review independente confirmou os bypasses e recebeu o patch corrigido para
revisão final. No fechamento deste registro, o serviço ainda está em r32,
ocioso após a falha; mounts RW preservados, Ollama running com restart 0 e sem
inferência. Próximo: instalar somente r33, verificar estado e refazer preflight
E14 antes de outra candidata isolada. E15+ permanece bloqueado.

### Reconciliação r33 e preflight E14

Somente o serviço `transass` foi recriado em r33. A imagem
`transass:v2.5.2-local-v3-candidate-only-20260928-r33` tem OCI
`sha256:a94d873dcabfb39771c58a8284298c799f8a9b73525af63600927b409719d436` e
revision `5b541ca-dirty-v3-reviewed-kaori-strict-target-gate-20260928-r33-overlay-from-r32`.
Health `ok`; `/version` confirma V3/2.5.2; fila vazia. Mounts RW continuam
`/PICAdeiro/data/Shows -> /shows` e `/docker/transass/state -> /app/state`.
A captura E14 r32 foi revalidada após a recriação e mantém SHA
`7cf8471464e2c6bcc5ba0aecb75c50d009c7070d0d62196dea9453f4ece423f9`. Ollama
segue running, `restart_count=0`, `ollama ps` vazio.

Preflight read-only E14 aprovou 1/1: `FR Full ASS`, stream interno 2, idioma
fonte francês, Qwen2.5:14b, pipeline V3. Nenhuma chamada ao modelo ocorreu no
preflight. O review independente reportou blockers concretos nos bypasses agora
corrigidos; a confirmação final do snapshot r33 ainda não chegou. Está liberada
somente uma candidata isolada E14 em r33, Ollama-only/candidate-only e sem
Library/publicação; E15+ continua bloqueado até auditoria completa da E14.

### E14 r33: fragmentos progressivos do nome Toshio

A candidata r33 falhou em `V3_TRANSLATION_SOURCE_COPY:241`. Na captura 31 do
job `9d7a0e8edfa44633911d435ec65a04e6`, a sequência fonte era `To...`,
`Toshi...`, `Toshio...`; o modelo preservou os três fragmentos, e o prompt já
identificava `Toshio` como nome próprio. Captura:
`/app/state/v3-runs/a22c184dbcd20c9ba931c61e/4eaaff05c855ba357711dfab2c70b0dd/captures/v3-retranslate-a22c184dbcd20c9ba931c61e-000031/`;
SHA-256 da resposta `8cce799e30ea3dd28e33ac4d083666f91ba920df4a928919da92ad3aa5c62852`.
Sem candidata validada ou publicação. GPU chegou a 81 °C, sem trip.

O gate comparava nomes protegidos completos com cada unidade individual, por
isso não reconhecia que `To...`/`Toshi...` eram prefixos progressivos de
`Toshio`. Foi acrescentada resolução de prefixo: apenas token único capitalizado,
reticências reais (`...` ou `…`), prefixo de pelo menos duas letras, uma única
correspondência protegida, target exato pt-BR e resposta visível integralmente
igual. O nome correspondente também é anexado ao contexto protegido da unidade.
Prefixo ausente/ambíguo, pontuação diferente, target inválido, stopword ou
reticências incompletas continuam bloqueados. Testes offline incluem integração
end-to-end com repetição contextual do nome. Suítes focadas: `138 passed`;
`git diff --check` limpo.

Imagem r34 construída offline sobre r33; `py_compile` e 12 probes sem rede ou
mounts aprovados, cobrindo prefixos, ambiguidade e todos os controles r33. OCI
`sha256:2d4718b61b046091b84d89e002532b14a69cbbcc64e200f5ec1187e22b34aca7`,
revision `5b541ca-dirty-v3-protected-ellipsized-name-fragments-20260928-r34-overlay-from-r33`.
Revisão read-only independente do patch de prefixo solicitada; ainda pendente.
O serviço permanece r33, fila terminalmente falha/ociosa; mounts RW preservados,
Ollama running, restart 0, sem inferência. Próximo: instalar somente r34,
validar health/V3/mounts/Ollama e refazer preflight E14 antes de nova candidata.
E15+ segue bloqueado.

### Reconciliação r34 e preflight E14

Após a falha r33, somente o serviço local Transass foi recriado em
`transass:v2.5.2-local-v3-candidate-only-20260928-r34`, OCI
`sha256:2d4718b61b046091b84d89e002532b14a69cbbcc64e200f5ec1187e22b34aca7`,
revision `5b541ca-dirty-v3-protected-ellipsized-name-fragments-20260928-r34-overlay-from-r33`.
Health `ok`; V3/2.5.2; fila vazia. Mounts RW continuam inalterados. Captura 31
da falha r33 foi verificada no volume, SHA
`8cce799e30ea3dd28e33ac4d083666f91ba920df4a928919da92ad3aa5c62852`. Ollama
segue running, restart_count 0, `ollama ps` vazio.

Preflight read-only Shiki S01E14 ID 301 aprovou 1/1, track francesa textual
`FR Full ASS` stream 2, Qwen2.5:14b e pipeline V3. Não foi feita chamada ao
modelo nesse passo. Testes focados 138/138 e smoke isolado r34 12/12 passaram;
review read-only independente do patch final segue pendente. Liberada somente
uma candidata E14 isolada, Ollama-only/candidate-only, sem Library/publicação.
E15+ permanece bloqueado até E14 validar.

### E14 r34: causa confirmada e correção contextual restrita

A candidata E14 r34 (`8d5932908645470cb62112ed10327400`) completou 31/31
chamadas sem fallback e falhou em `V3_TRANSLATION_SOURCE_COPY:241`; nenhum
artefato candidato foi validado ou publicado. A captura 31 permanece em
`/app/state/v3-runs/e1c2d463e8fb2f1ca1a24175/ab028ba7f54d1c1f0cabcb958ebcd7f8/captures/v3-retranslate-e1c2d463e8fb2f1ca1a24175-000031/`.
Resposta HTTP 200, request SHA-256
`c43fa84708601b18ee9fe041d57ca5640b8746c4a0b650a6dfb80834dad53168`, response
SHA-256 `86f06f7bf5c544abc7cff2c7887d02697ba4b215a68bd14d342818ffcfff3d16`.
GPU observada em 78 °C no snapshot terminal; sem trip.

O traceback e o código carregado no container foram conferidos. Não era uma
versão antiga do pipeline: r34 de fato recusou `To...` porque sua regra de
prefixo único via dois nomes franceses recorrentes iniciados por “To”:
`Tokujirô` e `Toshio`. A faixa ASS francesa (stream 2, `FR Full ASS`) confirma
que os eventos 241–243 são `To...`, `Toshi...`, `Toshio...`, mesmo estilo
`Default`, consecutivos, com intervalos de 790 ms e 630 ms. O prompt do lote
mencionava `Toshio` por causa do terceiro item; isso não tornava o primeiro
prefixo individualmente inequívoco. A validação r34 estava correta ao falhar
fechado; faltava propagar uma evidência progressiva e local entre eventos.

O patch não reduz a validação geral de cópia. Só resolve a unidade inicial se
os dois eventos ASS seguintes estiverem no mesmo lote, forem consecutivos,
mesmo estilo e com intervalos não negativos de até 2,5 s; os três textos devem
ser prefixos elípticos estritamente crescentes e o último deve coincidir
exatamente com um nome já protegido. Sem qualquer condição, o `To...` isolado
continua rejeitado. Testes focados: 140 passaram. A suíte offline completa
executou 1.136 testes aprovados, 38 deselecionados e 70 subtestes; dois testes
falharam fora deste patch: expectativa de fallback visual V2.3.8 e auditoria
de nomes próprios no fluxo web. Permanecem como falhas, sem mascaramento.

Imagem r35 construída offline sobre r34; `py_compile` passou e 12 probes em
container descartável, sem rede/mounts/modelo, passaram. OCI
`sha256:da1a6ec284f8cf8ca8765d5e6efe78df484e980a79bc363f5def0ff6d919e549`,
revision `5b541ca-dirty-v3-progressive-ellipsis-name-context-20260928-r35-overlay-from-r34`.
Revisão independente read-only foi solicitada, mas ainda não respondeu. O
serviço continua r34 até a reconciliação. Próximo: concluir revisão, instalar
somente r35, verificar saúde/mounts/Ollama e executar preflight antes de uma
única candidata E14. E15+ continua bloqueado até auditoria estrutural completa
da E14. Sem Library/publicação.

### Revisão independente do patch r35 e endurecimento

O revisor encontrou P2 antes da instalação do r35: a regra anterior ainda
permitia que um prefixo passasse quando o inventário, sozinho, retornasse uma
única correspondência. Esse atalho foi removido; a validação de cópia agora
recebe uma autorização contextual separada, produzida apenas por uma janela
completa de três eventos consecutivos no mesmo lote e estilo, intervalos de
0–2500 ms, fragmentos elípticos progressivos e nome final exato protegido.
Isso também cobre o fragmento central `Toshi...`; `Toshio...` é reconhecido
como token completo protegido. Sem a janela, prefixo único não autoriza cópia.

Foram adicionados controles para identidade sem contexto, janela dividida entre
lotes, estilo divergente, intervalo negativo e intervalo exatamente 2500 ms.
Os testes focados voltaram a passar `140/140`. A imagem r35, cuja auditoria
encontrou o P2, nunca foi instalada nem usada em nova inferência; permanece
somente como artefato local histórico. O serviço continua em r34, terminal e
ocioso; Ollama sem restart/inferência. O follow-up de revisão read-only do
patch estrito e a construção da próxima imagem ainda estão pendentes. E15+
permanece bloqueado; sem Library/publicação.

### Candidata estrita r36

Após remover o atalho de prefixo único, testes focados passaram `140/140` e
`py_compile` passou. A suíte offline completa permanece em `1.136 passed`,
`2 failed`, `38 deselected`, `70 subtests`; as duas falhas já descritas são
fora deste patch e continuam visíveis. r36 foi construída offline sobre r35,
sem rede no build, e passou 17 probes em container descartável sem mounts ou
modelo. OCI `sha256:dc58f1825b421efa492196f0e8e8d881e82d48277e02f86a3c5368481571405a`,
revision `5b541ca-dirty-v3-progressive-ellipsis-strict-context-20260928-r36-overlay-from-r35`.
Os probes confirmam que nome único não autoriza cópia, os fragmentos inicial e
central dependem da janela tripla, e lote/estilo/tempo inválidos falham
fechado. O reviewer recebeu follow-up read-only, sem resposta até aqui; o
finding P2 original foi tratado. Serviço continua r34, mounts inalterados,
Ollama running/restart 0/sem inferência. Antes da próxima chamada: instalar só
r36, verificar estado e refazer preflight E14. E15+ bloqueado; sem
Library/publicação.

### r39 instalado e E14 elegível

r39 foi instalada apenas em Transass (`--no-deps`), digest
`sha256:b7288a493c05d42aec6ac86a1fd08083bd4c6dcd643488383dbf58feb6b2fe82`;
health healthy, V3/2.5.2, fila vazia e ambos os bind mounts RW intactos. Ollama
continua sem restart e sem modelo carregado. Preflight read-only de E14 aprovou
1/1 para ASS interno francês track 2 e Qwen2.5:14b. Review final independente
após correções ainda não respondeu e não é afirmado como PASS; a continuação
autorizada permanece limitada a uma candidata isolada, sem fallback ou
publicação/Library.

### Novo blocker CES e interrupção r39

Review independente encontrou que contagem de ocorrências não basta: em
`Les membres du CES commentent ces photos.` → `Os membros comentam CES fotos.`,
a única `CES` de saída podia substituir o demonstrativo embora a fonte tivesse
um acrônimo `CES` em outra posição. A isenção precisa parear contexto/posição,
não somente contagem.

O job candidato E14 r39 foi parado cooperativamente assim que o achado chegou:
`8248a120217f4653b42d2b3adcbe0105`, sessão
`438f7f966e7c4b36a28e29d20d071e1c`, run `0b0d66da5a8fc9d25a369438`, operação
`915178bd5babc24fd9b39f53caa8bf54`; `CANCELLED`/`STOPPED`, código `-15`, 18
chamadas iniciadas e 17 unidades completas. Capturas 1–17 duráveis e captura
18 sem resposta HTTP durável, tudo preservado em
`/app/state/v3-runs/0b0d66da5a8fc9d25a369438/915178bd5babc24fd9b39f53caa8bf54/captures/`.
Sem resultado final/diagnóstico, Library ou publicação. Não reiniciar Ollama.

A correção local pareia `CES` da saída com contexto fonte por ocorrência
(inclui o controle `du` → `do`), com regressão para o contraexemplo deslocado.
Focados: 126 passaram; suíte completa estava rodando neste registro. Nenhuma
nova inferência até suíte, review final, imagem e preflight reconciliados.

### Estado r39 offline

A imagem r39 foi construída offline sobre r38, digest
`sha256:b7288a493c05d42aec6ac86a1fd08083bd4c6dcd643488383dbf58feb6b2fe82`;
compilação Python no build passou. Smoke em container descartável isolado, sem
rede/volumes/modelo, passou 10 assertions dos três blockers. O review read-only
da versão final ainda não respondeu; não é tratado como aprovado. Não instalei
r39 nem iniciei nova chamada de modelo/job após cancelar E14 r38. Capturas
preservadas; sem Library/publicação.

### Reconciliação r36 e preflight E14

Somente o serviço local Transass foi recriado em r36. Health `ok`, `/version`
confirma V3/2.5.2 e revision r36; fila vazia. Os mounts RW continuam
`/PICAdeiro/data/Shows -> /shows` e `/docker/transass/state -> /app/state`.
Ollama segue `running`, `restart_count=0`, `ollama ps` vazio.

Preflight read-only do episódio Shiki E14 (ID 301): elegível 1/1, fonte
francesa `FR Full ASS`, track textual interna 2 (`codec=ass`, `language=fre`),
modelo Qwen2.5:14b e pipeline V3. O preflight não chamou o modelo. Está
autorizada somente uma candidata isolada E14 via app, `candidate_only=true`,
`ollama_only=true`, sem fallback e sem Library/publicação. A auditoria final
read-only do segundo patch ainda não respondeu; o P2 original está corrigido e
coberto pelos testes/probes. Nenhuma nova inferência ocorreu depois da falha
r34 até aqui. E15+ continua bloqueado até conclusão/auditoria E14.

### E14 r36: falha estrutural, evidência e correções determinísticas

A única candidata autorizada r36 (`a62a1e5b28f5421d8bc8c5a823340014`, run
`462a20c5b17afb73a966e9f5`, operação
`adebb5245f9ae3079e8520f610667377`) completou 36 chamadas a Qwen2.5:14b,
sem fallback. A execução terminou em falha na validação estrutural dos eventos
197 e 252; não produziu candidata válida e não escreveu na Library.

Na captura 25, o original `L'EEG commence\Nà réagir régulièrement.` virou
`O EEG\Ncomeça a reagir regularmente.`. `EEG` é um acrônimo preservado
completo; a quebra está entre tokens completos, mas o validador tratou o
acrônimo curto como fragmento. Resposta bruta SHA-256
`2428edee052a97e6db13f88f7961cd48d512abe46865a3f5749dc11e7006593b`.
Na captura 32, `C'est comment éliminer\Nces Shi Ki.` virou
`É como eliminar\Nces Shi Ki.`. A quebra também está entre palavras, mas
`ces` é o demonstrativo francês não traduzido; essa é uma lacuna lexical real,
não motivo para relaxar a validação de quebra. Resposta bruta SHA-256
`f7aa950c8701e3ee368e3b066c5f9ab5521a1088bfb76eb0886db019b7af0d1f`.
As respostas e requests originais seguem no volume de state, em
`/app/state/v3-runs/462a20c5b17afb73a966e9f5/adebb5245f9ae3079e8520f610667377/captures/`.
Telemetria registrada abaixo de 90 °C (pico aproximado 83 °C), sem trip.

O patch atual acrescenta um risco pt-BR estreito para o demonstrativo francês
`ces`, com orientação contextual para traduzi-lo e preservar o nome seguinte;
a regressão inclui uma correção direcionada simulada e confirma que a resposta
reparada deixa de sinalizar o risco. Para a quebra, a validação passa a aceitar
somente tokens uppercase curtos comprovados no mesmo texto-fonte do evento
(por exemplo, `EEG`). A regra geral permanece: sem essa evidência, `EEG` curto
na borda continua sinalizado; `EE\NG` continua proibido mesmo quando `EEG`
existe na fonte. A auditoria read-only foi alinhada com o mesmo contexto
fonte-evento, sem ampliar a exceção.

Verificação após o patch: os testes offline direcionados registraram 107
passados e 1 excluído para isolar a falha preexistente de nomes próprios. A
suíte offline completa registrou 1.140 passados, 2 falhas preexistentes (teste
de fallback visual V2.3.8 e teste de auditoria de nomes próprios), 38
deselecionados e 70 subtestes. Revisão independente read-only pendente. A
imagem ainda não foi reconstruída nem instalada e nenhuma nova chamada ao
modelo foi feita desde a falha r36. Próximo gate: concluir review, reconstruir
offline, testar em container descartável, instalar somente Transass,
revalidar mounts/saúde/Ollama e executar preflight read-only antes de uma única
nova candidata E14. E15+ permanece bloqueado; sem Library/publicação.

### Reconciliação r37 antes de retry

Imagem candidata local r37 construída offline sobre r36 e instalada somente
no serviço Transass, OCI
`sha256:5ce8251210b7a73493c658d6502ec4113bb16024fe6d1a6afad49e1ead12a32b`,
revision `5b541ca-dirty-v3-ces-residue-source-acronym-break-20260928-r37-overlay-from-r36`.
Health healthy, `/version` V3/2.5.2, `/status` sem job/fila; mounts RW de mídia
e state preservados. Ollama permaneceu running, restart_count 0 e `ollama ps`
vazio. A imagem passou 9 assertions de smoke em container descartável com
`--network=none`, sem mounts/modelo. Preflight read-only E14 aprovou 1/1 na
track `FR Full ASS`, stream 2, francês, Qwen2.5:14b/V3; não criou job nem
chamou o modelo. Revisão independente read-only do patch foi reencaminhada em
modo prioritário e ainda aguarda resposta. Não houve model call após a falha
r36. Próximo gate permanece parecer, seguido por uma única candidata isolada
E14; sem fallback e sem Library/publicação. E15+ bloqueado.

### Reconciliação obrigatória após candidata r37

`CANONICAL_STATE_RECONCILIATION_REQUIRED`: o review independente do patch r37
encontrou dois blockers antes do próximo retry: o risco francês `ces` também
podia sinalizar/reparar indevidamente o acrônimo `CES` (`Ces photos du CES
arrivent.` → `Essas fotos do CES chegaram.`); e o léxico global que permite
`dez` como palavra curta aceitava a quebra real `dez\Nembro` de `dezembro`.
Não se reutiliza r37 até ambos os casos terem cobertura e correção.

Apesar do pedido de parada ter sido feito assim que o reviewer respondeu, o
worker E14 r37 já estava concluindo: 36/36 respostas Qwen2.5:14b foram duráveis,
sem fallback. O pipeline V3 terminou, mas o app reprovou a auditoria final; o
job `86e9053246de44ae9348b718d19f7e88` ficou `CANCELLED`/`STOPPED`, conservando
o erro e o diagnóstico. Não houve registro de legenda nem publicação. O
candidato de diagnóstico SHA-256 é
`0c57a77e600cb39296cef7b80875868f98dfb08c5c9b49efed3adb0a29595cc3`; o arquivo
`diagnostic.json` tem SHA-256
`dc96cb4e43659f37b9d23e21518c851812e48eece637b606e2628ebb5e36140b`. Ambos
foram preservados em
`/app/state/anime-subtitle-library/diagnostics/retranslation-86e9053246de44ae9348b718d19f7e88/`.
É uma escrita diagnóstica imutável gerada pelo fail-safe em state sob
`anime-subtitle-library/diagnostics`, fora do catálogo consumível; o job marca
`candidate_only=true`, `published=false`. Não apagar essa evidência.

A auditoria encontrou `LINE_BREAK_INSIDE_WORD`,
`POSSIBLE_UNTRANSLATED_OUTPUT` e `UNBALANCED_DELIMITERS`. Eventos 0 (`Les Shi
Ki\Ntraquent...` → `Os Shi Ki\Ncaçam...`) e 10/148/191/241/242 (`SHI KI`,
`Kyôko`, `Kyôko Ozaki`, `To...`, `Toshi...`) revelam falsos positivos do
auditor para nome próprio/fragmento reconhecido pelo fluxo V3. Eventos 233–234
mostram defeito real: o modelo acrescentou aspas finais não presentes na fonte,
alterando a posse das aspas entre eventos e causando o bloqueio fatal. A última
resposta durável está em run `aa9749e63450dcbdf8bd803e`, operação
`d37574d20d0a158b4df01f83d0df6263`, captura 36, SHA-256
`18cf2032aa4a5cd70042f194e2071090ee336f4225cfb24665a9ce407409890b`.

Até reconciliar esses achados em regra, testes, documentação e imagem, ficam
bloqueados novas model calls, jobs, Library, promoção e publicação. Próximo
patch deve: (a) distinguir demonstrativo `ces/Ces` de acrônimo `CES`; (b) manter
`dez casos` válido mas rejeitar `dez\Nembro`; (c) dar à auditoria a mesma
evidência estrita de nomes e fragmentos progressivos usada pelo V3, sem
suprimir cópia de diálogo; (d) preservar o número/posse de delimitadores por
evento e solicitar reparo direcionado antes do write final. Testes e revisão
independente read-only precedem build/reconciliação; um novo retry E14 segue
candidate-only/Ollama-only, sem Library/publicação. E15+ permanece bloqueado.

### Adendo r37 — correções locais sob validação

As correções propostas foram implementadas localmente, sem alterar ou apagar a
evidência r37: risco contextual `ces/Ces` versus `CES`, proteção para o split
`dez\Nembro` mantendo `dez casos`, contexto de nomes/siglas limitado à fonte
do evento na auditoria e verificação/reparo de delimitadores por evento no
V3. O reparo cobre aspas, parênteses e colchetes e não transfere delimitadores
entre falas.

Testes direcionados: 122 passaram e 1 teste conhecido de nomes próprios foi
isolado. Suíte offline completa: 1.146 passaram, 2 falhas conhecidas (fallback
visual V2.3.8 e classificação ampla de nomes próprios), 38 deselecionados e 70
subtestes. `git diff --check` passou. A revisão independente read-only segue
pendente; imagem/runtime r38 ainda não foram construídos/reconciliados. O
estado continua `CANONICAL_STATE_RECONCILIATION_REQUIRED`, sem novas chamadas
de modelo/jobs e sem promoção/publicação/Library. Próximos gates: parecer
independente, build/smoke offline, atualizar apenas Transass e verificar
saúde/mounts/Ollama, então preflight read-only de E14. E15+ continua bloqueado.

Reauditoria offline da candidata diagnóstica r37, preservada, usando a faixa
francesa ASS do E14: os hashes do texto-fonte coincidem em todos os 283/283
eventos com os hashes registrados pela auditoria original. A versão local do
auditor não sinaliza mais quebra ou cópia de nomes; ainda bloqueia pelos dois
eventos com aspas extras (233–234), como esperado. É uma verificação sobre
evidência antiga, não uma candidata corrigida ou aprovada; não fez inferência,
chamada ao modelo, gravação em Library nem alteração de state.

### Gate r38 — estado imediatamente antes da instalação

O parecer independente não retornou após reenvios e não é considerado PASS.
Foi feita revisão manual local, sem alterações de arquivos pelo auditor. A
imagem candidata r38 foi construída sem rede sobre r37, digest
`sha256:1a453dca064b1476e443988e6600ba0fcb1bd6fd364ebe0c9850e184206ae257`;
smoke descartável sem rede, volumes ou modelo passou cinco assertions. O
container r37 segue healthy e ocioso, com volumes RW intactos. Ollama permanece
running, `restart_count=0`, sem modelo carregado. A atualização planejada
substitui somente Transass (`--no-deps`); ainda não ocorreu model call após r37,
nem promoção/publicação/Library.

### Atualização posterior — review e r38 interrompida

O review independente retornou três riscos concretos: `CES` podia ser aceito
como resíduo `ces` sem ocorrência comprovada na fonte; delimitadores com
contagem igual mas ordem inválida (`(texto)` → `)texto(`) passavam pelo gate;
uma sigla da fonte podia reabrir o split `dez\Nembro`. Esses achados reativam
`CANONICAL_STATE_RECONCILIATION_REQUIRED` e bloqueiam nova inferência até patch,
testes, review, imagem e preflight.

O único job E14 r38 em execução foi parado cooperativamente ao receber o review:
job `da47fcedf3f84b99b3d838bb07506e94`, sessão
`ad9034bda13d4040b396a5ce977c9f4b`, run `1f9eab7e669ab942087ea175`, operação
`4abc7abb02dd8e2769ff2a12350d05f5`; estado `CANCELLED`/`STOPPED`, código `-15`,
20 chamadas iniciadas/19 unidades concluídas. Capturas 1–19 estão duráveis; a
20 preserva request/state sem resposta HTTP durável em
`/app/state/v3-runs/1f9eab7e669ab942087ea175/4abc7abb02dd8e2769ff2a12350d05f5/captures/`.
Não houve resultado final, diagnóstico de candidata, escrita na Library ou
publicação. Ollama não foi reiniciado e segue com o modelo carregado até unload
normal. Preservar todas as capturas.

### Reconciliação do patch após r38

O patch local agora exige evidência de ocorrência uppercase `CES` na fonte
antes de isentar a forma na saída; valida também sequência exata dos
delimitadores, não só suas quantidades; e avalia a concatenação conhecida
`dezembro` antes de aceitar token curto uppercase da fonte. Foram adicionados
testes para fonte `ces` → saída `CES`, delimitadores `(...)` reordenados com
contagem igual e `dez\Nembro` na presença da permissão `DEZ`.

Resultados: 126 testes focados passaram (1 teste conhecido foi isolado); suíte
offline 1.150 passou, 2 falhou por casos já conhecidos, 38 deselecionados, 70
subtestes; `git diff --check` passou. Review independente read-only dessa nova
versão ainda pendente. O serviço r38 está instalado, mas bloqueado para novo
job/model call até parecer, imagem r39, smoke e preflight. Nenhuma chamada de
modelo ocorreu após a parada do job r38; capturas preservadas, sem
Library/publicação.
