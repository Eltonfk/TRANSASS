# Runtime Desktop

Este pacote implementa caminhos por plataforma, lock de instância, servidor
Flask localhost, janela QtWebEngine, seleção de pasta e encerramento ordenado.

O servidor usa porta efêmera por padrão; `TRANSASS_PORT` aceita uma porta fixa
válida. Estado e configuração são resolvidos por plataforma e aliases Docker
são rejeitados fora do contêiner. No Windows, operações POSIX sem equivalente
— como `fsync` de diretório — degradam de forma explícita e segura.
