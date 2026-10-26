'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

function fixture(t, kind = 'source') {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'wenda-config-')));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const skill = path.join(root, '.workbuddy/skills/lx-wenda');
  fs.mkdirSync(skill, { recursive: true });
  fs.mkdirSync(path.join(root, 'config'));
  for (const name of ['client.js', 'ask.js', 'server.js', 'selftest.js']) {
    fs.copyFileSync(path.join(__dirname, name), path.join(skill, name));
  }
  const source = { version: 1, targets: { FOG: { repository: 'pxiaolei/FOG' },
    lxworkspace: { repository: 'pxiaolei/lxworkspace' } } };
  if (kind === 'source') {
    fs.writeFileSync(path.join(root, 'config/distribution-manifest.json'), JSON.stringify(source));
  } else if (kind !== 'unknown') {
    fs.writeFileSync(path.join(root, 'config/distribution.json'), JSON.stringify({
      version: 1, kind, repository: 'pxiaolei/' + kind,
      skills: kind === 'lxworkspace' ? ['lx-wenda'] : []
    }));
  }
  const privateConfig = path.join(skill, 'config.json');
  const rootConfig = path.join(root, 'config/lx-wenda.json');
  const config = base => JSON.stringify({ base, token: 'fictional-config-token', timeout_ms: 120000 });
  fs.writeFileSync(privateConfig, config('https://private.invalid'));
  fs.writeFileSync(rootConfig, config('https://partner.invalid'));
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('MAXKB_')));
  function run(args = [], extra = {}, script = 'ask.js') {
    return spawnSync(process.execPath, [path.join(skill, script), ...(script === 'ask.js' ? ['fixture question'] : []), ...args],
      { cwd: os.tmpdir(), env: { ...env, ...extra }, encoding: 'utf8', timeout: 5000 });
  }
  return { root, skill, privateConfig, rootConfig, config, env, run, source };
}

test('private entry ignores inherited service identity but uses its own selected file', t => {
  const f = fixture(t);
  const result = f.run([], { MAXKB_BASE: 'https://wrong-account.invalid', MAXKB_TOKEN: 'poison' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).service, 'https://private.invalid');
  assert.doesNotMatch(result.stdout + result.stderr, /fictional-config-token|poison/);
});

test('partner entry cannot read the source-style decoy next to the skill', t => {
  const f = fixture(t, 'lxworkspace');
  const result = f.run();
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).service, 'https://partner.invalid');
});

test('missing selected config never falls back to the other file or a default service', t => {
  const f = fixture(t);
  fs.unlinkSync(f.privateConfig);
  const result = f.run();
  assert.notEqual(result.status, 0);
  assert.doesNotMatch(result.stdout, /preview_hash/);
});

test('an inherited different config is rejected before it can select another account', t => {
  const f = fixture(t);
  assert.notEqual(f.run([], { MAXKB_CONFIG: f.rootConfig }).status, 0);
  assert.equal(f.run([], { MAXKB_CONFIG: f.privateConfig }).status, 0);
});

test('unknown, conflicting, damaged and disabled workspace identities fail closed', t => {
  for (const kind of ['unknown', 'FOG', 'conflict', 'damaged']) {
    const f = fixture(t, kind === 'conflict' || kind === 'damaged' ? 'source' : kind);
    if (kind === 'conflict') fs.writeFileSync(path.join(f.root, 'config/distribution.json'), '{}');
    if (kind === 'damaged') fs.writeFileSync(path.join(f.root, 'config/distribution-manifest.json'), '{broken');
    assert.notEqual(f.run().status, 0, kind);
  }
});

test('invalid JSON or missing fields does not produce a successful preview or expose contents', t => {
  const f = fixture(t);
  for (const text of ['{fictional-private-content', '{}', JSON.stringify({ base: 'https://fixture.invalid', token: '' })]) {
    fs.writeFileSync(f.privateConfig, text);
    const result = f.run();
    assert.notEqual(result.status, 0);
    assert.doesNotMatch(result.stdout + result.stderr, /fictional-private-content/);
  }
});

test('selected config symlink is rejected', t => {
  const f = fixture(t);
  fs.unlinkSync(f.privateConfig);
  fs.symlinkSync(f.rootConfig, f.privateConfig);
  assert.notEqual(f.run().status, 0);
});

test('explicit maintenance config is chosen without inherited service overrides', t => {
  const f = fixture(t, 'unknown');
  const outside = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'wenda-maintenance-')));
  t.after(() => fs.rmSync(outside, {recursive:true, force:true}));
  const file = path.join(outside, 'explicit.json');
  fs.writeFileSync(file, f.config('https://partner.invalid'));
  const result = f.run(['--config', file], { MAXKB_BASE: 'https://wrong.invalid', MAXKB_TOKEN: 'poison' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).service, 'https://partner.invalid');
  assert.notEqual(f.run(['--config', 'relative.json']).status, 0);
});

test('default selftest neither reads config contents nor makes network requests', t => {
  const f = fixture(t);
  const hook = path.join(f.root, 'read-guard.js');
  fs.writeFileSync(hook, `
    const fs = require('fs');
    const read = fs.readFileSync;
    fs.readFileSync = function(p, ...args) {
      if (/(?:config\\.json|lx-wenda\\.json)$/.test(String(p))) throw new Error('forbidden config read');
      return read.call(this, p, ...args);
    };
    for (const method of ['writeFileSync','appendFileSync','mkdirSync','renameSync','unlinkSync']) {
      fs[method] = () => { throw new Error('forbidden persistent write'); };
    }
    for (const mod of ['http', 'https']) require(mod).request = () => { throw new Error('forbidden network'); };
  `);
  const result = f.run([], { NODE_OPTIONS: '--require ' + JSON.stringify(hook) }, 'selftest.js');
  assert.equal(result.status, 0, result.stderr + result.stdout);
  assert.match(result.stdout, /服务调用 0 次/);
});

test('MCP uses the same workspace selection and explicit maintenance boundary', t => {
  for (const kind of ['source', 'lxworkspace', 'unknown']) {
    const f = fixture(t, kind);
    const call = args => spawnSync(process.execPath, [path.join(f.skill, 'server.js'), ...args], {
      input: JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'ask_knowledge_base',arguments:{question:'fixture'}}}) + '\n',
      env: {...f.env, MAXKB_BASE:'https://wrong.invalid'}, encoding:'utf8', timeout:5000
    });
    const normal = JSON.parse(call([]).stdout).result;
    if (kind === 'unknown') assert.equal(normal.isError, true);
    else assert.equal(normal.structuredContent.service, kind === 'source' ? 'https://private.invalid' : 'https://partner.invalid');
    assert.equal(JSON.parse(call(['--config', f.rootConfig]).stdout).result.structuredContent.service, 'https://partner.invalid');
  }
});

test('mismatched dispatcher context and linked identity metadata stop before preview', t => {
  const f = fixture(t);
  assert.notEqual(f.run([], {MAXKB_WORKSPACE_ROOT:os.tmpdir()}).status, 0);
  const marker = path.join(f.root, 'config/distribution-manifest.json');
  fs.renameSync(marker, marker + '.fixture');
  fs.symlinkSync(marker + '.fixture', marker);
  assert.notEqual(f.run().status, 0);
});

test('invalid service URLs and timeouts stop without exposing file contents', t => {
  const f = fixture(t);
  for (const fields of [{base:'https://fictional-user:fictional-password@example.invalid'},
    {base:'https://example.invalid?fictional-private-query'}, {base:'file:///fictional-private-path'}, {timeout_ms:0}]) {
    fs.writeFileSync(f.privateConfig, JSON.stringify({base:'https://example.invalid',token:'fictional-token',...fields}));
    const result = f.run();
    assert.notEqual(result.status, 0);
    assert.doesNotMatch(result.stdout + result.stderr, /fictional-(?:user|password|private-query|private-path|token)/);
  }
});
