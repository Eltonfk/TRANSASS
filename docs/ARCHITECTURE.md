# Arquitetura

O Transass usa o mesmo núcleo em três embalagens: aplicativo Linux, aplicativo
Windows e serviço Docker. A interface é web, mas o Desktop a carrega dentro de
uma janela Qt; o backend local escuta somente em `127.0.0.1` e porta efêmera.

## Fluxo principal

```text
Fonte ASS/SSA ou faixa MKV
  → resolução do idioma pelo conteúdo
  → classificação dos eventos
  → tradução linguística em lotes
  → reconstrução da estrutura ASS
  → complemento OP/ED no mesmo plano semântico
  → validação e checkpoint
  → acervo/revisão
  → publicação .pt-BR.ass
```

O modelo decide texto; o programa continua dono de tempo, estilo, camada,
tags, desenhos e quebras. Dar as chaves da estrutura ao LLM seria divertido
por aproximadamente três segundos.

## Componentes

- `app.py`: API Flask, fila, eventos e interface.
- `pipeline_registry.py`: planos permitidos e seus estágios.
- `pipeline_orchestrator.py`: execução, orçamento e validação entre estágios.
- `pipeline_v3.py`: fluxo unificado atual de classificação, lotes, remontagem,
  auditoria e publicação.
- `translation_quality.py`: qualidade linguística determinística, normalização de
  espaçamento, proteção de nomes próprios e heurísticas gramaticais desacopladas.
- `pipeline_v2_1_3.py` e `production_v2_3_0_adapter.py`: componentes históricos
  cujos classificadores ainda são reutilizados pelo V3; não são planos de novos
  jobs.
- `v238_*`: durabilidade por chamada, normalização e propriedade estrutural.
- `transport_providers.py`: Ollama e APIs hospedadas.
- `anime_subtitle_library.py`: acervo, objetos e lineage.
- `gpu_thermal_guard.py`: telemetria e backoff térmico agnóstico.
- `desktop/src/transass_desktop`: janela, caminhos e ciclo de vida local.

Os planos V2.1.2 e V2.1.3 não aceitam novos jobs. Eles continuam resolvíveis
somente para compatibilidade e replay explícito, porque parte do materializador
histórico ainda reutiliza suas primitivas. A remoção do código só poderá ocorrer
depois da extração dessas primitivas para módulos neutros.

Planos desconhecidos, quebra estrutural, perda de cobertura, identidade de mídia
incerta e cruzamento de episódios falham fechados. V3 só publica após auditar,
registrar a fonte e criar lineage para o resultado. Ausência de sensor térmico,
por outro lado, degrada para modo passivo: segurança não deve virar superstição.

## Contratos de custo e retenção

- Contextos linguísticos são construídos sobre uma ordenação e um índice por
  episódio; adicionar eventos não deve reordenar a legenda para cada unidade.
- A validação estrutural reutiliza os objetos ASS já carregados sempre que o
  contrato do estágio permitir.
- Eventos SSE carregam o próprio status. O cliente não deve transformar cada
  evento em outra consulta a `/status`.
- Evidência por chamada continua durável e fail-closed. Otimizações de ledger
  não podem remover `fsync`, exactly-once ou dados necessários à recuperação.
- Dados detalhados de auditoria e histórico precisam de retenção limitada ou
  armazenamento incremental; não devem crescer indefinidamente dentro do
  snapshot global da interface.

Auditorias completas vivem em `STATE_DIR/audits/record-<id>.json`; `jobs.json`
mantém apenas sua projeção compacta. A Library é a autoridade de publicação:
um job só chega a `COMPLETED` depois que objeto, sidecar e registro publicado
foram confirmados. Endpoints oficiais e digests de modelo são derivados no
servidor — o navegador não ganha um crachá de autoridade desenhado a lápis.
