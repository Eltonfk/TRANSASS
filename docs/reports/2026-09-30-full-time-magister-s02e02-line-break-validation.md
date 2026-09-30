# Full-Time Magister S02E02 — falso positivo de quebra de palavra

Data: 2026-09-30, horário do operador `America/Recife`.
Base Git: `main`, `e4ad5efb77c729844c7086f72f22166608c53e55`.
Esta correção permanece no working tree; não foi feito commit ou push.

## Incidente e evidência reconciliada

O trabalho `3977ad1cbdf646e3ad967bc84c0302ac`, de
`Full-Time Magister - S02E02 - Blood Color Alert HDTV-1080p.mkv`, terminou seus
28 lotes e falhou na validação estrutural às 09:19:46:

```text
Validação estrutural falhou:
['evento 146: LINE_BREAK_INSIDE_WORD', 'evento 183: LINE_BREAK_INSIDE_WORD']
```

Os índices internos começam em zero. A fonte é uma faixa SubRip no índice 2 do
MKV, não uma faixa PGS. O conteúdo original nesses eventos é inglês, embora o
trabalho estivesse configurado com `source_language=desconhecido`.

As respostas já capturadas pelo Qwen2.5:14b continham palavras completas:

| Evento | Tradução capturada | Diagnóstico |
| --- | --- | --- |
| 146 | `No final, a Corte Sagrada\Nirá descobrir sobre nós.` | `irá` é uma forma verbal completa. |
| 183 | `A Cúria Negra criou a Primavera da\NIra que se assemelha à Primavera Sagrada.` | `ira` é um substantivo completo. |

O léxico de palavras curtas do `ass_engine.py` não reconhecia esses dois tokens.
Não houve perda de tradução nesses eventos: a classificação da quebra era um
falso positivo. A fila continuou e concluiu os episódios 03 a 12.

Capturas históricas preservadas, sem reescrita:

```text
/docker/transass/state/v3-runs/b89b6d6204eba1d6715f4089/primary/captures/
```

Essa execução operacional, posterior ao baseline documental anterior, foi
reconciliada neste registro aditivo. A execução passada não foi tratada como
autorização para novas chamadas de modelo ou publicação.

## Correção mínima

- Inclusão de `irá` e `ira` no léxico central de palavras completas.
- Proteção de fusões conhecidas que poderiam tornar-se falsos negativos após
  essa inclusão, como `ment\Nira`, `prime\Nira` e `defin\Nirá`.
- Preservação de fronteiras legítimas, como `ira\Ndo` e `ira\Ndos`.
- Regressões com as duas frases reais, capitalização, `\n`, tags adjacentes e
  fragmentações inválidas. Os testes de pipeline exigem que resultados
  rejeitados não criem arquivo final.

Nenhuma flag foi removida, nenhuma validação foi desligada e não houve mudança
no transporte, parser ou contrato de publicação. O detector continua sendo uma
heurística lexical limitada, não um dicionário completo de português.

## Recuperação offline e validação

O vídeo foi lido sem alteração e a faixa original extraída para `/tmp`. O replay
somente leitura consumiu as mesmas 28 capturas, sem acesso ao modelo e sem
alterar o ledger real. Foram conferidos:

- Estado durável e HTTP 200 de cada resposta.
- SHA-256 do pedido canônico e dos bytes da resposta.
- Igualdade do pedido reconstruído com o pedido capturado.
- Igualdade entre resposta parseada e conteúdo do corpo original.

Antes da correção, o replay reproduziu exatamente os erros 146/183. Após a
correção, o V3 concluiu com **219 eventos na fonte e na saída**, ambas as
validações aprovadas e a quantidade de quebras preservada em todos os eventos.
Uma comparação estrutural adicional também passou usando evidência local da
fonte para o nome `Yue` no evento 116; nenhuma exceção global para esse nome foi
adicionada.

As palavras de todas as traduções permaneceram iguais às respostas capturadas.
Os eventos 40 e 168 receberam somente o reflow determinístico já existente,
restaurando a quantidade de linhas da fonte sem nova geração.

Gate final: **1.244 testes offline aprovados**, 38 deselecionados pela política
vigente, 70 subtestes aprovados, em 52,68 segundos. Revisão independente da
mudança lexical: aprovada no escopo; casos negativos continuaram bloqueados.

Artefatos isolados:

```text
/tmp/transass-e02-20260930-SxPzAJ/
  original.srt
  before-fix-report.json
  ready-to-review.pt-BR.ass
  ready-to-review-report.json
  replay_e02.py
  final-tests.log
```

SHA-256 da fonte extraída:
`67140bd775df6977ae6af9ec6511655286076df5fb3fd22567f090e625071550`.
SHA-256 da candidata recuperada:
`3f1f25659f80d2ff69113237d56073c44c14970ddb9145c8fa1169af97f4e325`.

A candidata não foi arquivada nem publicada. O trabalho histórico que falhou não
foi artificialmente convertido em sucesso. Estas provas cobrem preservação e
estrutura; não certificam a qualidade linguística absoluta de todas as falas.

## Atualização Docker autorizada após a fila

O operador autorizou atualizar somente depois de terminar a fila. Antes da
troca foram confirmados `running=false`, zero trabalhos em execução e zero
aguardando: **10 concluídos e 1 falha** nessa sessão. Não foi enviada parada,
pausa ou nova tradução; o Ollama não foi reiniciado.

Um build completo em modo sem rede não concluiu por falta de cache do download
de FFmpeg. Essa imagem não foi usada. Para a atualização mínima, os **71 módulos
Python** da imagem em uso foram comparados com o repositório: somente
`ass_engine.py` diferia. Foi então construída uma imagem derivada offline,
preservando todas as dependências existentes e copiando somente esse módulo.

- Base preservada: `transass:v2.5.2-local-v3-unified`, imagem
  `sha256:dde268cf564afb0cadb2a0dbe12dcde4bd3f21a6b31e8378e33ff41c7b074dab`.
- Imagem aplicada: `transass:v3.0.0-local-e02-20260930`.
- ID: `sha256:291c4d0369ad1a9b85a18fbe22ff439fe86440d20c245355d0fc34e6a2fa9646`.
- Identidade de build, explicitamente não representando um commit novo:
  `e4ad5efb77c729844c7086f72f22166608c53e55-worktree-e02-50bbf50bb7ee`.
- Início do contêiner atualizado: **10:06:05** (`13:06:05 UTC`).
- Seleção da imagem persistida em `/docker/transass/.env`; somente
  `TRANSASS_IMAGE` foi alterado. Backup protegido:
  `/docker/transass/.env.before-e02-20260930`.

Os mesmos binds foram mantidos:
`/PICAdeiro/data/Shows:/shows:rw` e `/docker/transass/state:/app/state:rw`.
A requisição de GPU, usuário, limites e configuração operacional foram
preservados. Os caminhos `/PICAdeiro` e `/Tank` foram conferidos contra a
identidade do vídeo antes da troca; nenhuma mídia foi movida ou renomeada.

Após a atualização: `/health` respondeu `ok`, `/version` identificou a nova
imagem e V3, o teste lexical dentro do contêiner passou e o SHA-256 do módulo
implantado coincidiu com o fonte corrigido:
`f2ea9604ac12dad41d53fa45564482848e5eca91bca8c858d006c18e5ea2983b`.
As contagens do acervo antes/depois foram idênticas e os trabalhos históricos
foram preservados. A nova sessão da fila iniciou vazia, sem repetir trabalhos.

Não houve publicação na Library, chamada nova de modelo, limpeza de evidências,
remoção de imagens históricas, commit, push ou alteração de release.
