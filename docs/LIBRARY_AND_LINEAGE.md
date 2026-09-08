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

O banco, a memória aprovada, o glossário operacional e as capturas ficam em
`STATE_DIR`. `resources/glossaries/` contém apenas recursos versionados da
aplicação.
