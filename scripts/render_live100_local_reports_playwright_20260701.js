#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

async function main() {
  const manifestPath = process.argv[2] || '/home/boban/projects/permitassist/artifacts/live100_no_neuter_action_path_fix_20260701_local/local_render_manifest.json';
  const outRoot = path.dirname(manifestPath);
  const rows = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  const browser = await chromium.launch({ headless: true });
  const records = [];
  const aggregate = {
    total: rows.length,
    render_ok: 0,
    screenshots: 0,
    decision_visible_match: 0,
    residential_commercial_timeline_leaks: 0,
    secret_leaks: 0,
    console_errors: 0,
  };
  const secretRe = /(PERMITASSIST_[A-Z0-9_]+|RAILWAY_[A-Z0-9_]+|OPENAI_API_KEY|ANTHROPIC_API_KEY|sk-[A-Za-z0-9_-]{16,}|pa_session[=:][A-Za-z0-9._-]+|authorization\s*:\s*bearer)/i;
  for (const row of rows) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1600 }, deviceScaleFactor: 1 });
    const consoleErrors = [];
    page.on('console', msg => { if (['error'].includes(msg.type())) consoleErrors.push(msg.text()); });
    page.on('pageerror', err => consoleErrors.push(String(err && err.message || err)));
    const fileUrl = 'file://' + path.resolve(row.html_path);
    let renderOk = false;
    let text = '';
    try {
      await page.goto(fileUrl, { waitUntil: 'networkidle', timeout: 30000 });
      await page.waitForTimeout(250);
      text = await page.locator('body').innerText({ timeout: 10000 });
      renderOk = text.length > 500 && /permit/i.test(text);
      await page.screenshot({ path: row.screenshot_path, fullPage: true });
    } catch (err) {
      consoleErrors.push(String(err && err.message || err));
    } finally {
      await page.close();
    }
    const lower = text.toLowerCase();
    const visibleYes = /permit required/i.test(text) || /required permit package/i.test(text);
    const visibleNo = /no permit required|permit not required|not required/i.test(text);
    const decisionMatch = row.decision === 'REQUIRED' ? (visibleYes && !(/^no permit required/i.test(text.trim()))) : visibleNo;
    const timelineLeak = row.segment === 'residential' && lower.includes('commercial ti/addition/remodel scopes usually require plan review');
    const secretLeak = secretRe.test(text);
    if (renderOk) aggregate.render_ok += 1;
    if (fs.existsSync(row.screenshot_path)) aggregate.screenshots += 1;
    if (decisionMatch) aggregate.decision_visible_match += 1;
    if (timelineLeak) aggregate.residential_commercial_timeline_leaks += 1;
    if (secretLeak) aggregate.secret_leaks += 1;
    if (consoleErrors.length) aggregate.console_errors += 1;
    records.push({
      case_id: row.case_id,
      segment: row.segment,
      city: row.city,
      state: row.state,
      decision: row.decision,
      render_ok: renderOk,
      body_text_len: text.length,
      decision_visible_match: decisionMatch,
      residential_commercial_timeline_leak: timelineLeak,
      secret_leak: secretLeak,
      console_errors: consoleErrors.slice(0, 5),
      html_path: row.html_path,
      screenshot_path: row.screenshot_path,
      body_text_sample: text.slice(0, 500),
    });
  }
  await browser.close();
  const output = { aggregate, records };
  fs.writeFileSync(path.join(outRoot, 'LOCAL_RENDERED_VERIFICATION.json'), JSON.stringify(output, null, 2));
  const md = [
    '# Local Chromium rendered verification',
    '',
    `- Total: \`${aggregate.total}\``,
    `- Render OK: \`${aggregate.render_ok}\``,
    `- Screenshots: \`${aggregate.screenshots}\``,
    `- Decision visible match: \`${aggregate.decision_visible_match}\``,
    `- Residential commercial timeline leaks: \`${aggregate.residential_commercial_timeline_leaks}\``,
    `- Secret leaks: \`${aggregate.secret_leaks}\``,
    `- Console-error cases: \`${aggregate.console_errors}\``,
    '',
    '## Non-OK cases',
  ];
  for (const r of records) {
    if (!r.render_ok || !r.decision_visible_match || r.residential_commercial_timeline_leak || r.secret_leak || r.console_errors.length) {
      md.push(`- **${r.case_id}** render_ok=${r.render_ok} decision_match=${r.decision_visible_match} timeline_leak=${r.residential_commercial_timeline_leak} secret=${r.secret_leak} console=${r.console_errors.join(' | ')}`);
    }
  }
  if (md[md.length - 1] === '## Non-OK cases') md.push('- none');
  fs.writeFileSync(path.join(outRoot, 'LOCAL_RENDERED_VERIFICATION.md'), md.join('\n') + '\n');
  console.log(JSON.stringify(aggregate, null, 2));
  if (aggregate.render_ok !== aggregate.total || aggregate.screenshots !== aggregate.total || aggregate.residential_commercial_timeline_leaks || aggregate.secret_leaks) process.exit(1);
}

main().catch(err => { console.error(err); process.exit(1); });
