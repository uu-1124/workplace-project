import { createCipheriv, createDecipheriv, createHash, randomBytes } from 'node:crypto'
import { appendFileSync, createReadStream, createWriteStream, openSync, readSync, closeSync, statSync, unlinkSync } from 'node:fs'
import { pipeline } from 'node:stream/promises'
import { resolve } from 'node:path'

const MAGIC = Buffer.from('SRBNDL01', 'ascii')
const IV_BYTES = 12
const TAG_BYTES = 16
const HEADER_BYTES = MAGIC.length + IV_BYTES
const KEY_PATTERN = /^[0-9a-f]{64}$/

function fail(message) {
  throw new Error(`Encrypted release bundle failed: ${message}`)
}

function parseArgs(argv) {
  const [command, ...rest] = argv
  if (!['encrypt', 'decrypt'].includes(command)) fail('first argument must be encrypt or decrypt')
  const parsed = { command }
  for (let index = 0; index < rest.length; index += 2) {
    const key = rest[index]
    const value = rest[index + 1]
    if (!key?.startsWith('--') || value === undefined) fail(`invalid argument list near ${key ?? '<end>'}`)
    parsed[key.slice(2)] = value
  }
  return parsed
}

async function sha256(path) {
  const hash = createHash('sha256')
  for await (const chunk of createReadStream(path)) hash.update(chunk)
  return hash.digest('hex')
}

function readHeaderAndTag(path) {
  const size = statSync(path).size
  if (size <= HEADER_BYTES + TAG_BYTES) fail('bundle is truncated')
  const handle = openSync(path, 'r')
  try {
    const header = Buffer.alloc(HEADER_BYTES)
    const tag = Buffer.alloc(TAG_BYTES)
    if (readSync(handle, header, 0, header.length, 0) !== header.length) fail('cannot read bundle header')
    if (readSync(handle, tag, 0, tag.length, size - TAG_BYTES) !== tag.length) fail('cannot read bundle authentication tag')
    if (!header.subarray(0, MAGIC.length).equals(MAGIC)) fail('bundle magic or format version is invalid')
    return { size, iv: header.subarray(MAGIC.length), tag }
  } finally {
    closeSync(handle)
  }
}

function keyFromEnvironment({ allowGenerate }) {
  const configured = process.env.RELEASE_BUNDLE_KEY?.trim()
  if (configured) {
    if (!KEY_PATTERN.test(configured)) fail('RELEASE_BUNDLE_KEY must be 64 lowercase hexadecimal characters')
    return configured
  }
  if (!allowGenerate) fail('RELEASE_BUNDLE_KEY is required for decryption')
  return randomBytes(32).toString('hex')
}

async function encrypt(inputPath, outputPath, keyHex) {
  const iv = randomBytes(IV_BYTES)
  const cipher = createCipheriv('aes-256-gcm', Buffer.from(keyHex, 'hex'), iv)
  cipher.setAAD(MAGIC)
  const output = createWriteStream(outputPath, { flags: 'wx', mode: 0o600 })
  output.write(Buffer.concat([MAGIC, iv]))
  await pipeline(createReadStream(inputPath), cipher, output, { end: false })
  await new Promise((resolvePromise, rejectPromise) => {
    output.once('error', rejectPromise)
    output.end(cipher.getAuthTag(), resolvePromise)
  })
}

async function decrypt(inputPath, outputPath, keyHex) {
  const { size, iv, tag } = readHeaderAndTag(inputPath)
  const decipher = createDecipheriv('aes-256-gcm', Buffer.from(keyHex, 'hex'), iv)
  decipher.setAAD(MAGIC)
  decipher.setAuthTag(tag)
  try {
    await pipeline(
      createReadStream(inputPath, { start: HEADER_BYTES, end: size - TAG_BYTES - 1 }),
      decipher,
      createWriteStream(outputPath, { flags: 'wx', mode: 0o600 }),
    )
  } catch (error) {
    try { unlinkSync(outputPath) } catch {}
    fail(`bundle authentication or decryption failed: ${error.message}`)
  }
}

const args = parseArgs(process.argv.slice(2))
if (!args.input || !args.output) fail('--input and --output are required')
const inputPath = resolve(args.input)
const outputPath = resolve(args.output)

if (args.command === 'encrypt') {
  const keyHex = keyFromEnvironment({ allowGenerate: true })
  await encrypt(inputPath, outputPath, keyHex)
  const digest = await sha256(outputPath)
  if (args['github-output']) {
    appendFileSync(resolve(args['github-output']), `bundle_key=${keyHex}\nbundle_sha256=${digest}\n`, 'utf8')
  }
  console.log(JSON.stringify({ ok: true, operation: 'encrypt', sha256: digest }))
} else {
  const keyHex = keyFromEnvironment({ allowGenerate: false })
  if (args['expected-sha256']) {
    if (!/^[0-9a-f]{64}$/.test(args['expected-sha256'])) fail('--expected-sha256 is invalid')
    const actual = await sha256(inputPath)
    if (actual !== args['expected-sha256']) fail(`encrypted bundle digest mismatch: expected ${args['expected-sha256']}, got ${actual}`)
  }
  await decrypt(inputPath, outputPath, keyHex)
  console.log(JSON.stringify({ ok: true, operation: 'decrypt' }))
}
