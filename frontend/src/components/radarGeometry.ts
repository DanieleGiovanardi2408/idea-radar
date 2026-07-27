/* La geometria del quadrante, separata dal disegno.
 *
 * Qui vivono le tre affermazioni che il radar fa e che non si possono verificare
 * guardando lo schermo: la distanza dal centro è `1 − composite` (le opportunità
 * migliori stanno sulla rotta, vicine al centro), l'angolo è un settore stabile
 * per topic, e la posizione non cambia tra due render con gli stessi dati. Erano
 * dentro il componente, quindi non erano mai state messe alla prova.
 */

import type { IdeaOut } from '../types'

export const SIZE = 440
export const CENTER = SIZE / 2
export const R_MIN = 46
export const R_MAX = 190
export const MAX_BLIPS = 60

export type Blip = {
  idea: IdeaOut
  x: number
  y: number
  angle: number
  proposed: boolean
}

export function blipsFor(ideas: IdeaOut[]): Blip[] {
  const topicOrder = Array.from(
    new Set(ideas.map((i) => i.topic_id ?? -1)),
  ).sort((a, b) => a - b)
  const sector = 360 / Math.max(topicOrder.length, 1)

  return ideas
    .slice()
    .sort((a, b) => b.composite - a.composite)
    .slice(0, MAX_BLIPS)
    .map((idea) => {
      const sectorIndex = topicOrder.indexOf(idea.topic_id ?? -1)
      // Spirale aurea dentro il settore: sparpaglia senza casualità.
      const offset = sector * 0.08 + ((idea.id * 137.508) % (sector * 0.84))
      const angle = sectorIndex * sector + offset
      const radius =
        R_MIN + (1 - Math.max(0, Math.min(1, idea.composite))) * (R_MAX - R_MIN)
      const rad = ((angle - 90) * Math.PI) / 180
      return {
        idea,
        angle,
        x: CENTER + radius * Math.cos(rad),
        y: CENTER + radius * Math.sin(rad),
        proposed: idea.status === 'proposed',
      }
    })
}

/** Distanza dal centro di un blip: comoda nei test e per leggere il codice. */
export function radiusOf(blip: Blip): number {
  return Math.hypot(blip.x - CENTER, blip.y - CENTER)
}
