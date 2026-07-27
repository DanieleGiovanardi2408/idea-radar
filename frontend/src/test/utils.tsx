import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import type { ReactElement } from 'react'
import type { IdeaDetailOut, IdeaOut } from '../types'

/** Un QueryClient per test: nessun retry, così un errore è immediato e visibile. */
export function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

/** `route` serve a chi testa il routing: parte da un indirizzo diverso da "/". */
export function renderWithProviders(
  ui: ReactElement,
  client = makeClient(),
  route = '/',
) {
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
      </QueryClientProvider>,
    ),
  }
}

/** Un'idea completa: i test sovrascrivono solo i campi che li riguardano. */
export function fakeIdea(overrides: Partial<IdeaDetailOut> = {}): IdeaDetailOut {
  return {
    id: 1,
    label: 'Show HN: un runtime self-hosted per agenti',
    summary: 'Fa girare agenti in locale.',
    status: 'proposed',
    topic_id: 7,
    topic_label: 'Agenti locali',
    composite: 0.42,
    heat: 0.6,
    credibility: 0.5,
    feasibility: 0.5,
    opportunity: 0.7,
    fit: 1,
    profile: 'ai-agents',
    why_text: 'Risolve un problema vero.',
    difficulty: 'med',
    n_items: 1,
    first_seen: '2026-07-20T10:00:00',
    last_seen: '2026-07-26T10:00:00',
    pinned: false,
    dismissed_at: null,
    seen_at: null,
    note: null,
    items: [],
    history: [],
    ...overrides,
  }
}

export function fakeIdeaOut(overrides: Partial<IdeaOut> = {}): IdeaOut {
  const { history: _history, ...idea } = fakeIdea(overrides as Partial<IdeaDetailOut>)
  return idea
}

/** Sostituisce `fetch` con una tabella rotta-per-rotta. */
export function mockFetch(
  routes: Record<string, unknown | ((init?: RequestInit) => unknown)>,
): { calls: { url: string; method: string; body: unknown }[] } {
  const calls: { url: string; method: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      calls.push({
        url,
        method,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      })
      const key = Object.keys(routes).find((k) => url.startsWith(k))
      if (key === undefined) {
        return new Response('non gestita', { status: 404 })
      }
      const value = routes[key]
      const payload = typeof value === 'function' ? value(init) : value
      if (payload instanceof Response) return payload
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }),
  )
  return { calls }
}
