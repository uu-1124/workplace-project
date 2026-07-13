import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomBytes } from 'node:crypto'

const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const repositoryRoot = resolve(scriptDirectory, '..', '..')
const validator = resolve(scriptDirectory, 'validate-pr-candidate.mjs')
const bundleTool = resolve(scriptDirectory, 'encrypted-release-bundle.mjs')
const temporaryRoot = mkdtempSync(resolve(tmpdir(), 'soloops-pr-gate-'))
const fixtureRoot = resolve(temporaryRoot, 'fixture')
const BASE_API_SHA = '1'.repeat(40)
const BASE_DESKTOP_SHA = '2'.repeat(40)
const NEXT_API_SHA = '3'.repeat(40)

after(() => {
  if (!temporaryRoot.startsWith(resolve(tmpdir()))) throw new Error('refusing to clean a non-temporary test directory')
  rmSync(temporaryRoot, { recursive: true, force: true })
})

function git(cwd, args) {
  return execFileSync('git', args, { cwd, encoding: 'utf8' }).trim()
}

function manifest(sourceCommit, apiCommit = BASE_API_SHA, desktopCommit = BASE_DESKTOP_SHA) {
  return {
    release_id: '2026.07.13-r0.28',
    released_at: '2026-07-13',
    components: {
      root: { source_commit: sourceCommit },
      api: { commit: apiCommit, runtime_version: '1.0.1', migration_head: '20260712_0031' },
      desktop: { commit: desktopCommit, package_version: '2.0.1' },
    },
    release_gate: {
      default_scope: 'phase-one',
      command: 'npm run test:release -- --prefix release',
      full_launch_compatibility: 'npm run test:release -- --base-url http://example-host:8000 --prefix compatibility --include-full-launch',
    },
  }
}

function writeJson(path, value) {
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, 'utf8')
}

mkdirSync(fixtureRoot, { recursive: true })
git(fixtureRoot, ['init', '--initial-branch=main'])
git(fixtureRoot, ['config', 'user.name', 'PR Gate Test'])
git(fixtureRoot, ['config', 'user.email', 'pr-gate@example.invalid'])
writeFileSync(resolve(fixtureRoot, '.gitmodules'), [
  '[submodule "workplace-api"]',
  '\tpath = workplace-api',
  '\turl = https://github.com/uu-1124/workplace-api.git',
  '[submodule "workplace-desktop-electron"]',
  '\tpath = workplace-desktop-electron',
  '\turl = https://github.com/uu-1124/workplace-desktop-electron.git',
  '',
].join('\n'), 'utf8')
mkdirSync(resolve(fixtureRoot, 'docs'), { recursive: true })
writeFileSync(resolve(fixtureRoot, 'docs', 'source.md'), 'trusted source\n', 'utf8')
git(fixtureRoot, ['add', '.gitmodules', 'docs/source.md'])
git(fixtureRoot, ['update-index', '--add', '--cacheinfo', `160000,${BASE_API_SHA},workplace-api`])
git(fixtureRoot, ['update-index', '--add', '--cacheinfo', `160000,${BASE_DESKTOP_SHA},workplace-desktop-electron`])
git(fixtureRoot, ['commit', '-m', 'source'])
const sourceCommit = git(fixtureRoot, ['rev-parse', 'HEAD'])
writeJson(resolve(fixtureRoot, 'release-manifest.json'), manifest(sourceCommit))
git(fixtureRoot, ['add', 'release-manifest.json'])
git(fixtureRoot, ['commit', '-m', 'baseline'])
const baseCommit = git(fixtureRoot, ['rev-parse', 'HEAD'])

function createCase(name, mutate) {
  const caseRoot = resolve(temporaryRoot, name)
  git(temporaryRoot, ['clone', '--no-recurse-submodules', fixtureRoot, caseRoot])
  git(caseRoot, ['config', 'user.name', 'PR Gate Test'])
  git(caseRoot, ['config', 'user.email', 'pr-gate@example.invalid'])
  mutate(caseRoot)
  git(caseRoot, ['add', '-A'])
  git(caseRoot, ['commit', '-m', name])
  return { caseRoot, headCommit: git(caseRoot, ['rev-parse', 'HEAD']) }
}

function validate(caseRoot, headCommit, { allowPrivileged = false } = {}) {
  const output = resolve(caseRoot, 'policy-output.txt')
  const result = spawnSync(process.execPath, [
    validator,
    '--root', caseRoot,
    '--base', baseCommit,
    '--head', headCommit,
    '--output', output,
    '--allow-privileged', String(allowPrivileged),
  ], { cwd: caseRoot, encoding: 'utf8' })
  const parsedOutput = existsSync(output)
    ? Object.fromEntries(readFileSync(output, 'utf8').trim().split('\n').filter(Boolean).map(line => line.split('=', 2)))
    : {}
  return { ...result, parsedOutput }
}

test('documentation-only PR passes without requesting private source', () => {
  const { caseRoot, headCommit } = createCase('docs-only', root => {
    appendFileSync(resolve(root, 'docs', 'source.md'), 'documentation update\n', 'utf8')
  })
  const result = validate(caseRoot, headCommit)
  assert.equal(result.status, 0, result.stderr)
  assert.equal(result.parsedOutput.requires_full_gate, 'false')
  assert.equal(result.parsedOutput.api_sha, BASE_API_SHA)
})

test('release gitlink and manifest update requests the full private gate', () => {
  const { caseRoot, headCommit } = createCase('release-update', root => {
    git(root, ['update-index', '--add', '--cacheinfo', `160000,${NEXT_API_SHA},workplace-api`])
    writeJson(resolve(root, 'release-manifest.json'), manifest(sourceCommit, NEXT_API_SHA))
  })
  const result = validate(caseRoot, headCommit)
  assert.equal(result.status, 0, result.stderr)
  assert.equal(result.parsedOutput.requires_full_gate, 'true')
  assert.equal(result.parsedOutput.api_sha, NEXT_API_SHA)
  assert.equal(result.parsedOutput.base_api_sha, BASE_API_SHA)
})

test('unlabelled workflow changes are rejected by the base policy', () => {
  const { caseRoot, headCommit } = createCase('workflow-change', root => {
    mkdirSync(resolve(root, '.github', 'workflows'), { recursive: true })
    writeFileSync(resolve(root, '.github', 'workflows', 'untrusted.yml'), 'name: untrusted\n', 'utf8')
  })
  const result = validate(caseRoot, headCommit)
  assert.notEqual(result.status, 0)
  assert.match(result.stderr, /trusted-ci-change/)
})

test('administrator-labelled workflow changes still require the full gate', () => {
  const { caseRoot, headCommit } = createCase('approved-workflow-change', root => {
    mkdirSync(resolve(root, '.github', 'workflows'), { recursive: true })
    writeFileSync(resolve(root, '.github', 'workflows', 'reviewed.yml'), 'name: reviewed\n', 'utf8')
  })
  const result = validate(caseRoot, headCommit, { allowPrivileged: true })
  assert.equal(result.status, 0, result.stderr)
  assert.equal(result.parsedOutput.privileged_change, 'true')
  assert.equal(result.parsedOutput.requires_full_gate, 'true')
})

test('even a labelled PR cannot redirect private submodules', () => {
  const { caseRoot, headCommit } = createCase('redirect-submodule', root => {
    const path = resolve(root, '.gitmodules')
    writeFileSync(path, readFileSync(path, 'utf8').replace('uu-1124/workplace-api.git', 'attacker/private-copy.git'), 'utf8')
  })
  const result = validate(caseRoot, headCommit, { allowPrivileged: true })
  assert.notEqual(result.status, 0)
  assert.match(result.stderr, /two approved GitHub repositories/)
})

test('gitlink and manifest disagreement is rejected before credentials exist', () => {
  const { caseRoot, headCommit } = createCase('gitlink-mismatch', root => {
    git(root, ['update-index', '--add', '--cacheinfo', `160000,${NEXT_API_SHA},workplace-api`])
  })
  const result = validate(caseRoot, headCommit)
  assert.notEqual(result.status, 0)
  assert.match(result.stderr, /API gitlink differs/)
})

test('replacing an approved gitlink with a normal directory is rejected', () => {
  const { caseRoot, headCommit } = createCase('replace-gitlink', root => {
    git(root, ['rm', '--cached', 'workplace-api'])
    mkdirSync(resolve(root, 'workplace-api'), { recursive: true })
    writeFileSync(resolve(root, 'workplace-api', 'fake.py'), 'print("not private source")\n', 'utf8')
  })
  const result = validate(caseRoot, headCommit)
  assert.notEqual(result.status, 0)
  assert.match(result.stderr, /exactly the two approved/)
})

test('AES-GCM bundle round-trip succeeds and rejects tampering', () => {
  const input = resolve(temporaryRoot, 'bundle-input.bin')
  const encrypted = resolve(temporaryRoot, 'bundle.enc')
  const decrypted = resolve(temporaryRoot, 'bundle-output.bin')
  const tampered = resolve(temporaryRoot, 'bundle-tampered.enc')
  const tamperedOutput = resolve(temporaryRoot, 'bundle-tampered-output.bin')
  const key = randomBytes(32).toString('hex')
  writeFileSync(input, Buffer.concat([Buffer.from('private source fixture\n'), randomBytes(2 * 1024 * 1024)]))

  const encryptResult = spawnSync(process.execPath, [bundleTool, 'encrypt', '--input', input, '--output', encrypted], {
    encoding: 'utf8',
    env: { ...process.env, RELEASE_BUNDLE_KEY: key },
  })
  assert.equal(encryptResult.status, 0, encryptResult.stderr)
  const digest = JSON.parse(encryptResult.stdout).sha256

  const decryptResult = spawnSync(process.execPath, [
    bundleTool,
    'decrypt',
    '--input', encrypted,
    '--output', decrypted,
    '--expected-sha256', digest,
  ], { encoding: 'utf8', env: { ...process.env, RELEASE_BUNDLE_KEY: key } })
  assert.equal(decryptResult.status, 0, decryptResult.stderr)
  assert.deepEqual(readFileSync(decrypted), readFileSync(input))

  const bytes = readFileSync(encrypted)
  bytes[Math.floor(bytes.length / 2)] ^= 0xff
  writeFileSync(tampered, bytes)
  const tamperedResult = spawnSync(process.execPath, [
    bundleTool,
    'decrypt',
    '--input', tampered,
    '--output', tamperedOutput,
  ], { encoding: 'utf8', env: { ...process.env, RELEASE_BUNDLE_KEY: key } })
  assert.notEqual(tamperedResult.status, 0)
  assert.equal(existsSync(tamperedOutput), false)
})

test('workflow contract keeps credentials out of candidate test jobs and pins actions', () => {
  const prWorkflow = readFileSync(resolve(repositoryRoot, '.github', 'workflows', 'pr-release-readiness-gate.yml'), 'utf8')
  const pushWorkflow = readFileSync(resolve(repositoryRoot, '.github', 'workflows', 'release-readiness-gate.yml'), 'utf8')
  assert.match(prWorkflow, /pull_request_target:/)
  assert.doesNotMatch(prWorkflow, /^\s+pull_request:\s*$/m)

  for (const workflow of [prWorkflow, pushWorkflow]) {
    const uses = [...workflow.matchAll(/uses:\s+actions\/[^@\s]+@([^\s#]+)/g)]
    assert.ok(uses.length > 0)
    for (const match of uses) assert.match(match[1], /^[0-9a-f]{40}$/)
  }

  const brokerStart = prWorkflow.indexOf('\n  source-bundle:')
  const linuxStart = prWorkflow.indexOf('\n  postgres-phase-one:')
  const windowsStart = prWorkflow.indexOf('\n  windows-package:')
  const finalStart = prWorkflow.indexOf('\n  required-pr-gate:')
  assert.ok(brokerStart > 0 && linuxStart > brokerStart && windowsStart > linuxStart && finalStart > windowsStart)
  const policy = prWorkflow.slice(0, brokerStart)
  const broker = prWorkflow.slice(brokerStart, linuxStart)
  const tests = prWorkflow.slice(linuxStart, finalStart)
  assert.doesNotMatch(policy, /RELEASE_APP_PRIVATE_KEY|app-token\.outputs\.token/)
  assert.match(broker, /environment:\s*\n\s+name: release-pr-trust/)
  assert.equal((broker.match(/RELEASE_APP_PRIVATE_KEY/g) ?? []).length, 1)
  assert.match(broker, /Encrypt private source before artifact handoff/)
  assert.doesNotMatch(tests, /RELEASE_APP_PRIVATE_KEY|app-token\.outputs\.token|actions\/checkout@/)
  assert.equal((tests.match(/permissions: \{\}/g) ?? []).length, 2)
  assert.equal((prWorkflow.match(/candidate-root\/release-manifest\.json/g) ?? []).length, 1)
})
