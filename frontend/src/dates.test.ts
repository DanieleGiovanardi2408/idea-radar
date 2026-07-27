/* Le date.
 *
 * Erano tre funzioni in tre file, due identiche. Ora sono una sola cosa, e le
 * asserzioni sul formato sono volutamente larghe (il mese, l'ora) invece di
 * confrontare la stringa intera: la punteggiatura di `toLocaleString` cambia tra
 * versioni di ICU, e un test che si rompe all'aggiornamento di Node non sta
 * proteggendo niente.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { dateAndTime, dayMonthYear, timeAgo } from './dates'

afterEach(() => {
  vi.useRealTimers()
})

describe('dateAndTime', () => {
  it('dice giorno, mese e ora', () => {
    const out = dateAndTime('2026-07-27T14:39:00')
    expect(out).toMatch(/27/)
    expect(out).toMatch(/lug/)
    expect(out).toMatch(/14[:.]39/)
  })

  it('su valore assente o non valido mette il trattino, non "Invalid Date"', () => {
    expect(dateAndTime(null)).toBe('—')
    expect(dateAndTime(undefined)).toBe('—')
    expect(dateAndTime('')).toBe('—')
    expect(dateAndTime('non una data')).toBe('—')
  })
})

describe('dayMonthYear', () => {
  it('dice giorno, mese e anno, senza ora', () => {
    const out = dayMonthYear('2026-07-27T14:39:00')
    expect(out).toMatch(/27/)
    expect(out).toMatch(/lug/)
    expect(out).toMatch(/2026/)
    expect(out).not.toMatch(/14[:.]39/)
  })

  it('regge l’assenza come le altre', () => {
    expect(dayMonthYear(null)).toBe('—')
    expect(dayMonthYear('non una data')).toBe('—')
  })
})

describe('timeAgo', () => {
  it('conta le ore, poi i giorni', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-27T12:00:00Z'))

    expect(timeAgo('2026-07-27T11:50:00Z')).toBe('ora') // meno di un'ora
    expect(timeAgo('2026-07-27T06:00:00Z')).toBe('6h')
    expect(timeAgo('2026-07-25T12:00:00Z')).toBe('2g')
  })

  it('su valore assente resta muto, non stampa un trattino in mezzo al testo', () => {
    // Qui il risultato finisce dentro "· 6h": un '—' sarebbe rumore.
    expect(timeAgo(null)).toBe('')
    expect(timeAgo('non una data')).toBe('')
  })
})
