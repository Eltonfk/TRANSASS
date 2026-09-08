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
  → complemento OP/ED ainda pendente
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
- `pipeline_v2_1_3.py`: tradução base, classificação e reconstrução.
- `production_v2_3_0_adapter.py`: complemento de letras OP/ED.
- `v238_*`: durabilidade por chamada, normalização e propriedade estrutural.
- `transport_providers.py`: Ollama e APIs hospedadas.
- `anime_subtitle_library.py`: acervo, objetos e lineage.
- `gpu_thermal_guard.py`: telemetria e backoff térmico agnóstico.
- `desktop/src/transass_desktop`: janela, caminhos e ciclo de vida local.

Planos desconhecidos, quebra estrutural, perda de cobertura e cruzamento de
episódios falham fechados. Ausência de sensor térmico, por outro lado, degrada
para modo passivo: segurança não deve virar superstição.
