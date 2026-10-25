import { defineConfig } from 'vitest/config'

// One time zone for every test run, whatever the machine's (CI runners are in UTC): the date tests
// are written for America/Chicago, with its DST changes. The zone is per process, so it is set here,
// before the worker threads start; a test setting process.env.TZ in a worker has no effect.
process.env.TZ = 'America/Chicago'

export default defineConfig({
  // Worker threads start faster than forked processes; the pure helpers under test need no isolation.
  test: { include: ['src/**/*.test.ts'], environment: 'node', pool: 'threads' },
})
