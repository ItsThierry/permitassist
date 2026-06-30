# Deploy approval — Live100 universal fix

- Date/time: 2026-06-30T01:19:51-05:00
- Approver: Boban Kostadinoski
- Approval message: "Deploy approved"
- Scope: Live100 universal customer-view invariant fix, segment-aware resolver constraints, source-backed NOT_REQUIRED preservation, URL-aware leak redaction, and regression coverage.

## Pre-deploy validation

```text
uv run --with pytest --with beautifulsoup4 --with pdfplumber python -m pytest tests/test_universal_deploy_readiness_fixes.py tests/test_live_customer_100_red_contracts_20260629.py tests/test_live100_universal_invariant_fix_20260630.py -q
57 passed in 34.01s
```

```text
python3 -m py_compile api/scope_contract.py api/permit_model.py api/v231_decision_cells.py api/server.py api/decision_resolver.py artifacts/live_customer_100_50_50_20260629T210943Z/live_customer_100_runner.py
exit 0
```

```text
uv run --with pytest --with beautifulsoup4 --with pdfplumber --with requests --with openai --with google-generativeai python -m pytest -q
1135 passed, 1 warning in 253.47s
```

## Prod verification required after deploy

- Railway target SHA is the committed SHA.
- Railway service is Online / Success.
- `/health` returns healthy.
- Live API/customer-view smoke confirms R100-007 REQUIRED and R100-048 NOT_REQUIRED behavior.
- Logs scanned for customer-visible leaks, path leaks, template leakage, and server errors.
