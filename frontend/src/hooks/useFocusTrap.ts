/* Trappola del focus per le finestre modali.
 *
 * Un drawer con `aria-modal="true"` dichiara a chi usa uno screen reader che
 * fuori da lì non c'è niente da leggere. Se poi il Tab esce e continua a girare
 * nella pagina sotto, quella dichiarazione è falsa: si finisce a navigare
 * elementi invisibili, senza capire dove si è. `aria-modal` è una promessa che
 * qualcuno deve mantenere, e a mano non la mantiene nessuno.
 *
 * L'hook fa tre cose: porta il focus dentro all'apertura, lo tiene dentro
 * ciclando sul primo/ultimo elemento, e lo restituisce a chi ce l'aveva quando
 * la modale si chiude — così chi arriva da tastiera non riparte dall'inizio
 * della pagina.
 */

import { useEffect, type RefObject } from 'react'

/* Ordine di tabulazione: gli elementi che il browser considera raggiungibili.
   `:not([disabled])` e `tabindex="-1"` esclusi, perché un bottone spento o un
   contenitore programmatico non sono tappe di un percorso da tastiera. */
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function visibile(el: HTMLElement): boolean {
  /* Un elemento nascosto è nel DOM ma non nel percorso di tabulazione. Il modo
     ovvio — `offsetParent !== null` — dipende dal layout, e in jsdom il layout
     non esiste: sotto test scartava *tutti* gli elementi e la trappola non
     trovava nulla da mettere a fuoco. `getComputedStyle` risponde in entrambi
     gli ambienti e guarda la proprietà giusta. */
  if (el.hasAttribute('hidden')) return false
  const stile = getComputedStyle(el)
  return stile.display !== 'none' && stile.visibility !== 'hidden'
}

function focusables(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    visibile,
  )
}

export function useFocusTrap(
  ref: RefObject<HTMLElement | null>,
  active = true,
): void {
  useEffect(() => {
    if (!active) return
    const container = ref.current
    if (!container) return

    const restituisciA = document.activeElement as HTMLElement | null

    // Il primo elemento utile, o il contenitore stesso: senza questo il focus
    // resterebbe sul bottone che ha aperto la modale, dietro l'overlay.
    const primi = focusables(container)
    if (primi.length > 0) {
      primi[0].focus()
    } else {
      container.setAttribute('tabindex', '-1')
      container.focus()
    }

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return
      const elenco = focusables(container)
      if (elenco.length === 0) {
        e.preventDefault()
        return
      }
      const primo = elenco[0]
      const ultimo = elenco[elenco.length - 1]
      // Il focus può essere fuori (click sull'overlay, o elemento rimosso): in
      // quel caso Tab lo riporta dentro invece di lasciarlo scappare.
      if (!container.contains(document.activeElement)) {
        e.preventDefault()
        primo.focus()
        return
      }
      if (!e.shiftKey && document.activeElement === ultimo) {
        e.preventDefault()
        primo.focus()
      } else if (e.shiftKey && document.activeElement === primo) {
        e.preventDefault()
        ultimo.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      // `isConnected`: se l'elemento di partenza non è più nel documento
      // (lista rifetchata mentre il drawer era aperto) non si insegue un
      // fantasma, si lascia il focus dove il browser lo mette.
      if (restituisciA?.isConnected) restituisciA.focus()
    }
  }, [ref, active])
}
