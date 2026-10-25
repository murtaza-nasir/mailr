// Right to left on a phone: the main screens of all five themes
// in ar-XB, the right-to-left pseudo-language. No screen may scroll sideways; the screenshots are
// attached (SWEEP_SHOTS=<dir> also writes them to one folder).
import { test } from '../fixtures'
import { rtlSweep, seedRtlData } from '../i18n/rtlSweep'
import { THEMES } from '../i18n/sweep'

for (const theme of THEMES) {
  test(`right-to-left sweep, ${theme}, phone`, async ({ page, seeded }, info) => {
    test.setTimeout(240_000)
    const data = await seedRtlData(page, seeded)
    await rtlSweep(page, info, theme, true, data)
  })
}
