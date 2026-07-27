/* Le date, formattate in un posto solo.
 *
 * `formatTime` in MonitorView e `shortDate` in TrendsView erano la stessa
 * funzione con due nomi — stesse opzioni, stesso locale — e `formatDate` in
 * IdeaDetail la loro cugina. Tre copie significano tre posti dove cambiare il
 * giorno che diventa "oggi", e nessuna garanzia che restino d'accordo.
 *
 * Il locale è fisso a it-IT perché l'interfaccia è in italiano: una data in un
 * formato diverso dal testo che la circonda si legge peggio, non meglio.
 */

const LOCALE = 'it-IT'
const ASSENTE = '—'

function parse(value: string | null | undefined): Date | null {
  if (!value) return null
  const d = new Date(value)
  // Una data non valida stampava "Invalid Date" in mezzo all'interfaccia:
  // meglio il trattino che si usa già per i valori mancanti.
  return Number.isNaN(d.getTime()) ? null : d
}

/** Giorno, mese e ora: "27 lug, 14:39". Per i run e i punti di un trend. */
export function dateAndTime(value: string | null | undefined): string {
  const d = parse(value)
  if (!d) return ASSENTE
  return d.toLocaleString(LOCALE, {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** Solo il giorno, con l'anno: "27 lug 2026". Per prima/ultima volta visto. */
export function dayMonthYear(value: string | null | undefined): string {
  const d = parse(value)
  if (!d) return ASSENTE
  return d.toLocaleString(LOCALE, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

/** Quanto tempo è passato, in forma corta: "ora", "6h", "2g". */
export function timeAgo(value: string | null | undefined): string {
  const d = parse(value)
  if (!d) return ''
  const hours = Math.round((Date.now() - d.getTime()) / 3_600_000)
  if (hours < 1) return 'ora'
  if (hours < 24) return `${hours}h`
  return `${Math.round(hours / 24)}g`
}
