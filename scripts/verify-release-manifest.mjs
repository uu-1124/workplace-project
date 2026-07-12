import { execFileSync } from 'node:child_process'
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const manifest = JSON.parse(readFileSync(resolve(root, 'release-manifest.json'), 'utf8'))
const apiPath = resolve(root, 'workplace-api')
const desktopPath = resolve(root, 'workplace-desktop-electron')

function fail(message) { throw new Error(`Release manifest verification failed: ${message}`) }
function git(args, cwd = root) { return execFileSync('git', args, { cwd, encoding: 'utf8' }).trim() }
function gitlink(path) {
  const match = git(['ls-tree', 'HEAD', path]).match(/^160000 commit ([0-9a-f]{40})\t/)
  if (!match) fail(`${path} is not recorded as a gitlink`)
  return match[1]
}

const desktopPackage = JSON.parse(readFileSync(resolve(desktopPath, 'package.json'), 'utf8'))
const apiConfig = readFileSync(resolve(apiPath, 'app', 'config.py'), 'utf8')
const migrationDirectory = resolve(apiPath, 'migrations', 'versions')
const migrationName = readdirSync(migrationDirectory).find(name => name.startsWith(`${manifest.components.api.migration_head}_`) && name.endsWith('.py'))
if (!migrationName) fail(`API migration ${manifest.components.api.migration_head} is not present`)
const migration = readFileSync(resolve(migrationDirectory, migrationName), 'utf8')

const currentRootCommit = git(['rev-parse', 'HEAD'])
try {
  execFileSync('git', ['merge-base', '--is-ancestor', manifest.components.root.source_commit, currentRootCommit], { cwd: root, stdio: 'ignore' })
} catch {
  fail('root source commit is not an ancestor of the checked-out release')
}
const manifestCommit = git(['log', '-1', '--format=%H', '--', 'release-manifest.json'])
if (!manifestCommit) fail('release manifest is not committed')
if (gitlink('workplace-api') !== manifest.components.api.commit) fail('API gitlink differs from manifest')
if (gitlink('workplace-desktop-electron') !== manifest.components.desktop.commit) fail('desktop gitlink differs from manifest')
if (git(['rev-parse', 'HEAD'], apiPath) !== manifest.components.api.commit) fail('checked-out API commit differs from manifest')
if (git(['rev-parse', 'HEAD'], desktopPath) !== manifest.components.desktop.commit) fail('checked-out desktop commit differs from manifest')
if (desktopPackage.version !== manifest.components.desktop.package_version) fail('desktop package version differs from manifest')
if (!apiConfig.includes(`app_version: str = "${manifest.components.api.runtime_version}"`)) fail('API runtime version differs from manifest')
if (!migration.includes(`revision = "${manifest.components.api.migration_head}"`)) fail('API migration head differs from manifest')

console.log(JSON.stringify({ ok: true, release_id: manifest.release_id, source_root_commit: manifest.components.root.source_commit, manifest_commit: manifestCommit, api: manifest.components.api, desktop: manifest.components.desktop }, null, 2))
