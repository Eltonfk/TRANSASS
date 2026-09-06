# Assinatura de releases

A assinatura não é executada automaticamente. Quando os certificados forem
 disponibilizados no runner protegido, assine os artefatos depois do checksum:

- Windows: `signtool sign /fd SHA256 /a Transass-Setup-2.5.0.exe`;
- Linux: assinar o AppImage com a chave de release do projeto e publicar a
  assinatura ao lado de `SHA256SUMS`.

As chaves privadas nunca entram no repositório, no bundle ou no SBOM.
