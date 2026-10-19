import { expect, it } from 'vitest'

// Wave-12 deploy-gate demo (SPEC-87): this commit is reverted by the next one.
it('w12 gate demo: verify goes red', () => expect('red').toBe('green'))
