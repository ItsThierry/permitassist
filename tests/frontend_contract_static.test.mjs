import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const indexHtml = fs.readFileSync(new URL('../frontend/index.html', import.meta.url), 'utf8');
const reportHtml = fs.readFileSync(new URL('../frontend/report.html', import.meta.url), 'utf8');
const serverPy = fs.readFileSync(new URL('../api/server.py', import.meta.url), 'utf8');
const corePy = fs.readFileSync(new URL('../api/permit_rule_engine.py', import.meta.url), 'utf8');

test('customer UI reads canonical permit_decision', () => {
  assert.match(indexHtml, /d\.permit_decision/);
});

test('required decisions map to positive required state', () => {
  assert.match(indexHtml, /t==='YES'\|\|t==='REQUIRED'\|\|t==='PERMIT_REQUIRED'/);
});

test('not-required decisions require positive no-permit evidence', () => {
  assert.match(indexHtml, /hasPositiveNoPermitEvidence\(d\) \? 'no' : 'maybe'/);
});

test('unknown decisions map to non-binary maybe state', () => {
  assert.match(indexHtml, /t==='MAYBE'\|\|t==='UNKNOWN'/);
});

test('unknown customer label remains verify-required', () => {
  assert.match(indexHtml, /raw==='UNKNOWN'.*return 'VERIFY REQUIRED'/s);
});

test('unsafe negative label is downgraded when evidence is absent', () => {
  assert.match(indexHtml, /hasPositiveNoPermitEvidence\(d\) \? 'NOT REQUIRED' : 'CHECK REQUIRED'/);
});

test('report uses a non-executable JSON data script', () => {
  assert.match(reportHtml, /<script id="report-data" type="application\/json">__REPORT_DATA__<\/script>/);
});

test('report parses data from textContent rather than executable interpolation', () => {
  assert.match(reportHtml, /JSON\.parse\(document\.getElementById\('report-data'\)\.textContent\)/);
});

test('report defaults a missing decision to VERIFY', () => {
  assert.match(reportHtml, /d\.permit_required === false \? 'NO' : 'VERIFY'/);
});

test('report renders non-binary decision as verify', () => {
  assert.match(reportHtml, /Permit status: Verify/);
});

test('report summary prefers actionable customer copy over raw coverage diagnostics', () => {
  assert.match(reportHtml, /d\.customer_next_step \|\| d\.customer_headline/);
  assert.doesNotMatch(reportHtml, /d\.summary \|\|/);
});

test('report does not synthesize application links from empty unresolved routes', () => {
  assert.match(reportHtml, /const raw = String\(url \|\| ''\)\.trim\(\)/);
  assert.match(reportHtml, /if \(!raw\) return fallback/);
});

test('report uses actionable verification tasks and humanizes machine trigger labels', () => {
  assert.match(reportHtml, /safeArray\(d\.verification_tasks\)/);
  assert.match(reportHtml, /task\.action/);
  assert.match(reportHtml, /replace\(\/\[_-\]\+\/g, ' '\)/);
});

test('report renders every canonical family decision as a visible matrix row', () => {
  assert.match(reportHtml, /safeArray\(d\.family_decisions\)/);
  assert.match(reportHtml, /Permit decision matrix/);
  assert.match(reportHtml, /decision-matrix/);
  assert.match(reportHtml, /decision-row/);
});

test('report fallback preserves one-to-one permit rows instead of rematching the first row', () => {
  assert.match(reportHtml, /row\.family \|\| row\.filing_family \|\| row\.permit_kind/);
  assert.match(reportHtml, /const permitRow = canonicalFamilyRows\.length[\s\S]*\? \(permitRows\.find[\s\S]*: row;/);
});

test('every customer server surface uses the one strict single-projection boundary', () => {
  assert.equal((serverPy.match(/project_core_customer_boundary\(/g) || []).length, 1);
  assert.ok((serverPy.match(/_project_core_customer_boundary_once\(/g) || []).length >= 7);
  assert.match(serverPy, /class _VerifiedCustomerProjection\(dict\)/);
  assert.match(serverPy, /has_intact_regulated_projection/);
  assert.doesNotMatch(serverPy, /def _rehydrate_cached_verified_core_projection/);
});

test('all owner-bound browser continuity requests forward authentication headers', () => {
  assert.match(
    indexHtml,
    /fetch\('\/api\/share',[\s\S]*?headers:\s*\{\s*\.\.\.getAuthHeaders\(\),\s*'Content-Type':\s*'application\/json'\s*\}/,
  );
  assert.match(
    indexHtml,
    /fetch\('\/api\/checklist',[\s\S]*?headers:\s*\{\s*\.\.\.getAuthHeaders\(\),\s*'Content-Type':\s*'application\/json'\s*\}/,
  );
  const emailReportCalls = indexHtml.match(
    /fetch\('\/api\/email-report',[\s\S]*?headers:\s*\{\s*\.\.\.getAuthHeaders\(\),\s*'Content-Type':\s*'application\/json'\s*\}/g,
  ) || [];
  assert.equal(emailReportCalls.length, 2);
});

test('shared public results use the v2 hash-sealed schema', () => {
  assert.match(serverPy, /SHARED_RESULT_SCHEMA_VERSION = "permitassist\.shared-result\.v2"/);
});

test('shared public result retrieval verifies SHA-256 before JSON use', () => {
  assert.match(serverPy, /hashlib\.sha256\(payload_json\.encode\("utf-8"\)\)\.hexdigest\(\) != payload_hash/);
});

test('tampered core decisions enter explicit integrity fail-closed path', () => {
  assert.match(corePy, /issue_code = "decision_integrity_validation_failed"/);
});

test('integrity fail-closed keeps family lanes visible as abstentions', () => {
  assert.match(corePy, /"verdict": FamilyVerdict\.ABSTAIN\.value/);
  assert.match(corePy, /"verification_tasks": verification_tasks/);
});

test('core fail-closed customer projection cannot publish a binary answer', () => {
  assert.match(corePy, /"permit_required": None/);
  assert.match(corePy, /"permit_decision": "UNKNOWN"/);
  assert.match(corePy, /"permit_verdict": "CONTACT_AHJ"/);
});
