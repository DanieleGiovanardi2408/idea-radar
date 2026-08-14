/* La geometria del quadrante, separata dal disegno.
 *
 * Qui vivono le tre affermazioni che il radar fa e che non si possono verificare
 * guardando lo schermo: la distanza dal centro è `1 − composite` (le opportunità
 * migliori stanno sulla rotta, vicine al centro), l'angolo è uno SPICCHIO
 * stabile per profilo (il macro-tema dichiarato in config), e la posizione non
 * cambia tra due render con gli stessi dati.
 *
 * Prima lo spicchio era per topic: con 150 topic i settori erano coriandoli da
 * 2.4° — matematicamente veri, visivamente rumore. Con 4-5 profili l'angolo
 * torna a dire qualcosa: "sta nascendo roba negli agenti, il mio spicchio IoT
 * è vuoto" si vede a colpo d'occhio.
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
  /** Vista per la prima volta nell'ultimo run: il radar la segnala come nuova. */
  fresh: boolean
}

export type Sector = {
  /** null = idee che nessun profilo reclama ("senza tema"). */
  profile: string | null
  start: number
  end: number
}

/** Un punto sul quadrante: angolo in gradi (0 = ore 12, orario) e raggio. */
export function pointAt(angleDeg: number, radius: number): { x: number; y: number } {
  const rad = ((angleDeg - 90) * Math.PI) / 180
  return {
    x: CENTER + radius * Math.cos(rad),
    y: CENTER + radius * Math.sin(rad),
  }
}

/** Gli spicchi del quadrante: uno per profilo PRESENTE tra le idee.
 *
 *  `order` (i profili come configurati) decide la sequenza; i profili non in
 *  lista seguono in ordine alfabetico, e il cestino "senza tema" chiude sempre
 *  il giro. Larghezza uguale per tutti, di proposito: pesare gli spicchi sul
 *  numero di idee li farebbe ballare a ogni run, e la stabilità della mappa
 *  vale più della densità. */
export function sectorsFor(ideas: IdeaOut[], order: string[] = []): Sector[] {
  const present = new Set<string | null>(ideas.map((i) => i.profile ?? null))
  const named: (string | null)[] = [
    ...order.filter((p) => present.has(p)),
    ...[...present]
      .filter((p): p is string => p !== null && !order.includes(p))
      .sort(),
  ]
  if (present.has(null)) named.push(null)
  const width = 360 / Math.max(named.length, 1)
  return named.map((profile, index) => ({
    profile,
    start: index * width,
    end: (index + 1) * width,
  }))
}

/** Se un'idea è comparsa nell'ultimo run.
 *
 * `first_seen` è il momento in cui l'idea è nata, `since` l'inizio dell'ultimo
 * run: nata dopo che il run è partito = l'ha trovata quel run. Senza `since`
 * (nessun run in archivio) nessuno è nuovo — meglio non dire niente che dire
 * "nuovo" a tutti, che è lo stesso che non dirlo. */
export function isFresh(idea: IdeaOut, since?: string | null): boolean {
  if (!since || !idea.first_seen) return false
  const nata = new Date(idea.first_seen).getTime()
  const inizio = new Date(since).getTime()
  if (Number.isNaN(nata) || Number.isNaN(inizio)) return false
  return nata >= inizio
}

export function blipsFor(
  ideas: IdeaOut[],
  order: string[] = [],
  freshSince?: string | null,
): Blip[] {
  const sectors = sectorsFor(ideas, order)
  const sectorIndex = new Map(sectors.map((s, i) => [s.profile, i]))
  const width = 360 / Math.max(sectors.length, 1)

  return ideas
    .slice()
    .sort((a, b) => b.composite - a.composite)
    .slice(0, MAX_BLIPS)
    .map((idea) => {
      const index = sectorIndex.get(idea.profile ?? null) ?? 0
      // Spirale aurea dentro lo spicchio: sparpaglia senza casualità, con un
      // margine dai bordi perché un blip A CAVALLO di due temi mentirebbe.
      const offset = width * 0.08 + ((idea.id * 137.508) % (width * 0.84))
      const angle = index * width + offset
      const radius =
        R_MIN + (1 - Math.max(0, Math.min(1, idea.composite))) * (R_MAX - R_MIN)
      const { x, y } = pointAt(angle, radius)
      return {
        idea,
        angle,
        x,
        y,
        proposed: idea.status === 'proposed',
        fresh: isFresh(idea, freshSince),
      }
    })
}

/** Distanza dal centro di un blip: comoda nei test e per leggere il codice. */
export function radiusOf(blip: Blip): number {
  return Math.hypot(blip.x - CENTER, blip.y - CENTER)
}
