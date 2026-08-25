---
description: Explica por que o Subtranslate parou e informa exatamente como prosseguir, sem escrever.
agent: subtranslate-build
---

Ative `ERROR_DIAGNOSIS_AND_CORRECTION_PROFILE` em modo diagnostico estritamente
read-only. Analise o terminal mais recente e o estado corrente. Nao corrija nem
escreva nesta invocacao.

Entregue primeiro uma explicacao curta em portugues simples e depois o bloco
obrigatorio `POR_QUE_PAROU ... SE_FALHAR_NOVAMENTE`. Classifique a correcao e
informe se o proximo passo e `/subtranslate-fix`, `/subtranslate-next`, uma
mensagem exata `AUTORIZAR` ou um comando manual root. Nunca responda apenas com
um nome de gate.

Contexto adicional do usuario:
$ARGUMENTS
