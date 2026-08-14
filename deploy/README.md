# Production layout candidate

This directory describes the future controlled-deploy layout. It is not the
live production configuration and it is not an authorization to deploy.

`compose.yaml` consumes a versioned image and keeps media and operational state
outside the image. Python source, tests, documentation, glossaries, and the
historical review workspace are not bind-mounted. The real `/docker/subtranslate`
tree remains the live authority until P2C4 and an explicit controlled-deploy
decision.

Validate the file with a safe environment before any future deployment:

```sh
docker compose -f deploy/compose.yaml config
```

The `.env` file and persistent state are host-local operational data and must
never be committed to Git.
