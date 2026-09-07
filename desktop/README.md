# Transass Desktop

Estrutura inicial do aplicativo instalável, derivado do Transass Web `v2.5.0`.

O código do motor continua em `../src/subtranslate`. Esta pasta conterá apenas
a casca Desktop, os recursos de distribuição e os testes específicos de
instalação/runtime.

## Estrutura

```text
desktop/
├── BASELINE.md
├── README.md
├── packaging/
│   ├── linux/
│   └── windows/
├── src/transass_desktop/
└── tests/
```

O launcher PySide6 e o runtime Flask local foram implementados na Fase 2; a
interface web é incorporada na própria janela do aplicativo por QtWebEngine,
sem abrir o navegador padrão durante a inicialização. O onboarding, a migração
segura e o diagnóstico foram concluídos nas fases seguintes. O bundle Linux `onedir`
e o AppImage podem ser gerados com as ferramentas isoladas descritas em
`packaging/`; o instalador Windows é gerado pelo workflow em um runner Windows.

Na Fase 4, o runtime migra arquivos ausentes do antigo `deploy/state` sem
sobrescrever a Library existente, mantém um backup com manifestos SHA-256 e
oferece o download de um diagnóstico sanitizado pela área Diagnóstico.

A janela oferece uma barra de menus opcional (`Ctrl+Shift+M`) e um menu Ajuda
com versão, links do projeto, atalhos e um tutorial visual para obter chaves
Google/NVIDIA ou instalar e preparar um modelo Ollama. A mesma marca visual do
app é usada na janela, no AppImage e no executável Windows.

O botão **Testar motor** permanece disponível dentro da configuração mesmo
após o primeiro onboarding; ele salva os campos atuais e testa a conectividade
sem executar uma tradução.

O roteiro da Fase 6 está em `BETA_CHECKLIST.md`; o smoke automatizado pode ser
executado com `python desktop/tests/run_beta_smoke.py` e não acessa a mídia real
nem chama modelos.

Depois de instalar as dependências de build, o bundle pode ser validado de
forma completa com:

```sh
python desktop/packaging/build_bundle.py --dist build/desktop
python desktop/tests/run_bundle_smoke.py build/desktop/Transass/Transass
```

O empacotador interrompe o processo antes do PyInstaller quando alguma
dependência do runtime Desktop estiver ausente.
