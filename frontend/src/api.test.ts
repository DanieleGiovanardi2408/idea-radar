/* Il client API: costruzione delle query string, il totale da X-Total-Count e
 * la gestione degli errori HTTP. Nessun componente: solo il contratto di rete. */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { fakeIdeaOut, mockFetch } from './test/utils'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('api.ideas', () => {
  it('traduce i parametri nella query string, omettendo i default', async () => {
    const { calls } = mockFetch({ '/ideas': [] })
    await api.ideas({
      status: 'proposed',
      offset: 100,
      limit: 100,
      profile: 'ai-agents',
      q: 'lora',
    })
    expect(calls[0].url).toBe(
      '/ideas?status=proposed&offset=100&limit=100&profile=ai-agents&q=lora',
    )
    await api.ideas()
    expect(calls[1].url).toBe('/ideas')
  })

  it('legge il totale filtrato da X-Total-Count', async () => {
    mockFetch({
      '/ideas': () =>
        new Response(JSON.stringify([fakeIdeaOut()]), {
          status: 200,
          headers: { 'Content-Type': 'application/json', 'X-Total-Count': '42' },
        }),
    })
    const page = await api.ideas()
    expect(page.rows).toHaveLength(1)
    expect(page.total).toBe(42)
  })

  it('senza header il totale è la pagina stessa, non zero', async () => {
    mockFetch({ '/ideas': [fakeIdeaOut(), fakeIdeaOut({ id: 2 })] })
    const page = await api.ideas()
    expect(page.total).toBe(2)
  })

  it('un errore HTTP diventa un errore, non una lista vuota', async () => {
    mockFetch({ '/ideas': () => new Response('giù', { status: 500 }) })
    await expect(api.ideas()).rejects.toThrow('HTTP 500')
  })
})

describe('api.patchIdea', () => {
  it('manda PATCH con il body JSON e risponde con l’idea aggiornata', async () => {
    const { calls } = mockFetch({ '/ideas/7': fakeIdeaOut({ id: 7, pinned: true }) })
    const updated = await api.patchIdea(7, { pinned: true })
    expect(calls[0]).toMatchObject({
      url: '/ideas/7',
      method: 'PATCH',
      body: { pinned: true },
    })
    expect(updated.pinned).toBe(true)
  })

  it('un rifiuto del server è un errore esplicito', async () => {
    mockFetch({ '/ideas/7': () => new Response('no', { status: 422 }) })
    await expect(api.patchIdea(7, { note: 'x' })).rejects.toThrow('HTTP 422')
  })
})
