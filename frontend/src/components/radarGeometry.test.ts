/* La geometria del quadrante.
 *
 * Il radar fa tre affermazioni che a schermo non si possono verificare: la
 * distanza dal centro *è* `1 − composite`, l'angolo è uno spicchio per PROFILO
 * (il macro-tema dichiarato), e la mappa non balla tra due render sugli stessi
 * dati. Finché stavano dentro il componente nessuno le aveva messe alla prova.
 */

import { describe, expect, it } from 'vitest'
import {
  blipsFor,
  CENTER,
  isFresh,
  MAX_BLIPS,
  pointAt,
  R_MAX,
  R_MIN,
  radiusOf,
  sectorsFor,
} from './radarGeometry'
import { fakeIdeaOut } from '../test/utils'

const idea = (id: number, composite: number, profile: string | null = 'ai-agents') =>
  fakeIdeaOut({ id, composite, profile, label: `Idea ${id}` })

describe('geometria dei blip', () => {
  it('il punteggio migliore sta più vicino al centro', () => {
    const [migliore, peggiore] = blipsFor([idea(1, 0.9), idea(2, 0.1)])
    expect(radiusOf(migliore)).toBeLessThan(radiusOf(peggiore))
  })

  it('la distanza è 1 − composite, riscalata tra R_MIN e R_MAX', () => {
    const [pieno] = blipsFor([idea(1, 1)])
    const [vuoto] = blipsFor([idea(1, 0)])
    expect(radiusOf(pieno)).toBeCloseTo(R_MIN, 5)
    expect(radiusOf(vuoto)).toBeCloseTo(R_MAX, 5)

    const [mezzo] = blipsFor([idea(1, 0.5)])
    expect(radiusOf(mezzo)).toBeCloseTo(R_MIN + (R_MAX - R_MIN) / 2, 5)
  })

  it('un composite fuori scala non esce dal quadrante', () => {
    for (const composite of [-5, 1.4, 99]) {
      const [blip] = blipsFor([idea(1, composite)])
      const r = radiusOf(blip)
      expect(r).toBeGreaterThanOrEqual(R_MIN - 0.001)
      expect(r).toBeLessThanOrEqual(R_MAX + 0.001)
    }
  })

  it('idee dello stesso profilo finiscono nello stesso spicchio', () => {
    const blips = blipsFor([
      idea(1, 0.5, 'agenti'),
      idea(2, 0.5, 'agenti'),
      idea(3, 0.5, 'iot'),
      idea(4, 0.5, 'iot'),
    ])
    // Due profili in ordine alfabetico: agenti 0-180°, iot 180-360°.
    for (const b of blips) {
      if (b.idea.profile === 'agenti') expect(b.angle).toBeLessThan(180)
      else expect(b.angle).toBeGreaterThanOrEqual(180)
    }
  })

  it('l\'ordine dei profili configurati comanda sugli spicchi', () => {
    const ideas = [idea(1, 0.5, 'zeta'), idea(2, 0.5, 'alfa')]
    // Senza ordine: alfabetico (alfa prima). Con ordine: zeta prima.
    const [conOrdine] = blipsFor(ideas, ['zeta', 'alfa'])
    expect(conOrdine.idea.id).toBe(1)
    expect(conOrdine.angle).toBeLessThan(180)
  })

  it('la stessa lista dà le stesse posizioni: la mappa non balla', () => {
    const ideas = [idea(1, 0.7, 'a'), idea(2, 0.4, 'b'), idea(3, 0.55, 'a')]
    expect(blipsFor(ideas)).toEqual(blipsFor(ideas))
    expect(blipsFor([...ideas].reverse()).map((b) => [b.x, b.y])).toEqual(
      blipsFor(ideas).map((b) => [b.x, b.y]),
    )
  })

  it('oltre MAX_BLIPS tiene i migliori, non i primi arrivati', () => {
    const molte = Array.from({ length: MAX_BLIPS + 20 }, (_, i) =>
      idea(i + 1, i / (MAX_BLIPS + 20)),
    )
    const blips = blipsFor(molte)
    expect(blips).toHaveLength(MAX_BLIPS)
    const dentro = blips.map((b) => b.idea.id)
    expect(dentro).toContain(MAX_BLIPS + 20)
    expect(dentro).not.toContain(1)
  })

  it('senza idee non c’è niente da disegnare', () => {
    expect(blipsFor([])).toEqual([])
    expect(sectorsFor([])).toEqual([])
  })

  it('un profilo assente non spacca la geometria', () => {
    const [blip] = blipsFor([idea(1, 0.5, null)])
    expect(Number.isFinite(blip.x)).toBe(true)
    expect(Number.isFinite(blip.y)).toBe(true)
    expect(radiusOf(blip)).toBeGreaterThan(0)
  })

  it('il centro del quadrante è il riferimento', () => {
    const [blip] = blipsFor([idea(1, 1)])
    expect(blip.x).toBeCloseTo(
      CENTER + R_MIN * Math.cos(((blip.angle - 90) * Math.PI) / 180),
      5,
    )
  })
})

describe('gli spicchi', () => {
  it('uno per profilo presente, larghezza uguale, senza-tema in coda', () => {
    const sectors = sectorsFor(
      [idea(1, 0.5, 'iot'), idea(2, 0.5, 'agenti'), idea(3, 0.5, null)],
      ['agenti', 'iot'],
    )
    expect(sectors.map((s) => s.profile)).toEqual(['agenti', 'iot', null])
    for (const s of sectors) expect(s.end - s.start).toBeCloseTo(120, 5)
  })

  it('i profili configurati ma assenti dalle idee non aprono spicchi vuoti', () => {
    const sectors = sectorsFor([idea(1, 0.5, 'agenti')], ['agenti', 'iot', 'llm'])
    expect(sectors.map((s) => s.profile)).toEqual(['agenti'])
  })

  it('pointAt: 0° è ore 12, 90° è ore 3', () => {
    const su = pointAt(0, 100)
    expect(su.x).toBeCloseTo(CENTER, 5)
    expect(su.y).toBeCloseTo(CENTER - 100, 5)
    const destra = pointAt(90, 100)
    expect(destra.x).toBeCloseTo(CENTER + 100, 5)
    expect(destra.y).toBeCloseTo(CENTER, 5)
  })
})

describe('contatti nuovi', () => {
  it("è nuova l'idea vista per la prima volta dopo l'inizio dell'ultimo run", () => {
    const since = '2026-08-14T09:00:00'
    expect(
      isFresh(fakeIdeaOut({ first_seen: '2026-08-14T09:12:00' }), since),
    ).toBe(true)
    expect(
      isFresh(fakeIdeaOut({ first_seen: '2026-08-13T22:00:00' }), since),
    ).toBe(false)
  })

  it('senza run in archivio nessuno è nuovo', () => {
    /* "Nuovo" detto a tutti è lo stesso che non dirlo, e in più fa pulsare
       sessanta blip insieme. */
    expect(isFresh(fakeIdeaOut({ first_seen: '2026-08-14T09:12:00' }), null)).toBe(false)
    expect(isFresh(fakeIdeaOut({ first_seen: null }), '2026-08-14T09:00:00')).toBe(false)
  })

  it('una data illeggibile non promuove nessuno a contatto nuovo', () => {
    expect(isFresh(fakeIdeaOut({ first_seen: 'boh' }), '2026-08-14T09:00:00')).toBe(false)
  })

  it('blipsFor propaga la freschezza sul blip', () => {
    const since = '2026-08-14T09:00:00'
    const blips = blipsFor(
      [
        fakeIdeaOut({ id: 1, first_seen: '2026-08-14T09:30:00' }),
        fakeIdeaOut({ id: 2, first_seen: '2026-08-01T09:30:00' }),
      ],
      [],
      since,
    )
    expect(blips.find((b) => b.idea.id === 1)?.fresh).toBe(true)
    expect(blips.find((b) => b.idea.id === 2)?.fresh).toBe(false)
  })
})
