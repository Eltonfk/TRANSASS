# Testes Desktop

Os testes desta pasta cobrirão comportamento específico de instalação e
runtime, sem chamadas reais de modelos:

- resolução de diretórios Windows/Linux;
- descoberta de `ffmpeg`/`ffprobe` empacotados;
- porta local e encerramento do servidor (12 testes da Fase 2);
- migração e preservação do estado;
- single-instance;
- smoke test do bundle congelado;
- instalação, atualização e desinstalação em máquinas limpas.

Após gerar um bundle `onedir`, o smoke congelado pode ser executado com:

```sh
python desktop/tests/run_bundle_smoke.py build/desktop/Transass/Transass
```

Ele valida a consulta de versão, o manifesto de `ffmpeg`/`ffprobe`, o servidor
HTTP local e o estado inicial do onboarding sem chamar modelos.
