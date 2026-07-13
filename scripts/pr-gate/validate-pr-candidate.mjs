import { appendFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'

const SHA_PATTERN = /^[0-9a-f]{40}$/
const EXPECTED_GITLINKS = ['workplace-api', 'workplace-desktop-electron']
const EXPECTED_GITMODULES = [
  '[submodule "workplace-api"]',
  '\tpath = workplace-api',
  '\turl = https://github.com/uu-1124/workplace-api.git',
  '[submodule "workplace-desktop-electron"]',
  '\tpath = workplace-desktop-electron',
  '\turl = https://github.com/uu-1124/workplace-desktop-electron.git',
  '',
].join('\n')

function fail(message) {
  throw new Error(`PR candidate validation failed: ${message}`)
}

function parseArgs(argv) {
  const parsed = {}
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index]
    const value = argv[index + 1]
    if (!key?.startsWith('--') || value === undefined) fail(`invalid argument list near ${key ?? '<end>'}`)
    parsed[key.slice(2)] = value
  }
  return parsed
}

const args = parseArgs(process.argv.slice(2))
const repositoryRoot = resolve(args.root ?? process.cwd())
const baseSha = args.base
const headSha = args.head
const outputPath = args.output ? resolve(args.output) : null
const allowPrivilegedChanges = args['allow-privileged'] === 'true'

for (const [label, sha] of [['base', baseSha], ['head', headSha]]) {
  if (!SHA_PATTERN.test(sha ?? '')) fail(`${label} SHA must be a lowercase 40-character commit SHA`)
}

function gitText(commandArgs) {
  return execFileSync('git', commandArgs, {
    cwd: repositoryRoot,
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
  }).trim()
}

function gitBuffer(commandArgs) {
  return execFileSync('git', commandArgs, {
    cwd: repositoryRoot,
    encoding: 'buffer',
    maxBuffer: 16 * 1024 * 1024,
  })
}

function assertCommit(sha, label) {
  try {
    execFileSync('git', ['cat-file', '-e', `${sha}^{commit}`], { cwd: repositoryRoot, stdio: 'ignore' })
  } catch {
    fail(`${label} commit ${sha} is not present in the policy checkout`)
  }
}

function assertAncestor(ancestor, descendant, message) {
  try {
    execFileSync('git', ['merge-base', '--is-ancestor', ancestor, descendant], { cwd: repositoryRoot, stdio: 'ignore' })
  } catch {
    fail(message)
  }
}

function treeEntries(ref) {
  const records = gitBuffer(['ls-tree', '-r', '-z', '--full-tree', ref]).toString('utf8').split('\0').filter(Boolean)
  return records.map(record => {
    const match = record.match(/^(\d{6}) ([a-z]+) ([0-9a-f]{40})\t([\s\S]+)$/)
    if (!match) fail(`cannot parse Git tree entry at ${ref}`)
    return { mode: match[1], type: match[2], sha: match[3], path: match[4] }
  })
}

function gitlinksAt(ref) {
  const links = treeEntries(ref).filter(entry => entry.mode === '160000' && entry.type === 'commit')
  const paths = links.map(entry => entry.path).sort()
  if (JSON.stringify(paths) !== JSON.stringify([...EXPECTED_GITLINKS].sort())) {
    fail(`${ref} must contain exactly the two approved API and Desktop gitlinks`)
  }
  return Object.fromEntries(links.map(entry => [entry.path, entry.sha]))
}

function changedPaths() {
  return gitBuffer(['diff', '--name-only', '-z', '--no-renames', baseSha, headSha])
    .toString('utf8')
    .split('\0')
    .filter(Boolean)
}

function isPrivilegedPath(path) {
  const normalized = path.toLowerCase()
  return normalized === '.gitmodules'
    || normalized === '.github'
    || normalized.startsWith('.github/')
    || normalized === 'scripts'
    || normalized.startsWith('scripts/')
}

function assertExactKeys(value, expectedKeys, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(`${label} must be an object`)
  const actual = Object.keys(value).sort()
  const expected = [...expectedKeys].sort()
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    fail(`${label} keys must be exactly: ${expected.join(', ')}`)
  }
}

function assertString(value, pattern, label) {
  if (typeof value !== 'string' || !pattern.test(value)) fail(`${label} has an invalid value`)
}

function readAt(ref, path) {
  try {
    return execFileSync('git', ['show', `${ref}:${path}`], {
      cwd: repositoryRoot,
      encoding: 'utf8',
      maxBuffer: 1024 * 1024,
    })
  } catch {
    fail(`${path} is missing or unreadable at ${ref}`)
  }
}

function validateManifest(raw, links) {
  let manifest
  try {
    manifest = JSON.parse(raw)
  } catch {
    fail('release-manifest.json is not valid JSON')
  }

  assertExactKeys(manifest, ['release_id', 'released_at', 'components', 'release_gate'], 'manifest')
  assertString(manifest.release_id, /^\d{4}\.\d{2}\.\d{2}-r\d+\.\d+$/, 'manifest.release_id')
  assertString(manifest.released_at, /^\d{4}-\d{2}-\d{2}$/, 'manifest.released_at')
  if (new Date(`${manifest.released_at}T00:00:00.000Z`).toISOString().slice(0, 10) !== manifest.released_at) {
    fail('manifest.released_at is not a real calendar date')
  }

  assertExactKeys(manifest.components, ['root', 'api', 'desktop'], 'manifest.components')
  assertExactKeys(manifest.components.root, ['source_commit'], 'manifest.components.root')
  assertExactKeys(manifest.components.api, ['commit', 'runtime_version', 'migration_head'], 'manifest.components.api')
  assertExactKeys(manifest.components.desktop, ['commit', 'package_version'], 'manifest.components.desktop')
  assertExactKeys(manifest.release_gate, ['default_scope', 'command', 'full_launch_compatibility'], 'manifest.release_gate')

  assertString(manifest.components.root.source_commit, SHA_PATTERN, 'manifest.components.root.source_commit')
  assertString(manifest.components.api.commit, SHA_PATTERN, 'manifest.components.api.commit')
  assertString(manifest.components.api.runtime_version, /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/, 'manifest.components.api.runtime_version')
  assertString(manifest.components.api.migration_head, /^\d{8}_\d{4}$/, 'manifest.components.api.migration_head')
  assertString(manifest.components.desktop.commit, SHA_PATTERN, 'manifest.components.desktop.commit')
  assertString(manifest.components.desktop.package_version, /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/, 'manifest.components.desktop.package_version')
  for (const key of ['default_scope', 'command', 'full_launch_compatibility']) {
    if (typeof manifest.release_gate[key] !== 'string' || manifest.release_gate[key].trim() === '') {
      fail(`manifest.release_gate.${key} must be a non-empty string`)
    }
  }

  assertCommit(manifest.components.root.source_commit, 'manifest root source')
  assertAncestor(
    manifest.components.root.source_commit,
    headSha,
    'manifest root source commit is not an ancestor of the PR head',
  )
  if (manifest.components.api.commit !== links['workplace-api']) fail('API gitlink differs from release manifest')
  if (manifest.components.desktop.commit !== links['workplace-desktop-electron']) fail('Desktop gitlink differs from release manifest')
  return manifest
}

assertCommit(baseSha, 'base')
assertCommit(headSha, 'head')
assertAncestor(baseSha, headSha, 'PR head must contain the current base commit; update the branch before release validation')

const headEntries = treeEntries(headSha)
const caseInsensitivePaths = new Map()
for (const entry of headEntries) {
  if (/\r|\n/.test(entry.path)) fail('repository paths containing line breaks are not allowed')
  const normalized = entry.path.toLowerCase()
  const previous = caseInsensitivePaths.get(normalized)
  if (previous && previous !== entry.path) {
    fail(`case-colliding paths are not portable to Windows: ${previous} and ${entry.path}`)
  }
  caseInsensitivePaths.set(normalized, entry.path)
}

const baseLinks = gitlinksAt(baseSha)
const headLinks = gitlinksAt(headSha)
const paths = changedPaths()
const privilegedPaths = paths.filter(isPrivilegedPath)
if (privilegedPaths.length > 0 && !allowPrivilegedChanges) {
  fail(`privileged CI paths require the trusted-ci-change label: ${privilegedPaths.join(', ')}`)
}

const gitmodules = readAt(headSha, '.gitmodules').replace(/\r\n/g, '\n')
if (gitmodules !== EXPECTED_GITMODULES) fail('.gitmodules must retain the two approved GitHub repositories and paths')

const manifest = validateManifest(readAt(headSha, 'release-manifest.json'), headLinks)
const releasePaths = new Set(['release-manifest.json', ...EXPECTED_GITLINKS])
const requiresFullGate = privilegedPaths.length > 0 || paths.some(path => releasePaths.has(path))

const outputs = {
  requires_full_gate: String(requiresFullGate),
  privileged_change: String(privilegedPaths.length > 0),
  root_head_sha: headSha,
  base_api_sha: baseLinks['workplace-api'],
  base_desktop_sha: baseLinks['workplace-desktop-electron'],
  api_sha: headLinks['workplace-api'],
  desktop_sha: headLinks['workplace-desktop-electron'],
  release_id: manifest.release_id,
}

if (outputPath) {
  appendFileSync(outputPath, Object.entries(outputs).map(([key, value]) => `${key}=${value}\n`).join(''), 'utf8')
}

console.log(JSON.stringify({
  ok: true,
  changed_path_count: paths.length,
  requires_full_gate: requiresFullGate,
  privileged_change: privilegedPaths.length > 0,
  root_head_sha: headSha,
  api_sha: outputs.api_sha,
  desktop_sha: outputs.desktop_sha,
  release_id: outputs.release_id,
}, null, 2))
