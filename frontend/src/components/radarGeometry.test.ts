/* La geometria del quadrante.
 *
 * Il radar fa tre affermazioni che a schermo non si possono verificare: la
 * distanza dal centro *è* `1 − composite`, l'angolo raggruppa per topic, e la
 * mappa non balla tra due render sugli stessi dati. Finché stavano dentro il
 * componente nessuno le aveva messe alla prova.
 */

import { describe, expect, it } from 'vitest'
import {
  blipsFor,
  CENTER,
  MAX_BLIPS,
  R_MAX,
  R_MIN,
  radiusOf,
} from './radarGeometry'
import { fakeIdeaOut } from '../test/utils'

const idea = (id: number, composite: number, topic_id = 1) =>
  fakeIdeaOut({ id, composite, topic_id, label: `Idea ${id}` })

describe('geometria dei blip', () => {
  it('il punteggio migliore sta più vicino al centro', () => {
    const [migliore, peggiore] = blipsFor([idea(1, 0.9), idea(2, 0.1)])
    expect(radiusOf(migliore)).toBeLessThan(radiusOf(peggiore))
  })

  it('la distanza è 1 − composite, riscalata tra R_MIN e R_MAX', () => {
    // Le due estremità fissano la scala: composite 1 al bordo interno,
    // composite 0 su quello esterno.
    const [pieno] = blipsFor([idea(1, 1)])
    const [vuoto] = blipsFor([idea(1, 0)])
    expect(radiusOf(pieno)).toBeCloseTo(R_MIN, 5)
    expect(radiusOf(vuoto)).toBeCloseTo(R_MAX, 5)

    const [mezzo] = blipsFor([idea(1, 0.5)])
    expect(radiusOf(mezzo)).toBeCloseTo(R_MIN + (R_MAX - R_MIN) / 2, 5)
  })

  it('un composite fuori scala non esce dal quadrante', () => {
    // Il backend non dovrebbe mandarli, ma un blip a 3000px dal centro sarebbe
    // invisibile e inspiegabile: il clamp è la differenza tra un dato sbagliato
    // e un'interfaccia rotta.
    for (const composite of [-5, 1.4, 99]) {
      const [blip] = blipsFor([idea(1, composite)])
      const r = radiusOf(blip)
      expect(r).toBeGreaterThanOrEqual(R_MIN - 0.001)
      expect(r).toBeLessThanOrEqual(R_MAX + 0.001)
    }
  })

  it('idee dello stesso topic finiscono nello stesso settore', () => {
    const blips = blipsFor([
      idea(1, 0.5, 10),
      idea(2, 0.5, 10),
      idea(3, 0.5, 20),
      idea(4, 0.5, 20),
    ])
    const perTopic = new Map<number, number[]>()
    for (const b of blips) {
      const key = b.idea.topic_id ?? -1
      perTopic.set(key, [...(perTopic.get(key) ?? []), b.angle])
    }
    // Due topic, due settori da 180°: ogni gruppo sta dentro il suo.
    const [primo, secondo] = [...perTopic.values()]
    expect(Math.max(...primo)).toBeLessThan(180)
    expect(Math.min(...secondo)).toBeGreaterThanOrEqual(180)
  })

  it('la stessa lista dà le stesse posizioni: la mappa non balla', () => {
    const ideas = [idea(1, 0.7, 3), idea(2, 0.4, 9), idea(3, 0.55, 3)]
    expect(blipsFor(ideas)).toEqual(blipsFor(ideas))
    // Nemmeno se arrivano in ordine diverso: l'ordinamento è interno.
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
    // Il composite più alto è dentro, il più basso è fuori.
    const dentro = blips.map((b) => b.idea.id)
    expect(dentro).toContain(MAX_BLIPS + 20)
    expect(dentro).not.toContain(1)
  })

  it('senza idee non c’è niente da disegnare', () => {
    expect(blipsFor([])).toEqual([])
  })

  it('un topic assente non spacca la geometria', () => {
    const [blip] = blipsFor([idea(1, 0.5, undefined as unknown as number)])
    expect(Number.isFinite(blip.x)).toBe(true)
    expect(Number.isFinite(blip.y)).toBe(true)
    expect(radiusOf(blip)).toBeGreaterThan(0)
  })

  it('il centro del quadrante è il riferimento', () => {
    const [blip] = blipsFor([idea(1, 1)])
    // composite 1 e un solo topic: il blip sta sopra il centro, a R_MIN.
    expect(blip.x).toBeCloseTo(CENTER + R_MIN * Math.cos(((blip.angle - 90) * Math.PI) / 180), 5)
  })
})
