# Recovery and rollback

P2B3B proved the local operational rollback artifact
`subtranslate:rollback-pre-p2c-20260813T000000Z`, which points to the current
live production image. It is an operational artifact, not a Git release.

Future rollback uses the exact image tag/digest, preserved compose/config and
validated persistent state. No rollback is executed by P2C2.
