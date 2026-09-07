# Transass Desktop — plano de transformação

## Objetivo

Transformar o Transass Web `v2.5.0` em um aplicativo instalável para usuários
leigos de Windows e Linux, preservando o motor de tradução, a durabilidade,
o lineage, a revisão humana e a compatibilidade do formato `.ass`.

O aplicativo Desktop deverá iniciar por um ícone, abrir a interface numa janela
própria e não exigir Python, Docker, Git ou comandos no terminal.

## Baseline adotado

- Versão: `v2.5.0`
- Commit: `b5166b937d284f38edf1f43a9ec3c9c9261bd2ba`
- Branch: `candidate/v2.3.8`
- Tag existente: `v2.5.0`
- Snapshot: somente leitura; a linha Desktop nasce a partir dele.

O snapshot histórico registrava uma divergência entre `_version.py` e
`pyproject.toml`; a candidata agora mantém ambos, além do shell Desktop, em
`2.5.0`. A tag congelada não foi reescrita. `v2_3_8` continua reservado ao
identificador do pipeline e da branch de origem.

## Status da implementação

Fase 0 liberada no gate padrão: `903 passed`, sem warnings, com os perfis
históricos E07 e stress separados. Fase 1 concluída: o núcleo já não depende de `cwd=/app` ou
defaults de `/shows`, a configuração e paths são centralizados, os módulos
entram no Docker allowlist, o projeto gera wheel `2.5.0` e os binários
empacotados podem ser priorizados pelo `PATH`. Fase 2 concluída: launcher
launcher PySide6 leve, Flask em `127.0.0.1` com porta validada, bloqueio
single-instance e diálogo nativo de pasta. Fase 3 concluída: onboarding,
checagem de provider sem chamada de modelo e mensagens orientadas ao usuário.
Fase 4 concluída: credenciais usam cofre do sistema quando disponível, estado
antigo é migrado sem sobrescrita, backups têm manifesto e o diagnóstico é
sanitizado. Instaladores continuam pendentes.

Fase 5 em andamento: o bundle `onedir` e o AppImage Linux `x86_64` foram
regenerados com as correções atuais do motor e validados neste Ubuntu Server.
O instalador Windows continua sendo gerado pelo runner Windows do workflow CI,
pois o Inno Setup não roda neste ambiente Linux.

### Gate atual da Fase 5

- Script PyInstaller `onedir` com inclusão explícita do núcleo flat e dos
  recursos web/glossários.
- AppImage `x86_64` com desktop entry, ícone e checksum.
- Barra de menus opcional, com atalho `Ctrl+Shift+M`, tema escuro alinhado à
  interface web e menu **Ajuda** com versão, GitHub, documentação e atalhos.
- O diálogo de configuração mantém **Testar motor** disponível após a primeira
  abertura, sem executar uma tradução.
- Links externos tentam o navegador padrão e, se indisponível, copiam o
  endereço com aviso; popups e barras de rolagem seguem a paleta do app.
- A marca de `src/subtranslate/transass_logo.png` é reutilizada na janela,
  no AppImage e no executável Windows.
- Instalador Windows por usuário (Inno Setup), sem apagar estado ou mídia na
  desinstalação.
- Workflow CI com matriz Ubuntu/Windows, SBOM CycloneDX e SHA-256.
- Testes de empacotamento/Desktop: `27 passed`.
- Bundle Linux local validado: o executável iniciou o servidor Flask local e
  carregou a interface web; o timeout do teste foi intencional para encerrar a
  janela sem interação.
- AppImage local gerado em `build/Transass-x86_64.AppImage`, com
  `build/release/SHA256SUMS` e SBOM CycloneDX.
- O instalador Windows ainda depende do runner Windows do workflow.

Fase 6 em andamento: o checklist beta e o smoke test end-to-end foram criados.
O smoke cobre caminhos com Unicode/espaços, aliases de Compose, provider
indisponível, onboarding, diagnóstico e migração repetida. Após a correção do
renderer ASS, do agrupamento de falas quebradas e da auditoria de
delimitadores, Desktop tests: `27 passed`; suíte offline: `882 passed`, `38
deselected`, `66 subtests`. Ainda falta a evidência
manual em máquinas limpas (Ubuntu Desktop e Windows), incluindo instalação,
atualização, interrupção e desinstalação.

### Gate da Fase 2

- Testes Desktop: `12 passed`.
- Runtime Flask validado em `127.0.0.1` com porta efêmera e encerramento
  limpo.
- Bloqueio de segunda instância validado com lock do sistema operacional.
- Seleção de pasta usa diálogo nativo Qt e persiste a escolha para a próxima
  abertura.
- Serviço Docker local reconstruído e validado com `/Tank/data/Shows` e
  `/docker/transass/state` definidos pelo `.env`.

### Gate da Fase 3

- Assistente de primeira abertura integrado à interface web.
- Estado da biblioteca exibido sem revelar caminhos completos do host.
- Teste de provider usa endpoint de metadados e nunca chama o modelo.
- Erros de conexão, credencial ausente e configuração inválida recebem
  mensagens orientadas ao usuário.
- Conclusão persistida atomicamente em `onboarding.json`.
- Testes offline: `903 passed`; testes Desktop: `12 passed`.

### Gate da Fase 4

- `CredentialStore` seleciona keyring do sistema ou fallback de arquivo `600`.
- Migração copia apenas arquivos ausentes, preserva a Library e cria backup
  completo com hashes SHA-256.
- Diagnóstico exportado não contém API keys, conteúdo de mídia ou caminhos
  absolutos.
- O navegador expõe **Baixar diagnóstico** na área Diagnóstico.
- Imagem Docker reconstruída e serviço `transass` saudável.

## Direção técnica

```text
launcher PySide6 leve + navegador padrão
        ↓
servidor Flask local em 127.0.0.1
        ↓
núcleo Transass existente
        ↓
ffmpeg/ffprobe + Ollama ou API externa
```

Decisões iniciais:

- Reaproveitar a interface web atual dentro de uma janela desktop.
- Usar um servidor local com porta efêmera e sem exposição na rede.
- Substituir suposições de container (`/app`, `/shows`, `/tmp`) por caminhos
  gerenciados pela plataforma.
- Empacotar primeiro em modo `onedir`; o instalador esconderá os arquivos
  internos e facilitará diagnóstico e atualização.
- Windows: instalador por usuário (`.exe`).
- Linux: AppImage `x86_64` como primeiro formato; `.deb` depois.
- Não incluir modelos Ollama no instalador inicial.

## Fases e gates

### 0. Congelamento

Registrar manifesto do baseline, resultado da suíte offline, dependências e
checksums. Não promover para `main` nem fazer deploy automaticamente.

### 1. Portabilidade do núcleo

Criar configuração central, pacote Python real, resolver binários, remover
`cwd` e caminhos absolutos de container, e manter Docker funcionando.

### 2. Runtime Desktop

Criar launcher PySide6, iniciar/parar Flask com segurança, escolher porta,
impedir duas instâncias e abrir diálogos nativos de pasta.

### 3. Onboarding

Assistente de primeira execução, teste de provider, detecção de Ollama, seleção
de mídia e mensagens de erro compreensíveis.

### 4. Segurança e migração

Usar cofre de credenciais do sistema para API keys, migrar estado antigo com
backup, exportar diagnóstico sanitizado e preservar Library durante upgrades.

### 5. Empacotamento e distribuição

Builds nativos em CI para Windows e Linux, instaladores, ícones, desinstalação,
checksums, SBOM e assinatura digital quando os certificados estiverem
disponíveis.

### 6. Beta

Testar máquinas limpas, caminhos com acentos/espaços, falha de rede, Ollama
indisponível, interrupção durante tradução, atualização e desinstalação.

## Critério de aceite do MVP

Em uma máquina limpa, sem Python ou Docker, uma pessoa deve conseguir instalar,
abrir, escolher uma pasta, configurar API/Ollama, traduzir um episódio, fechar
e reabrir sem perder a fila, atualizar sem perder o estado e desinstalar sem
apagar as legendas geradas.

## Fora do MVP

- Empacotar modelos de IA locais.
- Suporte inicial a macOS ou ARM64.
- Sincronização em nuvem.
- Reescrita completa da interface em outro framework.
