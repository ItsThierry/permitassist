# PermitAssist sensitive-path posture

Date: 2026-07-06
Scope: local-only documentation for Fable 5 Phase 7. No production data, Railway volume, or paid/live API was accessed.

## Do not open or scan contents

These paths may contain personal or lead data. Audits may list path names and metadata, but must not read contents unless Boban separately approves a scoped PII review.

- `marketing/lead-registry/ready_lead_registry.sqlite`
- `marketing/lead-registry/`
- `marketing/ready-to-send/`
- `marketing/internal-review/`
- `marketing/*.csv`
- `data/cache.db` and copies/backups when sourced from production or user sessions
- `.env`, `.env.*`, `.env.bak-*`

## Local-only DB split boundary

`scripts/split_app_cache_db.py` is a local dry-run helper only. Running it against Railway volume data, swapping app DB paths, or taking a production backup is approval-gated.

## verified_cities carry-forward

Until T-073 canonicalization, runtime DB copies are the authority:

- `data/verified_cities.db`
- `knowledge/verified_cities.db`

The JSON copies are non-authoritative evidence artifacts and must not be silently merged into the runtime DB record set.
