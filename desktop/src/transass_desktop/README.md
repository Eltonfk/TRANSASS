# Runtime Desktop

Este pacote será responsável por:

- caminhos por plataforma;
- configuração e migração de estado;
- inicialização controlada do servidor local;
- janela PySide6 com a interface web incorporada via QtWebEngine;
- ciclo de vida e prevenção de instâncias duplicadas.

O launcher PySide6 e o runtime Flask local fazem parte da Fase 2. O servidor
usa `127.0.0.1` e porta efêmera por padrão, aceita uma porta fixa validada por
`TRANSASS_PORT`, e a instância é protegida por bloqueio exclusivo no diretório
de configuração. O QtWebEngine carrega o runtime web local dentro da janela;
links externos só são abertos quando o usuário escolhe uma ação do menu Ajuda.
Os diálogos de pasta são carregados sob demanda para que os testes e builds
sem Qt continuem funcionando.
