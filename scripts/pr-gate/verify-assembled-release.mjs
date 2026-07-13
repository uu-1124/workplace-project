import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'

const SHA_PATTERN = /^[0-9a-f]{40}$/

function fail(message) {
  throw new Error(`Assembled PR release verification failed: ${message}`)
}

function parseArgs(argv) {
  const parsed = {}
  for (let index = 0; index < argv.length; index += 2) {
    if (!argv[index]?.startsWith('--') || argv[index + 1] === undefined) fail('invalid command arguments')
    parsed[argv[index].slice(2)] = argv[index + 1]
  }
  return parsed
}

const args = parseArgs(process.argv.slice(2))
const root = resolve(args.root ?? process.cwd())
const manifest = JSON.parse(readFileSync(resolve(root, 'release-manifest.json'), 'utf8'))
const metadata = JSON.parse(readFileSync(resolve(root, 'release-source-metadata.json'), 'utf8'))
const apiPath = resolve(root, 'workplace-api')
const desktopPath = resolve(root, 'workplace-desktop-electron')

if (metadata.schema_version !== 1) fail('unsupported metadata schema')
for (const key of ['root_base_sha', 'root_head_sha', 'api_commit', 'desktop_commit', 'previous_desktop_commit']) {
  if (typeof metadata[key] !== 'string' || !SHA_PATTERN.test(metadata[key])) fail(`metadata.${key} is invalid`)
}
if (manifest.components.api.commit !== metadata.api_commit) fail('API source differs from candidate manifest')
if (manifest.components.desktop.commit !== metadata.desktop_commit) fail('Desktop source differs from candidate manifest')

const desktopPackage = JSON.parse(readFileSync(resolve(desktopPath, 'package.json'), 'utf8'))
if (desktopPackage.version !== manifest.components.desktop.package_version) fail('Desktop package version differs from manifest')

const apiConfig = readFileSync(resolve(apiPath, 'app', 'config.py'), 'utf8')
if (!apiConfig.includes(`app_version: str = "${manifest.components.api.runtime_version}"`)) {
  fail('API runtime version differs from manifest')
}

const migrationDirectory = resolve(apiPath, 'migrations', 'versions')
const migrationName = readdirSync(migrationDirectory)
  .find(name => name.startsWith(`${manifest.components.api.migration_head}_`) && name.endsWith('.py'))
if (!migrationName) fail(`API migration ${manifest.components.api.migration_head} is not present`)
const migration = readFileSync(resolve(migrationDirectory, migrationName), 'utf8')
if (!migration.includes(`revision = "${manifest.components.api.migration_head}"`)) {
  fail('API migration revision differs from manifest')
}

console.log(JSON.stringify({
  ok: true,
  release_id: manifest.release_id,
  root_head_sha: metadata.root_head_sha,
  api_commit: metadata.api_commit,
  desktop_commit: metadata.desktop_commit,
  previous_desktop_commit: metadata.previous_desktop_commit,
}, null, 2))
