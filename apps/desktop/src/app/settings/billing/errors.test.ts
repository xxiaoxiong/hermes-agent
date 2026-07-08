import { describe, expect, it } from 'vitest'

import type { BillingErrorKind, BillingRefusal } from './api'
import { resolveRefusal } from './errors'

const expectedActions: Record<BillingErrorKind | 'transport' | 'timeout', 'portal' | 'retry' | 'step_up' | 'none'> = {
  cli_billing_disabled: 'portal',
  endpoint_unavailable: 'retry',
  idempotency_conflict: 'none',
  insufficient_scope: 'step_up',
  monthly_cap_exceeded: 'portal',
  no_payment_method: 'portal',
  rate_limited: 'retry',
  remote_spending_disabled: 'portal',
  remote_spending_revoked: 'portal',
  role_required: 'portal',
  session_revoked: 'portal',
  temporarily_unavailable: 'retry',
  timeout: 'retry',
  transport: 'retry'
}

describe('resolveRefusal', () => {
  it('maps every known refusal kind to copy and the expected action', () => {
    for (const [kind, actionType] of Object.entries(expectedActions)) {
      const resolved = resolveRefusal({
        kind: kind as BillingRefusal['kind'],
        message: 'Server message.',
        portalUrl: 'https://portal.nousresearch.com/billing',
        retryAfter: 90
      })

      expect(resolved.title, kind).not.toHaveLength(0)
      expect(resolved.message, kind).not.toHaveLength(0)
      expect(resolved.action.type, kind).toBe(actionType)
    }
  })

  it('includes monthly cap headroom when the server sends it', () => {
    const resolved = resolveRefusal({
      kind: 'monthly_cap_exceeded',
      message: 'Monthly spend cap reached.',
      payload: { remainingUsd: '4.50' }
    })

    expect(resolved.message).toContain('$4.50 headroom left')
  })

  it('falls back sanely for unknown refusal kinds', () => {
    const resolved = resolveRefusal({ kind: 'new_billing_code', message: 'Something changed upstream.' })

    expect(resolved).toEqual({
      action: { type: 'none' },
      message: 'Something changed upstream.',
      title: 'Billing request failed'
    })
  })
})
