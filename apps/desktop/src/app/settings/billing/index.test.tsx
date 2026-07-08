import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { BillingSettings } from './index'

afterEach(() => {
  cleanup()
})

describe('BillingSettings', () => {
  it('renders the static billing skeleton without invoice content', () => {
    render(<BillingSettings />)

    expect(screen.getByText('Balance')).toBeTruthy()
    expect(screen.getByText('Plan')).toBeTruthy()
    expect(screen.getAllByText('Auto-refill').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(3)

    expect(screen.getByText('Payment method')).toBeTruthy()
    expect(screen.getByText('Manage the card used for top-ups and subscription renewals.')).toBeTruthy()
    expect(screen.getByText('Subscription')).toBeTruthy()
    expect(screen.getByText('Review your plan and change it from the billing portal.')).toBeTruthy()
    expect(screen.getByText('Buy credits')).toBeTruthy()
    expect(screen.getByText('Add top-up credits for agent runs outside your plan.')).toBeTruthy()

    expect(screen.getByText('Usage')).toBeTruthy()
    expect(screen.getByText('Subscription credits')).toBeTruthy()
    expect(screen.getByText('Top-up credits')).toBeTruthy()
    expect(screen.getByText('Monthly spend cap')).toBeTruthy()
    expect(screen.queryByText('Invoices')).toBeNull()

    const disabledControls = screen.getAllByRole('button').filter(button => button.hasAttribute('disabled'))
    expect(disabledControls.length).toBe(4)
  })
})
