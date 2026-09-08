# Recovery e rollback

## Tradução interrompida

O Transass persiste fila, tentativas e checkpoints. Reinicie o aplicativo,
confirme o provider e retome pela interface. Não copie manualmente um arquivo
intermediário para o nome publicado: isso contorna validação e lineage.

## Atualização com problema

- Desktop: reinstale o artefato anterior; os dados ficam fora da instalação.
- Docker: recrie o serviço usando a tag/digest anterior.
- Confirme `/health` e faça uma leitura do acervo antes de iniciar nova fila.

Um rollback deve registrar versão, commit, digest/checksum e backup de
`STATE_DIR`. Tags Git não devem ser movidas para “apontar para o que queríamos
ter lançado”; publique uma versão corretiva.

## Estado

Antes de qualquer reparo material:

1. pare o serviço;
2. copie `STATE_DIR` para destino separado;
3. registre SHA-256 ou manifesto;
4. trabalhe numa cópia;
5. valide acervo, fila e configuração antes de substituir o original.

Recovery bom é tedioso. Recovery emocionante costuma virar post-mortem.
