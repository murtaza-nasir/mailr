// The harmony modes (engine.ts places the partner colours). A module of its own, without the HCT
// library, so the code that runs on every page load stays small (boot.ts).

export type Harmony = 'single' | 'complementary' | 'analogous' | 'triadic' | 'split'
export const HARMONIES: Harmony[] = ['single', 'complementary', 'analogous', 'triadic', 'split']

export function isHarmony(v: unknown): v is Harmony {
  return typeof v === 'string' && (HARMONIES as string[]).includes(v)
}
