import { Button } from '@/components/ui/button'
import { BarChart3 } from '@/lib/icons'

import { ListRow, SectionHeading, SettingsContent } from '../primitives'

const EMPTY_VALUE = '—'
const FEATURE_BILLING_INVOICES = false

const SUMMARY_ITEMS = [
  { label: 'Balance', value: EMPTY_VALUE },
  { label: 'Plan', value: EMPTY_VALUE },
  { label: 'Auto-refill', value: EMPTY_VALUE }
] as const

const BILLING_ROWS = [
  {
    description: 'Manage the card used for top-ups and subscription renewals.',
    title: 'Payment method'
  },
  {
    description: 'Review your plan and change it from the billing portal.',
    title: 'Subscription'
  },
  {
    description: 'Add top-up credits for agent runs outside your plan.',
    title: 'Buy credits'
  },
  {
    description: 'Keep your balance topped up when it drops below your threshold.',
    title: 'Auto-refill'
  }
] as const

const USAGE_ROWS = [
  {
    description: 'Credits remaining from your current subscription cycle.',
    title: 'Subscription credits'
  },
  {
    description: 'Additional credits available after subscription credits run out.',
    title: 'Top-up credits'
  },
  {
    description: 'Maximum terminal billing spend allowed this month.',
    title: 'Monthly spend cap'
  }
] as const

function PlaceholderButton() {
  return (
    <Button disabled size="sm" type="button" variant="outline">
      {EMPTY_VALUE}
    </Button>
  )
}

function PlaceholderValue() {
  return (
    <span className="text-[length:var(--conversation-text-font-size)] text-(--ui-text-tertiary)">{EMPTY_VALUE}</span>
  )
}

export function BillingSettings() {
  return (
    <SettingsContent>
      <SectionHeading icon={BarChart3} title="Billing" />

      <div className="@container mb-5">
        <div className="grid gap-3 rounded-lg border border-border/70 bg-muted/20 p-4 @2xl:grid-cols-3">
          {SUMMARY_ITEMS.map(item => (
            <div className="min-w-0" key={item.label}>
              <div className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
                {item.label}
              </div>
              <div className="mt-1 text-lg font-semibold text-foreground">{item.value}</div>
            </div>
          ))}
        </div>
      </div>

      <SectionHeading icon={BarChart3} title="Account" />
      {BILLING_ROWS.map(row => (
        <ListRow action={<PlaceholderButton />} description={row.description} key={row.title} title={row.title} />
      ))}

      <SectionHeading icon={BarChart3} title="Usage" />
      {USAGE_ROWS.map(row => (
        <ListRow action={<PlaceholderValue />} description={row.description} key={row.title} title={row.title} />
      ))}

      {
        // no endpoint yet — NAS capability-board gap
        FEATURE_BILLING_INVOICES ? <SectionHeading icon={BarChart3} title="Invoices" /> : null
      }
    </SettingsContent>
  )
}
