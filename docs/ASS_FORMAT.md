# ASS no Transass: formato, tags e regras de preservação

Esta é a referência operacional do Transass para arquivos `.ass` e `.ssa` com
typesetting. Ela combina a documentação do Aegisub com a análise de legendas
reais do projeto. A regra central é simples: o modelo traduz palavras; o
programa protege o palco inteiro em que elas aparecem.

## O que é ASS

ASS (Advanced SubStation Alpha) é um formato de legenda orientado a eventos.
Além de texto e tempo, ele pode carregar estilos, camadas, fontes, posições,
movimentos, máscaras, desenhos vetoriais, transformações e efeitos de karaoke.

As seções mais importantes são:

- `[Script Info]`: resolução lógica, modo de quebra, matriz de cor e metadados;
- `[V4+ Styles]`: estilos base, incluindo fonte, tamanho, cores, borda, sombra,
  alinhamento e margens;
- `[Events]`: linhas `Dialogue` e `Comment`, com tempo, camada, estilo,
  metadados e payload visível;
- `[Fonts]` e `[Graphics]`, quando a legenda embute recursos auxiliares.

O formato de evento normalmente é:

```text
Dialogue: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
```

O último campo, `Text`, contém tanto o texto linguístico quanto os blocos de
override ASS. Vírgulas anteriores ao décimo campo pertencem à estrutura do
evento; vírgulas no payload devem continuar no campo `Text`.

## Dois mundos no mesmo payload

Uma linha ASS mistura duas camadas que o tradutor precisa separar:

```text
{\an8\pos(640,80)\fad(200,200)}The title
```

- `{...}` é um bloco de override: instruções de renderização;
- `The title` é o payload linguístico;
- o Transass pode traduzir o payload;
- o Transass deve preservar o override, o tempo, a camada e o estilo.

Tags não reconhecidas dentro de um bloco podem ser ignoradas pelo renderizador.
Por isso, texto produzido pelo modelo nunca deve ser usado como autoridade para
reconstruir tags: uma tag perdida pode ser silenciosa e visualmente catastrófica.

Blocos entre chaves sem uma tag reconhecida também aparecem em fontes reais como
anotações de edição ou metadados do typesetting. Eles continuam sendo blocos
opacos da fonte: não são enviados ao modelo, não são traduzidos e não são
descartados só porque o parser não conhece o comando interno.

## Tags relevantes para o Transass

### Texto e quebra

| Tag | Função | Regra no tradutor |
|---|---|---|
| `\N` | Quebra rígida, independente do modo de wrap | Preservar quantidade e posição estrutural |
| `\n` | Quebra suave, dependente de `\q` | Não converter automaticamente em `\N` |
| `\h` | Espaço não quebrável | Preservar como parte do envelope |
| `\q0`–`\q3` | Política de quebra da linha | Vem da fonte/estilo |
| `\r` / `\rEstilo` | Restaura o estilo atual ou um estilo nomeado | Nunca deixar o modelo inventar |

`\N` não é apenas uma nova linha estética: ele pode separar segmentos
linguísticos que têm posição, fonte ou animação própria. O reconstruidor deve
usar a legenda original como molde.

### Fonte e aparência

| Família | Tags |
|---|---|
| Fonte | `\fn`, `\fe` |
| Tamanho/escala | `\fs`, `\fscx`, `\fscy`, `\fsp` |
| Estilo | `\b`, `\i`, `\u`, `\s` |
| Rotação/distorção | `\fr`, `\frx`, `\fry`, `\frz`, `\fax`, `\fay` |
| Borda/sombra | `\bord`, `\xbord`, `\ybord`, `\shad`, `\xshad`, `\yshad` |
| Desfoque | `\be`, `\blur` |
| Cor | `\c`, `\1c`, `\2c`, `\3c`, `\4c` |
| Transparência | `\alpha`, `\1a`, `\2a`, `\3a`, `\4a` |
| Alinhamento | `\an1`–`\an9` e a forma legada `\a` |

As cores ASS usam ordem BGR, não RGB de HTML: `&H<bb><gg><rr>&`. Na
transparência, `00` é totalmente opaco e `FF` é totalmente invisível.

### Geometria, posição e máscaras

| Tag | Função | Risco |
|---|---|---|
| `\pos(x,y)` | Posição fixa | Alteração desloca a legenda |
| `\move(...)` | Movimento linear | Tempos são relativos ao início do evento |
| `\org(x,y)` | Origem de rotação | Pode estar longe do texto de propósito |
| `\clip(...)` | Mostra apenas a área delimitada | Pode ser retangular ou vetorial |
| `\iclip(...)` | Oculta a área delimitada | Inverter com `\clip` muda o resultado |
| `\fad(...)` | Fade simples | Tempos em milissegundos |
| `\fade(...)` | Fade com múltiplos estados | Exige preservar todos os parâmetros |

Uma linha não deve acumular tags mutuamente exclusivas como `\pos` e `\move`
ou `\clip` e `\iclip`. O Transass não deve “corrigir” criativamente uma fonte
que já usa uma construção específica; deve preservar e sinalizar divergências.

### Transformações animadas

`\t(...)` anima modificadores de estilo, geometria e efeitos ao longo do tempo.
Pode conter tags como `\fs`, `\fscx`, `\fscy`, cores, alpha, rotação, borda,
sombra, blur e certos tipos de clip.

O parêntese da transformação pertence à tag, enquanto parênteses no texto
pertencem ao conteúdo linguístico. Um parser que apenas conte caracteres pode
confundir os dois. A validação deve remover o envelope ASS antes de auditar o
texto e comparar o envelope original separadamente.

### Karaoke e desenho vetorial

| Família | Função | Regra no Transass |
|---|---|---|
| `\k`, `\K`, `\kf`, `\ko` | Duração de sílabas de karaoke | Preservar; não retimar nesta etapa |
| `\kt` | Início relativo de uma sílaba | Preservar e sinalizar compatibilidade especial |
| `\p0` | Desliga modo de desenho | Marca fim do payload vetorial |
| `\p1` ou maior | Ativa desenho vetorial e define escala | Payload não é texto traduzível |
| `m`, `n`, `l`, `b`, `s`, `p`, `c` | Comandos vetoriais | Preservar byte a byte quando possível |

As durações da família `\k` são expressas em centésimos de segundo. O valor
`100`, por exemplo, representa um segundo. Os comandos vetoriais podem parecer
palavras (`m`, `l`, `b`), mas dentro de `\p1` são geometria, não inglês.

## Contrato de tradução

O fluxo seguro é:

```text
ASS original
  → parser e inventário do envelope
  → classificação de texto, música, romaji, desenho e efeito
  → tradução somente do payload elegível
  → re-envelopamento determinístico pela fonte
  → validação estrutural e de cobertura
  → checkpoint
  → publicação
```

O modelo não é autorizado a produzir:

- tags ASS;
- tempos, camadas, estilos ou margens;
- comandos vetoriais;
- IDs de evento ou placeholders técnicos;
- quebras técnicas novas;
- conteúdo de eventos vizinhos.

O modelo pode produzir apenas o texto linguístico solicitado, em PT-BR quando
o conteúdo-fonte for inglês. Letras OP/ED em inglês devem ser traduzidas mesmo
quando o nome do estilo sugerir japonês, romaji ou `Karaoke Translation`.
Romaji, desenho, efeitos e créditos permanecem preservados quando a
classificação confirmar que não são conteúdo linguístico a traduzir.

### Espaços nos limites de tags

Espaço visível também faz parte do envelope. Compare:

```text
How {\c&HE8A070&}Beetles
Como os {\c&HE8A070&}Besouros
```

A versão sem espaço (`os{tag}Besouros`) cola duas palavras e pode fazer o
renderizador aplicar a transição de cor no ponto errado. O reconstruidor
compartilhado aloca as palavras traduzidas por blocos linguísticos e conserva o
espaço inicial/final de cada bloco. Isso cobre tanto títulos com uma tag por
palavra quanto animações com uma tag por caractere.

Uma legenda de referência do projeto contém 964 eventos, centenas de tags por
caractere, transformações `\t(...)` com parênteses internos e desenhos `\p1`.
Esses padrões reforçam a regra: preservar o bloco original como unidade opaca e
alterar somente o payload linguístico comprovadamente elegível.

### Implementação compartilhada

As regras básicas ficam em `src/subtranslate/ass_structure.py` e são usadas
pelos componentes históricos V2.1.x, pelo V2.3.8 e pela camada de karaokê
V2.3.0. O helper `replace_source_payload` é a única rota compartilhada para
reconstruir o texto em torno das tags; os adaptadores apenas o delegam. Isso
evita que cada pipeline interprete ASS de uma maneira diferente. A mudança é
compatível com as legendas já concluídas: elas não são reescritas
automaticamente; a proteção passa a valer em novas traduções, retries e
validações futuras.

O módulo compartilhado registra e preserva:

- `\N` e `\n` com o tipo original de quebra;
- `\h` como espaço não separável, com restauração por posição projetada;
- desenhos vetoriais em modo `\p1+`, fora do caminho linguístico;
- `\k`, `\K`, `\kf`, `\ko` e `\kt` como controles de karaoke;
- tags ASS fora do payload enviado ao modelo.

## Perfil aplicado: `Legend of the Galactic Heroes — E110`

A legenda anexada é um bom teste de compatibilidade legada: foi gerada pelo
Aegisub 2.1.8, usa `ScriptType: v4.00+`, tem 468 entradas de evento (447
diálogos e 21 comentários) e reutiliza o estilo `Karaoke Translation op` para
funções diferentes.

| Padrão observado | Quantidade | Regra aplicada |
|---|---:|---|
| Eventos com tags ASS | 176 | copiar o envelope da fonte, nunca o da resposta do modelo |
| Eventos com `\N`/`\n` | 320 | conservar o tipo, a ordem e inclusive quebras consecutivas |
| Controles de quebra | 381 | `\N\N\N` pode representar espaçamento visual, não três frases |
| Eventos no estilo `Karaoke Translation op` | 21 | estilo é pista; `Name`, conteúdo e tipo do evento decidem |
| Linhas de música (`SONG:*`) elegíveis | 8 | traduzir o inglês para PT-BR |
| Elenco, créditos, título e notas no mesmo estilo | 13 | preservar como não lírico |
| Eventos vetoriais `\p` e espaços `\h` | 0 nesta fonte | manter o fallback geral para outras legendas |

Há linhas com dois blocos de override consecutivos, por exemplo `\a6`, `\pos`,
`\c` e `\3c`, além de payloads com pontuação separada por espaço (`life ?`).
O reconstruidor agora mantém os blocos de estilo, recompõe as quebras a partir
da fonte e deixa a pontuação linguística para a tradução — evitando resultados
como `vida? ?` sem abrir mão da validação estrutural.

Comentários também podem conter texto em inglês, nomes e anotações de timing.
Eles continuam comentários: não entram na fila de tradução nem são usados como
evidência de que todas as 21 linhas do estilo são músicas. Assim, o nome
“Karaoke Translation” deixa de ser um passe livre e volta a ser apenas uma
pista — como deve ser em uma legenda que gosta de pregar peças.

## O que a legenda analisada revelou

A cópia forense de `Your Lie in April - S01E01` usada no Docker contém um caso
de typesetting muito mais denso que uma legenda de diálogo comum:

| Medida observada | Quantidade | Interpretação |
|---|---:|---|
| Eventos ASS | 14.310 | Não equivale a 14.310 frases |
| Unidades semânticas | 9.278 | Há agrupamento e reutilização visual |
| Eventos de desenho `\p` | 6.437 | São vetores, não tradução |
| Eventos com posição/clip | 13.940 | Envelope visual sensível |
| Grupos com intervalo compartilhado | 2.283 | Camadas simultâneas |
| Maior sobreposição no mesmo intervalo | 40 eventos | Não ordenar só pelo texto |
| Ocorrências de `\k` | 173 | Karaoke com timing próprio |
| Eventos de um caractere | 5.186 | Muitos são efeitos/letters de typesetting |
| Campos `Effect` preenchidos | 5.130 | Metadado útil, mas não prova semântica |
| Unidades OP/ED em inglês | 35 | 24 textos únicos |
| Textos repetidos entre camadas ED | 11 | Pode compartilhar tradução, não envelope |

O mesmo texto do ED aparece nas camadas `ED` e `ED top`. A otimização correta é
traduzir uma vez e aplicar o resultado nos dois eventos, preservando tags,
camada, tempo e estilo de cada um. Não é correto deduplicar removendo uma das
linhas: as duas podem ser necessárias para o efeito visual.

## Falha conhecida e prevenção

Nesta legenda, os eventos semânticos 3813 e 3814 formam uma placa em duas
linhas simultâneas:

```text
3813: (Structure may be
3814: moved elsewhere)
```

O parêntese é aberto em um evento e fechado no seguinte. A validação antiga
exigia equilíbrio dentro de cada evento e gerou um falso bloqueio. A regra
corrigida aceita somente fragmentos que mantenham exatamente a mesma contagem
de delimitadores da fonte. Se o modelo adicionar, remover ou trocar um
delimitador, a execução continua falhando fechada.

Outros riscos previsíveis desse perfil são:

1. traduzir payload vetorial por confundir `m`, `l` ou `b` com palavras;
2. retimar ou remover `\k`/`\kt` durante a tradução;
3. perder `\t`, `\move`, `\clip`, `\iclip` ou `\fad`;
4. tratar `ED top` como uma música nova e desperdiçar uma chamada;
5. marcar como falha milhares de eventos de um caractere que são apenas
   typesetting;
6. aceitar resposta idêntica em inglês para uma linha OP/ED que precisava de
   PT-BR (`KARAOKE_TRANSLATION_SOURCE_COPY`);
7. rejeitar parênteses legítimos divididos entre eventos simultâneos.

## Pré-flight recomendado

Antes de enviar a legenda ao modelo, o Transass deve registrar:

- contagem de eventos e unidades semânticas;
- estilos, camadas, `Name` e `Effect` usados;
- ocorrências de `\N`, `\k`, `\t`, `\pos`, `\move`, `\clip`, `\iclip` e `\p`;
- eventos de desenho e eventos de um caractere;
- blocos OP/ED, textos duplicados e romaji;
- delimitadores visíveis que atravessam eventos simultâneos;
- candidatos em inglês que precisam ser traduzidos;
- envelopes incompatíveis ou ambíguos que devem bloquear publicação.

Esse inventário não substitui a validação final. Ele apenas troca a surpresa no
fim da temporada por um aviso no começo — uma troca bastante vantajosa para
qualquer máquina que já tenha precisado traduzir 14 mil eventos.

## Fontes técnicas

- [Aegisub — ASS Override Tags](https://aegisub.org/docs/latest/ass_tags/):
  referência das tags de texto, posição, transformação, karaoke e desenho.
- [Aegisub — página oficial](https://aegisub.org/): projeto e documentação do
  editor usado como referência prática do ecossistema ASS.

Última atualização desta referência: 2026-09-10.
