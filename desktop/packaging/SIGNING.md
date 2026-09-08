# Assinatura de artefatos

O CI gera checksums e SBOM, mas não assina sem certificados protegidos.

- Windows: assinar `Transass.exe`, `Transass-Setup-2.5.2.exe` e desinstalador
  com `signtool` e SHA-256.
- Linux: assinar o AppImage e publicar a assinatura ao lado de `SHA256SUMS`.

Chaves privadas nunca entram no repositório, bundle, log ou SBOM. Até existir
assinatura oficial, o checksum publicado é obrigatório e o alerta SmartScreen
é esperado.
