// Right to left on the desktop: the main screens of all five
// themes in ar-XB, the right-to-left pseudo-language. The page mirrors (start is on the right), the
// words read right to left, and what people wrote keeps its own direction. No screen may scroll
// sideways; every screenshot is attached (SWEEP_SHOTS=<dir> also writes them to one folder).
// The phone: e2e/phone/rtl-phone.spec.ts.
import { test } from './fixtures'
import { rtlSweep, seedRtlData } from './i18n/rtlSweep'
import { THEMES } from './i18n/sweep'

for (const theme of THEMES) {
  test(`right-to-left sweep, ${theme}, desktop`, async ({ page, seeded }, info) => {
    test.setTimeout(240_000)
    const data = await seedRtlData(page, seeded)
    await rtlSweep(page, info, theme, false, data)
  })
}
