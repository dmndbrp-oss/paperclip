# Knowledge store CLI

The canonical CLI entry point is `knowledge-store`, published by this package
from `scripts/knowledge-cli.ts`.

Invoke it through npm so the package-managed executable is discoverable without
manual `PATH` modification:

```sh
printf '%s\n' '...' | npm exec -- knowledge-store validate
printf '%s\n' '...' | npm exec -- knowledge-store write
```

The command accepts `write` or `validate` and reads one YAML knowledge entry
from stdin. `validate` prints `valid` on success and writes nothing. `write`
requires either `KNOWLEDGE_BASE_DIR`, or both `HOME` and
`PAPERCLIP_COMPANY_ID`; it writes the task YAML and the domain/specialty JSONL
index pointers below the resolved knowledge-base directory.

For a disposable persistence check, set `KNOWLEDGE_BASE_DIR` to a temporary
directory before invoking `write` and validate the returned path and generated
files there.
