/* Small, UI-only localization layer. Technical data, logs and API payloads do
 * not pass through this module. The fallback locale is deliberately explicit
 * so an incomplete presentation dictionary can never break the interface. */
(function () {
  "use strict";

  const FALLBACK = "pt-BR";
  const STORAGE_KEY = "transass.uiLocale";

  const ptBR = {
    "app.title": "Transass · Central de tradução",
    "locale.ptBR": "pt-BR",
    "locale.qi83": "QI 83",
    "ui.language": "Idioma da interface",
    "brand.subtitle": "Central de tradução de legendas",
    "brand.joke": "Troca o idioma. O nome continua questionável.",
    "chip.pipeline": "Pipeline…",
    "chip.model": "Modelo…",
    "chip.motor": "Motor…",
    "chip.service": "Serviço…",
    "action.configureMotor": "⚙ Configurar motor",
    "nav.areas": "Áreas do Transass",
    "nav.translate": "▶ Traduzir",
    "nav.inbox": "▤ Caixa de entrada",
    "nav.library": "▣ Acervo e revisão",
    "nav.memory": "✦ Memória aprovada",
    "nav.diagnostics": "⌨ Diagnóstico",
    "nav.translate.label": "Traduzir",
    "nav.inbox.label": "Caixa de entrada",
    "nav.library.label": "Acervo",
    "nav.memory.label": "Memória",
    "nav.diagnostics.label": "Diagnóstico",
    "workflow.step1": "Escolha uma temporada",
    "workflow.step2": "Selecione os episódios",
    "workflow.step3": "Tradução em andamento",
    "workflow.ready": "Pronto para traduzir",
    "workflow.hint.browse": "Navegue até uma pasta com vídeos para começar.",
    "workflow.hint.loaded": "Navegue até uma pasta com vídeos e carregue a temporada.",
    "workflow.hint.running": "Acompanhe o episódio atual. Se der ruim, o log não vai fingir demência.",
    "workflow.hint.select": "Use “Sem PT-BR” para pegar somente o que ainda precisa de tradução.",
    "workflow.hint.ready": "{count} episódio(s) com fonte {source} → português do Brasil.",
    "workflow.safety": "✓ Sem sobrescrever PT-BR existente",
    "step.1": "Passo 1",
    "step.2": "Passo 2",
    "step.3": "Passo 3",
    "origin.title": "Origem",
    "origin.subtitle": "Escolha a temporada que vai ganhar uma legenda nova.",
    "origin.currentPath": "Caminho atual",
    "origin.subfolders": "Subpastas",
    "origin.openFolder": "Abrir pasta",
    "origin.back": "← Voltar",
    "origin.loadSeason": "Carregar esta temporada",
    "origin.activeSeason": "Temporada ativa",
    "origin.noneLoaded": "Nenhuma carregada",
    "queue.title": "Fila de tradução",
    "queue.subtitle": "Tudo pronto para traduzir — e, se algo der ruim, a fila entrega o boletim.",
    "queue.completed": "concluídos",
    "queue.waiting": "na fila",
    "queue.running": "em execução",
    "queue.failed": "falhas",
    "queue.skipped": "ignorados",
    "queue.blocked": "bloqueados pela falha",
    "queue.selectEpisodes": "Selecione episódios",
    "queue.translate": "Traduzir {count} episódio(s)",
    "queue.translate.one": "Traduzir {count} episódio",
    "queue.translate.many": "Traduzir {count} episódios",
    "queue.retry": "Reprocessar falhos",
    "queue.pause": "Pausar",
    "queue.resume": "Continuar",
    "queue.stop": "Parar fila",
    "queue.advanced": "Ações avançadas e de manutenção",
    "queue.dryRun": "Simulação (zero tradução/publicação)",
    "queue.dryRunTitle": "Planejamento sem tradução nem publicação",
    "queue.audit": "Auditar temporada",
    "queue.retranslateSeason": "Retraduzir temporada inteira",
    "episodes.title": "Escolha os episódios",
    "episodes.subtitle": "A seleção fica aqui; a ansiedade pode esperar na fila.",
    "episodes.filter": "Filtrar episódios…",
    "episodes.selected": "{count} selecionado(s) · {visible}/{total} exibidos",
    "episodes.selected.one": "{count} selecionado · {visible}/{total} exibidos",
    "episodes.selected.many": "{count} selecionados · {visible}/{total} exibidos",
    "episodes.missing": "Sem PT-BR",
    "episodes.legacy": "Legadas",
    "episodes.clear": "Limpar",
    "episodes.retranslate": "Retraduzir",
    "episodes.sourceLanguage": "Idioma da fonte",
    "episodes.sourceLanguageTitle": "Aplica a todos os episódios da pasta",
    "episodes.detect": "Detectar automaticamente",
    "episodes.detectTitle": "Detecta os idiomas disponíveis na temporada",
    "episodes.adjustHint": "Pode ser ajustado por episódio na lista.",
    "episodes.noneLoaded": "Nenhuma temporada carregada",
    "episodes.chooseFolder": "Escolha uma pasta ao lado e clique em “Carregar esta temporada”.",
    "episodes.noneFound": "Nenhum vídeo encontrado",
    "episodes.notSeason": "Esta pasta não parece ser uma temporada. Ou os episódios estão muito bem escondidos.",
    "episodes.nothingHere": "Nada por aqui",
    "episodes.noMatch": "Nenhum episódio corresponde ao filtro atual.",
    "episodes.more": "Carregar mais episódios",
    "status.alreadyTranslated": "Já traduzido",
    "status.notStarted": "Não iniciado",
    "status.waiting": "Na fila",
    "status.starting": "Iniciando",
    "status.translating": "Traduzindo",
    "status.validating": "Validando",
    "status.publishing": "Publicando",
    "status.paused": "Pausada",
    "status.failed": "Falhou",
    "status.completed": "Concluído",
    "status.skippedCurrent": "Ignorado · versão atual validada",
    "status.blockedAfterFailure": "Não iniciado após falha",
    "status.sourceLibrary": "✓ Biblioteca",
    "status.sourceSidecar": "✓ Legenda externa",
    "status.sourceInternal": "✓ Faixa interna",
    "status.sourcePgs": "⚠ PGS — OCR não suportado",
    "status.sourceAmbiguous": "⚠ Fonte ambígua",
    "status.sourceError": "⚠ Metadados da fonte",
    "status.sourceMissing": "✕ Fonte não encontrada",
    "status.auditClean": "✓ sem problemas",
    "status.auditProblems": "⚠ problemas",
    "status.auditPartial": "◐ parcial",
    "status.auditReview": "◐ revisão recomendada",
    "status.auditSeparated": "◐ estados por versão",
    "status.auditNone": "— não auditada",
    "status.stageSemantic": "RECONSTRUÇÃO SEMÂNTICA",
    "status.preparing": "PREPARANDO — total ainda não calculado",
    "status.progress": "Unidades base",
    "status.baseCalls": "Chamadas base",
    "status.semanticCalls": "Chamadas semânticas",
    "status.retries": "Tentativas",
    "status.budget": "Limite de tentativas",
    "status.time": "Tempo",
    "status.lastActivity": "Última atividade",
    "status.failure": "Falha",
    "status.interrupted": "Temporada interrompida após a primeira falha. Os episódios restantes não foram iniciados.",
    "status.downloadCandidate": "Baixar candidato",
    "aria.episodeSelect": "Selecionar {name}",
    "aria.episodeSource": "Idioma de origem de {name}",
    "progress.kicker": "Agora",
    "progress.title": "Progresso atual",
    "progress.none": "Nenhum episódio em execução.",
    "progress.unitLabel": "Progresso por unidade",
    "history.kicker": "Recentes",
    "history.title": "Histórico",
    "history.technical": "incluir jobs técnicos",
    "history.none": "Nenhuma sessão registrada.",
    "inbox.kicker": "Pós-tradução",
    "inbox.title": "Caixa de entrada",
    "inbox.subtitle": "Cada resultado tem um próximo passo claro — publicar, revisar ou investigar.",
    "inbox.refresh": "Atualizar",
    "inbox.ready": "Pronto para publicar",
    "inbox.review": "Precisa de revisão",
    "inbox.failed": "Falhou",
    "inbox.noneCandidate": "Nada esperando. Até o robô entregou no prazo.",
    "inbox.nonePending": "Nada pendente.",
    "inbox.noneRecentFailure": "Nenhuma falha recente.",
    "inbox.loading": "Carregando caixa de entrada…",
    "inbox.unavailable": "Não foi possível carregar a caixa de entrada.",
    "library.kicker": "Acervo persistente",
    "library.title": "Acervo e versões",
    "library.subtitle": "Compare, revise e publique sem apagar o que veio antes.",
    "library.loading": "Carregando acervo…",
    "library.none": "Nenhuma série de anime catalogada.",
    "library.unavailable": "Não foi possível carregar o acervo.",
    "library.detailLoading": "Carregando episódios e versões…",
    "library.detailUnavailable": "Não foi possível carregar os detalhes desta série.",
    "memory.kicker": "Conhecimento controlado",
    "memory.title": "Memória e glossário",
    "memory.subtitle": "Sincronizada automaticamente após cada aprovação humana. O robô não vota na própria prova.",
    "memory.none": "Nenhuma correção aprovada ainda. O robô continua sem cola.",
    "memory.openGlossary": "Abrir glossário",
    "memory.loading": "Carregando memória…",
    "memory.unavailable": "Não foi possível carregar a memória local.",
    "diagnostics.kicker": "Transparência operacional",
    "diagnostics.title": "Diagnóstico e logs",
    "diagnostics.subtitle": "O lugar onde “deu ruim” vira evidência reproduzível.",
    "diagnostics.download": "Baixar diagnóstico",
    "diagnostics.clear": "Limpar visualização",
    "diagnostics.thermalTelemetry": "mostrar leituras térmicas",
    "diagnostics.empty": "Tudo quieto. Suspeito, mas saudável.",
    "dialog.library": "Biblioteca",
    "dialog.versionDetails": "Detalhes da versão",
    "dialog.close": "Fechar",
    "dialog.loading": "Carregando…",
    "dialog.configuration": "Configuração",
    "dialog.translationMotor": "Motor de tradução",
    "dialog.motorIntro": "Um motor principal, um plano B e nenhuma chave de API passeando pelo navegador.",
    "dialog.sourceLanguage": "Idioma padrão da legenda fonte",
    "dialog.destinationHint": "O destino é sempre português do Brasil.",
    "dialog.primaryMotor": "Motor principal",
    "dialog.fallback": "Motor alternativo",
    "dialog.noFallback": "— sem motor alternativo —",
    "dialog.fallbackHint": "O motor alternativo só entra quando o principal falha e deixa evidência própria.",
    "dialog.keysNote": "As chaves de API ficam armazenadas localmente e nunca são devolvidas ao navegador. Campo vazio mantém a chave atual.",
    "dialog.testMotor": "Testar motor",
    "dialog.saveAndTest": "Salvar e testar",
    "dialog.save": "Salvar configuração",
    "dialog.firstOpen": "Primeira abertura",
    "dialog.prepare": "Vamos preparar o Transass",
    "dialog.prepareIntro": "Em poucos passos, confira a pasta de mídia e o motor. Nenhuma tradução será executada durante este teste.",
    "dialog.checkingLibrary": "Verificando a biblioteca…",
    "dialog.checkingMotor": "Verificando o motor…",
    "dialog.configureMotor": "Configurar motor",
    "dialog.finish": "Concluir",
    "dialog.preflight": "Pré-verificação",
    "dialog.beforeTranslate": "Confira antes de traduzir",
    "dialog.preflightIntro": "Nenhuma chamada foi feita ainda. É só a conta do que vem pela frente.",
    "dialog.startTranslation": "Iniciar tradução",
    "footer": "durabilidade forense para arquivos cujo nome já começa com ass.",
    "aria.closeDetails": "Fechar detalhes",
    "aria.sourcePath": "Caminho atual",
    "aria.sourceLanguage": "Idioma de origem da legenda",
    "aria.filterEpisodes": "Filtrar episódios",
    "placeholder.sourceLanguage": "ex.: inglês, espanhol, japonês",
    "placeholder.primaryModel": "Modelo, ex.: qwen3.5:9b",
    "placeholder.fallbackModel": "Modelo alternativo",
    "placeholder.baseUrl": "URL-base · somente para OpenAI-compatível"
  };

  const qi83 = {
    "app.title": "🐒🍌",
    "locale.qi83": "🐒 QI 83 🍌",
    "ui.language": "🐒💬",
    "brand.subtitle": "🐒💬🍌",
    "brand.joke": "🐒🔄🍌",
    "chip.pipeline": "🐒⚙️",
    "chip.model": "🧠🐒",
    "chip.motor": "🐒🔌",
    "chip.service": "🐒✅",
    "action.configureMotor": "⚙️🐒",
    "nav.areas": "🐒📍",
    "nav.translate": "🐒💬➡️🍌",
    "nav.inbox": "📥🍌",
    "nav.library": "📚🍌",
    "nav.memory": "🧠🍌",
    "nav.diagnostics": "⌨️🐒",
    "nav.translate.label": "🐒💬➡️🍌",
    "nav.inbox.label": "📥🍌",
    "nav.library.label": "📚🍌",
    "nav.memory.label": "🧠🍌",
    "nav.diagnostics.label": "⌨️🐒",
    "workflow.step1": "🐒👉📁",
    "workflow.step2": "🐒👉📄",
    "workflow.step3": "🐒💬➡️🍌",
    "workflow.ready": "✅🍌",
    "workflow.hint.browse": "🐒👉📁",
    "workflow.hint.loaded": "📁✅🍌",
    "workflow.hint.running": "🐒🔨🍌",
    "workflow.hint.select": "🐒👉📄",
    "workflow.hint.ready": "🐒💬➡️🍌",
    "workflow.safety": "✅🍌",
    "step.1": "1️⃣",
    "step.2": "2️⃣",
    "step.3": "3️⃣",
    "origin.title": "📥🍌",
    "origin.subtitle": "🐒👉📁",
    "origin.currentPath": "📍🐒",
    "origin.subfolders": "📁🍌",
    "origin.openFolder": "📂🍌",
    "origin.back": "⬅️🐒",
    "origin.loadSeason": "✅🍌",
    "origin.activeSeason": "🐒✅",
    "origin.noneLoaded": "🙈",
    "queue.title": "🐒📋",
    "queue.subtitle": "🐒👀🍌",
    "queue.completed": "🐒🍌🎉",
    "queue.waiting": "🐒🕐",
    "queue.running": "🐒🔨",
    "queue.failed": "🐒💥",
    "queue.skipped": "🙈",
    "queue.blocked": "🐒💥⛔",
    "queue.selectEpisodes": "🐒👉📄",
    "queue.translate": "🐒💬➡️🍌",
    "queue.translate.one": "🐒💬➡️🍌",
    "queue.translate.many": "🐒💬➡️🍌",
    "queue.retry": "🔁🐒",
    "queue.pause": "⏸️🐒",
    "queue.resume": "▶️🐒",
    "queue.stop": "⛔🐒",
    "queue.advanced": "🦍⚙️☠️",
    "queue.dryRun": "🐒🧪",
    "queue.dryRunTitle": "🐒🧪",
    "queue.audit": "🦍🔍",
    "queue.retranslateSeason": "🔄🍌",
    "episodes.title": "🎬🍌",
    "episodes.subtitle": "🐒👉📄",
    "episodes.filter": "🔎🍌",
    "episodes.selected": "🍌",
    "episodes.selected.one": "🍌",
    "episodes.selected.many": "🍌",
    "episodes.missing": "🙈 PT-BR",
    "episodes.legacy": "📦🍌",
    "episodes.clear": "🙈",
    "episodes.retranslate": "🔄🐒",
    "episodes.sourceLanguage": "💬❓",
    "episodes.detect": "🧠🐒✨",
    "episodes.adjustHint": "🐒🤏🍌",
    "episodes.noneLoaded": "🙈",
    "episodes.chooseFolder": "🐒👉📄",
    "episodes.noneFound": "🐒❓📄",
    "episodes.notSeason": "🐒❓📁",
    "episodes.nothingHere": "🙈",
    "episodes.noMatch": "🙈",
    "episodes.more": "🐒➡️🍌",
    "status.alreadyTranslated": "🍌✅",
    "status.notStarted": "🐒❓",
    "status.waiting": "🐒🕐",
    "status.starting": "▶️🐒",
    "status.translating": "🐒🔨",
    "status.validating": "🧐🐒",
    "status.publishing": "📦➡️",
    "status.paused": "⏸️🐒",
    "status.failed": "🐒💥",
    "status.completed": "✅🍌",
    "status.skippedCurrent": "🙈✅",
    "status.blockedAfterFailure": "⛔🐒",
    "status.sourceLibrary": "📚✅",
    "status.sourceSidecar": "📄✅",
    "status.sourceInternal": "💬✅",
    "status.sourcePgs": "⚠️🖼️",
    "status.sourceAmbiguous": "⚠️❓",
    "status.sourceError": "⚠️💥",
    "status.sourceMissing": "🐒❌📄",
    "status.auditClean": "✅🍌",
    "status.auditProblems": "🐒💥",
    "status.auditPartial": "◐🐒",
    "status.auditReview": "🧐🐒",
    "status.auditSeparated": "🔀🍌",
    "status.auditNone": "🙈",
    "status.stageSemantic": "🐒🧠",
    "status.preparing": "🐒⏳",
    "status.progress": "📊",
    "status.baseCalls": "🧠",
    "status.semanticCalls": "🧠✨",
    "status.retries": "🔁",
    "status.budget": "📊",
    "status.time": "⏱️",
    "status.lastActivity": "🕐",
    "status.failure": "🐒💥",
    "status.interrupted": "🦍💥",
    "status.downloadCandidate": "📥🍌",
    "progress.kicker": "🐒🕐",
    "progress.title": "🐒📊",
    "progress.none": "🙈",
    "history.kicker": "🕐🍌",
    "history.title": "📜🍌",
    "history.technical": "🦍⚙️",
    "history.none": "🙈",
    "inbox.kicker": "🐒➡️",
    "inbox.title": "📥🍌",
    "inbox.subtitle": "🐒👉🍌",
    "inbox.refresh": "🔄🍌",
    "inbox.ready": "✅🍌",
    "inbox.review": "🧐🐒",
    "inbox.failed": "🍌💥",
    "inbox.noneCandidate": "🙈",
    "inbox.nonePending": "🙈",
    "inbox.noneRecentFailure": "🙈",
    "inbox.loading": "🐒⏳📥",
    "inbox.unavailable": "🐒💥📥",
    "library.kicker": "📚🍌",
    "library.title": "📚🍌",
    "library.subtitle": "🐒🔍🍌",
    "library.loading": "🐒⏳",
    "library.none": "🙈",
    "library.unavailable": "🐒💥📚",
    "library.detailLoading": "🐒⏳📚",
    "library.detailUnavailable": "🐒💥📚",
    "memory.kicker": "🧠🍌",
    "memory.title": "🧠✅🍌",
    "memory.subtitle": "🐒🧠🍌",
    "memory.none": "🙈",
    "memory.loading": "🐒⏳🧠",
    "memory.unavailable": "🐒💥🧠",
    "memory.openGlossary": "📚🍌",
    "diagnostics.kicker": "🦍🔍",
    "diagnostics.title": "⌨️🐒",
    "diagnostics.subtitle": "🐒💥➡️📜",
    "diagnostics.download": "📥🍌",
    "diagnostics.clear": "🙈",
    "diagnostics.thermalTelemetry": "🌡️👀",
    "diagnostics.empty": "🙈",
    "dialog.library": "📚🍌",
    "dialog.versionDetails": "🔍🍌",
    "dialog.close": "❌",
    "dialog.loading": "🐒⏳",
    "dialog.configuration": "⚙️🐒",
    "dialog.translationMotor": "🐒🔌",
    "dialog.motorIntro": "🐒🧠🍌",
    "dialog.sourceLanguage": "💬❓",
    "dialog.destinationHint": "💬➡️🇧🇷",
    "dialog.primaryMotor": "🐒🔌",
    "dialog.fallback": "🔄🐒",
    "dialog.noFallback": "🙈",
    "dialog.fallbackHint": "🐒🔄💥",
    "dialog.keysNote": "🔐🐒",
    "dialog.testMotor": "🧪🐒",
    "dialog.saveAndTest": "💾🧪🐒",
    "dialog.save": "💾🍌",
    "dialog.firstOpen": "🐒👋🍌",
    "dialog.prepare": "🐒👉⚙️",
    "dialog.prepareIntro": "🐒🤏🍌",
    "dialog.checkingLibrary": "🐒⏳",
    "dialog.checkingMotor": "🐒⏳",
    "dialog.configureMotor": "⚙️🐒",
    "dialog.finish": "🦍🍌🎉",
    "dialog.preflight": "🐒🧪",
    "dialog.beforeTranslate": "🐒👉❓",
    "dialog.preflightIntro": "🐒🧮",
    "dialog.startTranslation": "▶️🐒",
    "footer": "🐒🍌",
    "aria.closeDetails": "Fechar detalhes",
    "aria.sourcePath": "Caminho atual",
    "aria.sourceLanguage": "Idioma de origem da legenda",
    "aria.filterEpisodes": "Filtrar episódios",
    "placeholder.sourceLanguage": "💬❓",
    "placeholder.primaryModel": "🧠🐒",
    "placeholder.fallbackModel": "🔄🧠",
    "placeholder.baseUrl": "🐒🔌"
  };

  const dictionaries = { [FALLBACK]: ptBR, "qi-83": qi83 };
  let current = FALLBACK;

  // QtWebEngine does not guarantee a color-emoji font on Linux bundles.
  // QI 83 therefore stays intentionally icon-only while using a compact
  // monochrome alphabet available in the fonts already shipped by Qt.
  const qi83Icons = new Map([
    ["🐒", "◉"], ["🦍", "◉"], ["🍌", "◒"], ["💬", "▱"],
    ["🔄", "↻"], ["🔁", "↺"], ["⚙", "⚙"], ["🧠", "◆"],
    ["🔌", "⌁"], ["✅", "✓"], ["📍", "◈"], ["📥", "⇩"],
    ["📚", "▤"], ["⌨", "⌨"], ["👉", "›"], ["📁", "□"],
    ["📂", "□"], ["📋", "▤"], ["⬅", "←"], ["⏳", "◴"], ["🧮", "▦"],
    ["📄", "▱"], ["🔨", "⚒"], ["❓", "?"], ["➡", "→"],
    ["🕐", "◷"], ["💥", "!"], ["⛔", "■"], ["🙈", "○"],
    ["🎉", "★"], ["⏸", "Ⅱ"], ["▶", "▶"], ["☠", "×"],
    ["🧪", "◇"], ["🔍", "⌕"], ["🔎", "⌕"], ["🎬", "▸"],
    ["📦", "▧"], ["🤏", "◇"], ["🖼", "▣"], ["🧐", "◎"],
    ["🔀", "⇄"], ["📊", "▥"], ["⏱", "◴"], ["📜", "≡"],
    ["🌡", "△"], ["👀", "◎"], ["🔐", "◆"], ["💾", "▣"],
    ["👋", "◇"], ["✨", "✦"], ["❌", "×"]
  ]);

  function iconizeQi83(value) {
    let source = String(value)
      .replaceAll("1️⃣", "①").replaceAll("2️⃣", "②").replaceAll("3️⃣", "③")
      .replaceAll("🇧🇷", "◇");
    const rendered = Array.from(source).map((character) => {
      if (qi83Icons.has(character)) return qi83Icons.get(character);
      if (/^[\s·—→←✓⚠✕◐]$/.test(character)) return character;
      return "";
    }).join("").replace(/\s+/g, " ").trim();
    return rendered || "◇";
  }

  function normalize(locale) {
    return Object.prototype.hasOwnProperty.call(dictionaries, locale) ? locale : FALLBACK;
  }

  function interpolate(value, vars) {
    return String(value).replace(/\{(\w+)\}/g, (_, key) => vars && vars[key] != null ? String(vars[key]) : `{${key}}`);
  }

  function t(key, vars) {
    const value = dictionaries[current]?.[key] ?? dictionaries[FALLBACK]?.[key] ?? key;
    const interpolated = interpolate(value, vars);
    if (current === "qi-83" && key.startsWith("aria.")) {
      return interpolate(dictionaries[FALLBACK]?.[key] ?? key, vars);
    }
    if (current === "qi-83" && Object.prototype.hasOwnProperty.call(qi83, key)) {
      return iconizeQi83(interpolated);
    }
    return interpolated;
  }

  function apply(root = document) {
    root.querySelectorAll("[data-i18n]").forEach((element) => {
      const value = t(element.dataset.i18n);
      if (element.textContent !== value) element.textContent = value;
    });
    root.querySelectorAll("[data-i18n-title]").forEach((element) => {
      const value = t(element.dataset.i18nTitle);
      if (element.getAttribute("title") !== value) element.setAttribute("title", value);
    });
    root.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
      const value = t(element.dataset.i18nPlaceholder);
      if (element.getAttribute("placeholder") !== value) element.setAttribute("placeholder", value);
    });
    root.querySelectorAll("[data-i18n-aria-label]").forEach((element) => {
      // Accessibility stays in a human language even in the meme locale.
      const vars = element.dataset.i18nName ? { name: element.dataset.i18nName } : undefined;
      const value = t(element.dataset.i18nAriaLabel, vars);
      if (element.getAttribute("aria-label") !== value) element.setAttribute("aria-label", value);
    });
    if (root === document) {
      document.documentElement.lang = current === "qi-83" ? "pt-BR" : current;
      document.documentElement.dataset.locale = current;
    }
    const selector = document.getElementById("uiLanguageSelect");
    if (selector && selector.value !== current) selector.value = current;
  }

  function setLocale(locale) {
    const next = normalize(locale);
    if (next === current) {
      apply();
      return next;
    }
    current = next;
    try { localStorage.setItem(STORAGE_KEY, current); } catch (_) {}
    apply();
    window.dispatchEvent(new CustomEvent("transass:locale-changed", { detail: { locale: current } }));
    return current;
  }

  try { current = normalize(localStorage.getItem(STORAGE_KEY) || FALLBACK); } catch (_) {}
  window.TransassI18n = Object.freeze({ FALLBACK, dictionaries, get locale() { return current; }, t, apply, setLocale });
  document.addEventListener("DOMContentLoaded", () => {
    apply();
    if (document.body && window.MutationObserver) {
      let scheduled = false;
      const observer = new MutationObserver(() => {
        if (scheduled) return;
        scheduled = true;
        window.queueMicrotask(() => { scheduled = false; apply(); });
      });
      observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["title", "placeholder", "aria-label"] });
    }
    const selector = document.getElementById("uiLanguageSelect");
    if (selector) selector.addEventListener("change", () => setLocale(selector.value));
  });
})();
