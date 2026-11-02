---
paths:
  - "apps/platform/src/recon/migrations/**"
---
# Adding an Alembic migration

- **Revision id ≤ 32 chars** (`alembic_version.version_num` is `varchar(32)`). Abbreviate the
  tail, keep the `NNNN_` prefix; the docstring can spell out the full name.
- **`down_revision` = the current single head** (the latest `NNNN_*.py`). Two branches adding off
  the same parent make two heads and `upgrade head` refuses.
- **Guard column adds with `ADD COLUMN IF NOT EXISTS`.** Migration `0001` runs
  `Base.metadata.create_all` from the live models, so on a fresh DB/CI the column already exists
  and a plain `add_column` fails with DuplicateColumn (pattern: 0003/0009/0010/0019).

The first two are enforced by `migrations/revisions_test.py` (fast lane); the third is not.
