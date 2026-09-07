# Runtime Desktop

Este pacote será responsável por:

- caminhos por plataforma;
- configuração e migração de estado;
- inicialização controlada do servidor local;
- janela PySide6 leve integrada ao runtime web local;
- ciclo de vida e prevenção de instâncias duplicadas.

O launcher PySide6 e o runtime Flask local fazem parte da Fase 2. O servidor
usa `127.0.0.1` e porta efêmera por padrão, aceita uma porta fixa validada por
`TRANSASS_PORT`, e a instância é protegida por bloqueio exclusivo no diretório
de configuração. Os diálogos de pasta são carregados sob demanda para que os
testes e builds sem Qt continuem funcionando.
