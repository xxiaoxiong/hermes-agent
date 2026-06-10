/**
 * Pure tests for the Esc+Esc session prompt viewer logic (Epic 5;
 * logic/promptHistory.ts): the double-press window (free-code's 800ms model),
 * entry sourcing (user prompts only, newest first, session-only, empty → none),
 * Undo eligibility (most-recent entry ONLY), and the 7-visible centered
 * windowing math.
 */
import { describe, expect, test } from 'vitest'

import {
  actionCommand,
  confirmOptions,
  createDoublePress,
  DOUBLE_PRESS_WINDOW_MS,
  MAX_VISIBLE,
  promptHistoryEntries,
  ROLLBACK_LABEL,
  UNDO_LABEL,
  windowStart
} from '../logic/promptHistory.ts'

describe('createDoublePress — the 800ms window', () => {
  test('two presses 799ms apart fire', () => {
    const dp = createDoublePress()
    expect(dp.press(1000)).toBe(false)
    expect(dp.press(1799)).toBe(true)
  })

  test('two presses exactly at the window boundary fire (<=, free-code parity)', () => {
    const dp = createDoublePress()
    expect(dp.press(0)).toBe(false)
    expect(dp.press(DOUBLE_PRESS_WINDOW_MS)).toBe(true)
  })

  test('two presses 801ms apart do NOT fire — the late press re-arms instead', () => {
    const dp = createDoublePress()
    expect(dp.press(1000)).toBe(false)
    expect(dp.press(1801)).toBe(false)
    // …and the re-armed press pairs with a quick follow-up
    expect(dp.press(2000)).toBe(true)
  })

  test('an intervening key (reset) disarms the pending press', () => {
    const dp = createDoublePress()
    expect(dp.press(1000)).toBe(false)
    dp.reset()
    expect(dp.press(1100)).toBe(false) // would have fired without the reset
    expect(dp.press(1200)).toBe(true)
  })

  test('firing disarms — a third quick press starts a fresh cycle', () => {
    const dp = createDoublePress()
    dp.press(1000)
    expect(dp.press(1100)).toBe(true)
    expect(dp.press(1200)).toBe(false)
  })
})

describe('promptHistoryEntries — sourcing from the session transcript', () => {
  test('user prompts only, newest first, with stable transcript indices', () => {
    const messages = [
      { role: 'user', text: 'first prompt' },
      { role: 'assistant', text: 'reply one' },
      { role: 'system', text: 'a notice' },
      { role: 'user', text: 'second prompt' },
      { role: 'assistant', text: 'reply two' },
      { role: 'user', text: 'third prompt' }
    ]
    expect(promptHistoryEntries(messages)).toEqual([
      { index: 5, text: 'third prompt' },
      { index: 3, text: 'second prompt' },
      { index: 0, text: 'first prompt' }
    ])
  })

  test('empty session → no entries (the trigger shows nothing)', () => {
    expect(promptHistoryEntries([])).toEqual([])
  })

  test('assistant/system-only transcript → no entries', () => {
    expect(
      promptHistoryEntries([
        { role: 'assistant', text: 'hello' },
        { role: 'system', text: 'gateway ready' }
      ])
    ).toEqual([])
  })

  test('blank user rows are skipped', () => {
    expect(
      promptHistoryEntries([
        { role: 'user', text: '   ' },
        { role: 'user', text: 'real' }
      ])
    ).toEqual([{ index: 1, text: 'real' }])
  })
})

describe('confirmOptions — Undo only for the most recent prompt', () => {
  test('latest entry offers Undo then Rollback, with the signed-off labels', () => {
    expect(confirmOptions(true)).toEqual([
      { action: 'undo', label: UNDO_LABEL },
      { action: 'rollback', label: ROLLBACK_LABEL }
    ])
    expect(UNDO_LABEL).toBe('Undo — rewind the conversation (files kept)')
    expect(ROLLBACK_LABEL).toBe('Rollback — restore files from checkpoint (conversation kept)')
  })

  test('an older entry hides Undo (the gateway only rewinds the LAST exchange)', () => {
    expect(confirmOptions(false)).toEqual([{ action: 'rollback', label: ROLLBACK_LABEL }])
  })

  test('actions map to the existing slash commands', () => {
    expect(actionCommand('undo')).toBe('/undo')
    expect(actionCommand('rollback')).toBe('/rollback')
  })
})

describe('windowStart — 7 visible, selection centered', () => {
  test('everything fits → window starts at 0', () => {
    expect(windowStart(0, 5)).toBe(0)
    expect(windowStart(4, 5)).toBe(0)
    expect(windowStart(6, MAX_VISIBLE)).toBe(0)
  })

  test('selection centers once past the half-window', () => {
    // visible=7 → half=3; selection 5 of 20 → window starts at 2 (5 centered)
    expect(windowStart(5, 20)).toBe(2)
    expect(windowStart(10, 20)).toBe(7)
  })

  test('clamps at the top and the bottom', () => {
    expect(windowStart(0, 20)).toBe(0)
    expect(windowStart(2, 20)).toBe(0) // still within the first half-window
    expect(windowStart(19, 20)).toBe(13) // last 7 rows
    expect(windowStart(17, 20)).toBe(13)
  })

  test('honors a custom visible count', () => {
    expect(windowStart(4, 10, 3)).toBe(3)
    expect(windowStart(9, 10, 3)).toBe(7)
  })
})
