/// <reference types="node" />
// react-dom/server (used only lazily by the assistant's copy-as-email) stays out of the
// vendor chunk that every page loads first.
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const config = readFileSync(new URL('../../vite.config.ts', import.meta.url), 'utf8')
const literal = /name: 'vendor', test: \/(.+)\/(?:, priority: \d+)? \}/.exec(config)![1]
const vendor = new RegExp(literal)

describe('vendor chunk', () => {
  it('holds React and the router', () => {
    expect(vendor.test('/x/node_modules/react-dom/cjs/react-dom-client.production.js')).toBe(true)
    expect(vendor.test('/x/node_modules/react/index.js')).toBe(true)
    expect(vendor.test('/x/node_modules/react-router/dist/index.mjs')).toBe(true)
  })
  it('leaves react-dom/server out', () => {
    expect(vendor.test('/x/node_modules/react-dom/server.browser.js')).toBe(false)
    expect(vendor.test('/x/node_modules/react-dom/cjs/react-dom-server.browser.production.js')).toBe(false)
    expect(vendor.test('/x/node_modules/react-dom/cjs/react-dom-server-legacy.browser.production.js')).toBe(false)
  })
})
