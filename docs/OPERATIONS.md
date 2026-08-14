# Operations

The intended future sequence is:

`Git repository → versioned image build → isolated smoke → controlled deploy`.

The current live production container and image are not replaced by this
repository. Production state, media, Library/TM/glossary state and secrets are
external boundaries. A clean deployment must identify version, commit, image
digest, effective pipeline and effective model before any authority switch.
