import { useEffect, useState } from 'react'

type BackendStatus = 'loading' | 'ok' | 'error'

function App() {
  const [status, setStatus] = useState<BackendStatus>('loading')

  useEffect(() => {
    fetch('/health')
      .then((res) => (res.ok ? res.json() : Promise.reject(res)))
      .then((data: { status: string }) =>
        setStatus(data.status === 'ok' ? 'ok' : 'error'),
      )
      .catch(() => setStatus('error'))
  }, [])

  const badge = {
    loading: { label: 'Verifica in corso…', classes: 'bg-amber-100 text-amber-800' },
    ok: { label: 'Backend online', classes: 'bg-emerald-100 text-emerald-800' },
    error: { label: 'Backend non raggiungibile', classes: 'bg-red-100 text-red-800' },
  }[status]

  return (
    <main className="min-h-screen bg-slate-950 flex flex-col items-center justify-center gap-6 px-4">
      <h1 className="text-4xl font-bold tracking-tight text-slate-100">
        Idea Radar
      </h1>
      <p className="text-slate-400">
        Scaffold iniziale — la logica di business arriverà nelle prossime iterazioni.
      </p>
      <span
        className={`inline-flex items-center gap-2 rounded-full px-4 py-1.5 text-sm font-medium ${badge.classes}`}
      >
        <span className="size-2 rounded-full bg-current" />
        {badge.label}
      </span>
    </main>
  )
}

export default App
