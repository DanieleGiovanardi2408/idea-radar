import { useEffect, useState } from 'react'

/** Il valore, ma aggiornato solo quando smette di cambiare per `delay` ms.
 *
 *  Serve alla ricerca server-side: una query per pausa di digitazione,
 *  non una per tasto. Il primo valore passa subito (nessun flash vuoto). */
export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}
