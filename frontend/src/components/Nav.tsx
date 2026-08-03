/* La navigazione, col suo indicatore scorrevole.
 *
 * Stava in App.tsx assieme a tutto il resto: quattro pezzi di stato (ref della
 * nav, ref dei tab, posizione dell'indicatore, effetto di misura) che non
 * servivano a nessun altro. Qui dentro sono dettagli di un componente; là erano
 * quattro righe in più da scavalcare per capire cosa fa l'applicazione.
 *
 * Non è un `role="tablist"`: i tab sono link con un URL e una pagina propria.
 * Marcarli come tab farebbe perdere a chi usa uno screen reader l'informazione
 * che sono link, e imporrebbe la navigazione a frecce di un widget composito.
 * `<nav>` con un nome più l'`aria-current` che NavLink già emette è il pattern
 * corretto da quando il routing è passato agli URL.
 */

import { useLayoutEffect, useRef, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'

const TABS: [string, string][] = [
  ['/radar', 'Radar'],
  ['/topics', 'Topic'],
  ['/trends', 'Trend'],
  ['/sviluppo', 'Sviluppo'],
  ['/monitor', 'Monitor'],
]

export function Nav({
  counts,
}: {
  /** Contatori per rotta: la nav dice quante idee e quanti temi ci sono. */
  counts: Record<string, number | undefined>
}) {
  const { pathname } = useLocation()
  const navRef = useRef<HTMLElement | null>(null)
  const tabRefs = useRef<Record<string, HTMLAnchorElement | null>>({})
  const [indicator, setIndicator] = useState({ left: 0, width: 0 })

  /* I contatori arrivano async e cambiano la larghezza dei tab, quindi
     l'indicatore va rimisurato. Una firma dei valori invece di elencare le
     rotte a mano: aggiungere un tab con un contatore non richiede di ricordarsi
     di toccare anche questa lista. */
  const firmaContatori = TABS.map(([to]) => counts[to] ?? '').join('|')

  useLayoutEffect(() => {
    const measure = () => {
      const tab = tabRefs.current[pathname]
      const nav = navRef.current
      if (!tab || !nav) return
      const tabBox = tab.getBoundingClientRect()
      const navBox = nav.getBoundingClientRect()
      setIndicator({ left: tabBox.left - navBox.left, width: tabBox.width })
    }
    measure()
    window.addEventListener('resize', measure)
    // il font display arriva async: rimisura quando è pronto
    document.fonts?.ready.then(measure).catch(() => undefined)
    return () => window.removeEventListener('resize', measure)
  }, [pathname, firmaContatori])

  return (
    <nav
      ref={navRef}
      aria-label="Viste del radar"
      className="glass view-enter relative mt-6 flex gap-1 rounded-2xl p-1.5"
    >
      {/* indicatore scorrevole */}
      <span
        className="absolute top-1.5 bottom-1.5 rounded-xl bg-phosphor/12 shadow-[inset_0_0_0_1px_rgba(46,232,162,0.28),0_0_18px_-4px_rgba(46,232,162,0.35)] transition-all duration-300 ease-out"
        style={{ left: indicator.left, width: indicator.width }}
      />
      {TABS.map(([to, label]) => (
        <NavLink
          key={to}
          to={to}
          ref={(el) => {
            tabRefs.current[to] = el
          }}
          className={({ isActive }) =>
            `relative z-10 flex-1 rounded-xl px-3.5 py-2 text-center font-display text-sm font-medium tracking-tight transition-colors duration-300 sm:flex-none ${
              isActive ? 'text-phosphor' : 'text-slate-500 hover:text-slate-300'
            }`
          }
        >
          {label}
          {counts[to] !== undefined && counts[to]! > 0 && (
            <span
              className={`ml-1.5 text-xs tabular-nums ${
                pathname === to ? 'text-phosphor/60' : 'text-slate-600'
              }`}
            >
              {counts[to]}
            </span>
          )}
        </NavLink>
      ))}
    </nav>
  )
}
