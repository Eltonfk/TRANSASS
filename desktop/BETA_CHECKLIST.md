# Checklist da Fase 6 — Beta

Esta fase valida o instalador em uma máquina limpa, sem Python, Docker ou Git.
O teste deve ser repetido em Ubuntu 22.04/24.04 e Windows 10/11 quando o
artefato Windows estiver disponível no workflow CI.

## Roteiro para uma pessoa leiga

1. Instalar pelo arquivo recebido e abrir pelo ícone.
2. Escolher uma pasta com espaços e acentos, por exemplo
   `Biblioteca de Animes [PT-BR]`.
3. Confirmar que as séries aparecem e que o motor/modelo podem ser escolhidos.
4. Testar o botão de conexão com o provider desligado; a mensagem deve orientar
   o usuário e nenhuma tradução deve ser iniciada.
5. Ligar/configurar o provider e executar um episódio de teste.
6. Fechar durante uma tradução e abrir novamente; a fila deve continuar íntegra.
7. Executar a atualização por cima da instalação anterior; legendas, fila,
   credenciais e Library devem permanecer.
8. Desinstalar e confirmar que a pasta de mídia e as legendas geradas continuam.

## Gate automatizado

No checkout, com as dependências de desenvolvimento instaladas:

```sh
python -m pytest desktop/tests -q
python desktop/tests/run_beta_smoke.py
```

O smoke test usa uma pasta temporária, não acessa a mídia real e não faz
chamadas a modelos. Ele verifica HTTP local, caminhos Unicode, provider
indisponível, diagnóstico sanitizado e persistência do onboarding.

## Evidência manual necessária

- capturas da instalação e desinstalação em Ubuntu e Windows;
- versão do sistema, artefato e SHA-256 usado;
- resultado do episódio de teste e do reabrir após interrupção;
- confirmação de que a mídia não foi apagada.
