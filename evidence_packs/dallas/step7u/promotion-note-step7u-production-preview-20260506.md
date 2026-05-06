# Dallas Step 7U production-preview evidence pack note

Scope: separately reviewed production-preview contract artifact only. Production remains HOLD; no Railway production env vars were modified and no production deploy is authorized by this file.

Source staging pack: `evidence_packs/dallas/step7h/permitassist-step7h-dallas-only-staging-evidence-pack-20260505.json`
Production-preview pack: `evidence_packs/dallas/step7u/permitassist-step7u-dallas-only-production-preview-evidence-pack-20260506.json`
Runtime Railway path if later approved: `/app/evidence_packs/dallas/step7u/permitassist-step7u-dallas-only-production-preview-evidence-pack-20260506.json`

Raw SHA-256: `dd099d0197ddee6b1e8aa343cc109285461877b5253766041e60335e1c1b1e57`
Metadata fingerprint: `2f46defd28b2a06c227735e4ef46f98575422923ca76e0cae3acb410f60aa7e1`
Evidence pack version: `step7u_dallas_only_production_preview_v1`
Mode: `dallas_step7u_production_preview`
Production wiring allowed: `true` (contract assertion for this separate preview pack only; not deployment authorization and not active without later Railway production env/deploy approval)
Records: `10`

Guardrail: the Step 7H staging pack stays `production_wiring_allowed=false`; production preview requires this separate Step 7U version/mode/fingerprint and a later explicit production promotion approval.
