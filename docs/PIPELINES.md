# Pipelines

`pipeline_registry.py` define os IDs aceitos e `pipeline_orchestrator.py`
executa os planos. O padrão para novos trabalhos é `v3`; a versão técnica
registrada nos artefatos é `v3_0_0`.

## V3 — plano atual

```text
parse ASS → classificar → lotes semânticos → efeitos → validar → publicar
```

O V3 processa a legenda em memória, agrupa placas repetidas e só envia ao
modelo unidades que passaram pelo classificador de fonte. Cada lote precisa
retornar todas as unidades; resposta ausente, vazia, duplicada ou desconhecida
interrompe o job sem criar saída publicável. A escrita usa arquivo temporário,
`fsync`, publicação atômica sem sobrescrita e uma segunda validação após serialização.

Chamadas de produção passam por captura durável e orçamento físico por provider.
O identificador da execução é preservado em retries e após reinício: respostas
completas são reproduzidas, reservas de chamadas são restauradas e capturas
ambíguas param para reconciliação sem reenvio automático. O fallback não reinicia
um episódio depois de uma chamada física primária; nesse ponto, o estado precisa
ser reconciliado. A saída só segue para publicação depois da auditoria de
arquivamento, da validação estrutural e da confirmação da identidade da Library.

OP/ED em inglês sem temporização silábica são traduzidos e remontados no
envelope ASS original. Romaji, créditos e efeitos são preservados. Letras em
inglês com tags de karaoke silábico são bloqueadas: o V3 ainda não remapeia com
segurança tempos de sílabas entre idiomas, então não inventa sincronização.

## Planos em retirada

`legacy`, `v2_1_2`, `v2_1_3`, `v2_3_0` e `v2_3_8` são somente
compatibilidade/replay e não aceitam novos jobs. Configurações persistidas com
`legacy`, `v2_3_0` ou `v2_3_8` migram para `v3` em memória; o arquivo antigo não
é reescrito só por ser lido. Ao salvar configurações, o ID substituído é
normalizado para `v3`. Os adapters e registros históricos permanecem
disponíveis para lineage, diagnósticos e replay explícito.

Para jobs normais, a Library precisa conter um episódio ANIME cujo caminho de
mídia corresponda exatamente ao vídeo selecionado. O V3 não inventa IDs por
hash: antes de qualquer chamada ao modelo, bloqueia se a identidade não puder
ser provada. Depois da auditoria, arquiva ou reutiliza o registro exato da fonte,
grava a relação `TRANSLATED_FROM` e publica por criação exclusiva. Se outro
processo criar o sidecar nesse intervalo, a publicação falha sem substituí-lo.

`/retry-failed` é recuperação/replay da mesma execução, não um pedido implícito
para gerar novamente uma resposta já capturada. Para solicitar uma nova geração
depois de uma resposta semanticamente inválida, o operador deve iniciar um novo
job; capturas incompletas continuam bloqueadas para reconciliação.

Isso ainda não é remoção física. V3 reutiliza classificadores semânticos
históricos para romaji e OP/ED, e os planos antigos precisam continuar
resolvíveis para replay. Extrair esses classificadores para módulos neutros é o
próximo passo antes de remover os arquivos históricos.

Essa compatibilidade também é uma dependência de código ativa, não apenas
histórico: `pipeline_v3.py` ainda importa helpers de idioma/resíduo e o
classificador de eventos de `pipeline_v2_1_3.py`; a auditoria web reaproveita
validadores desse módulo. Isso não seleciona o adaptador V2.1.3 para um job V3,
mas significa que a extração do legado ainda não terminou. Antes de apagar ou
renomear o módulo antigo, esses helpers precisam ser movidos para módulos
neutros, com os mesmos testes de contrato e replay preservado.

## Outros planos históricos

`v2_2_4`, `v2_2_5`, `v2_2_6`, `v2_3_0` e `v2_3_8` permanecem registrados para
compatibilidade e replay. Eles não são fallback automático. IDs desconhecidos
falham antes de importar adapters ou chamar um modelo.

## Regras invariantes

- estrutura ASS pertence ao programa;
- fallback não repete chamadas que possam ter alcançado o modelo;
- cada chamada nova consome o orçamento físico do provider ativo;
- capturas completas podem ser reproduzidas, capturas ambíguas não são reenviadas;
- cobertura parcial ou auditoria bloqueada nunca publica candidato;
- lineage só é registrado quando há um registro de origem válido.
