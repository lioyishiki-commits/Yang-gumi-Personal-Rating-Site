# Release contents

The public release contains the complete runnable application:

- all Python source modules and Windows/macOS/Linux launchers;
- pinned dependency declarations;
- the Streamlit 1.58.0 old-Edge compatibility frontend;
- empty runtime directory structure and the default fallback cover;
- the full test suite;
- deployment, privacy, security, contribution, user, and complete DOCX guides.

The following local runtime state is deliberately excluded from both Git and
release ZIP files because it may contain private user information:

- `data/*` except `.gitkeep`;
- `backups/*` and `exports/*` except `.gitkeep`;
- user covers, backgrounds, daily-art images, and generated poster caches;
- tokens, `.env`, Streamlit secrets, databases, logs, temporary files, and
  Python bytecode;
- local Git metadata and original-computer absolute paths.

Excluding these files does not remove application functionality. On first
launch, Yang-gumi creates a fresh empty database and all required runtime files.
