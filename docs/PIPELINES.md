# Pipelines

`pipeline_registry.py` define os IDs aceitos e `pipeline_orchestrator.py`
executa os estágios. O padrão operacional é `v2_3_8`.

## V2.3.8

```text
FULL_TRANSLATION_V238 → KARAOKE_AUGMENTATION_V230
```

O primeiro estágio traduz diálogo, sinais e letras inglesas detectadas pelo
conteúdo, com orçamento físico e evidência por chamada. O segundo compara a
fonte original com o candidato intermediário e traduz somente letras OP/ED que
ainda estejam em inglês.

Metadados ASS também participam da decisão: eventos de elenco, créditos,
título e comentários técnicos não viram letra apenas porque o fansub reutilizou
um estilo chamado `Karaoke Translation`. Foi assim que 21 “músicas” voltaram a
ser 8; a matemática agradeceu.

## Planos em retirada

`v2_1_2` e `v2_1_3` estão marcados como `deprecated`. A interface, o endpoint
de preflight, a fila web e o comando normal de tradução recusam esses IDs para
novos jobs e apontam para `v2_3_8`. O registro e os adapters continuam presentes
temporariamente para preservar lineage, diagnósticos, jobs já criados e replay
explícito.

Essa etapa não é uma remoção física. O núcleo `pipeline_v2_1_3.py` ainda é uma
dependência transitiva dos adapters V2.2.x e do materializador base utilizado
por V2.3.8. Antes de apagar os arquivos, as primitivas restantes precisam ser
extraídas para módulos neutros, os checkpoints históricos precisam ser auditados
e a suíte offline precisa provar que o fechamento de imports continua íntegro.

## Outros planos históricos

`legacy`, `v2_2_4`, `v2_2_5`, `v2_2_6` e `v2_3_0` permanecem registrados para
replay e compatibilidade explícita. Eles não são fallback automático do plano
canônico. ID desconhecido falha antes de importar ou chamar um modelo.

`v2_3_0` não está em retirada: continua sendo a etapa de complemento de
karaokê/OP/ED dentro do plano V2.3.8.

## Regras invariantes

- estrutura ASS pertence ao programa;
- fallback muda provider apenas por falha de transporte;
- retry e isolamento consomem o mesmo orçamento físico;
- cobertura parcial não publica candidato;
- checkpoint é salvo antes de encerrar uma falha recuperável.
