// Brotli and gzip copies of a web build's compressible files, made once at image build time
// (Dockerfile), so the server sends them without compressing per request (backend
// core/webstatic.py). A copy is written only when it is smaller than the file.
//   node scripts/precompress.mjs [dist]        (prints the totals)
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { brotliCompressSync, constants, gzipSync } from 'node:zlib'

const root = path.resolve(process.argv[2] ?? path.join(path.dirname(fileURLToPath(import.meta.url)), '../dist'))
const TEXT = /\.(js|mjs|css|html|json|webmanifest|svg|txt|xml|ico|map)$/
const MIN = 1024

const files = []
const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    const f = path.join(dir, name)
    if (statSync(f).isDirectory()) walk(f)
    else if (TEXT.test(name) && statSync(f).size >= MIN) files.push(f)
  }
}
walk(root)

let raw = 0
let br = 0
let gz = 0
for (const f of files) {
  const src = readFileSync(f)
  const b = brotliCompressSync(src, {
    params: { [constants.BROTLI_PARAM_QUALITY]: 11, [constants.BROTLI_PARAM_SIZE_HINT]: src.length, [constants.BROTLI_PARAM_MODE]: constants.BROTLI_MODE_TEXT },
  })
  const g = gzipSync(src, { level: 9 })
  raw += src.length
  if (b.length < src.length) {
    writeFileSync(`${f}.br`, b)
    br += b.length
  } else br += src.length
  if (g.length < src.length) {
    writeFileSync(`${f}.gz`, g)
    gz += g.length
  } else gz += src.length
}
const kb = (n) => `${(n / 1024).toFixed(0)} kB`
console.log(`precompressed ${files.length} files: ${kb(raw)} -> brotli ${kb(br)}, gzip ${kb(gz)}`)
