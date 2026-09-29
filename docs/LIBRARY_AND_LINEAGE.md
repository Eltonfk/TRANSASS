# Acervo e lineage

O acervo registra fontes, candidatos, revisões e publicações. Lineage descreve
proveniência, não uma árvore de versões decorativa.

```text
SOURCE
  └─ TRANSLATED_FROM → estágio V238
       └─ KARAOKE_AUGMENTED_FROM → resultado final
            └─ PUBLISHED_AS → sidecar .pt-BR.ass
```

Uma retradução adiciona `RETRANSLATED_FROM`. Toda aresta deve permanecer no
mesmo episódio (`LINEAGE_EPISODE_BOUNDARY_INVARIANT`); cruzar episódios falha
fechado.

Os selos visuais do acervo distinguem pipeline, versão publicada, candidato e
estado de revisão. Publicar uma retradução substitui o sidecar anterior de
forma controlada, mas preserva os registros e hashes que explicam a troca.

## Retradução candidata sem efeitos na Library

`candidate_only=true` é um fluxo de teste isolado: consulta a versão PT-BR e a
fonte original, mas não importa o sidecar legado, não ingere a faixa extraída,
não cria lineage/registro traduzido e não publica ou substitui arquivo algum.
A fonte de trabalho é copiada ou extraída sob
`STATE_DIR/candidate-source-staging/<job-id>/`, validada por tamanho e SHA-256,
e removida quando o job termina ou é cancelado. A saída candidata e as capturas
duráveis do modelo ficam em `STATE_DIR` para revisão; não são uma publicação nem
uma aprovação linguística. Uma candidata não é promovida automaticamente.

O banco, a memória aprovada, o glossário operacional e as capturas ficam em
`STATE_DIR`. `resources/glossaries/` contém apenas recursos versionados da
aplicação.
