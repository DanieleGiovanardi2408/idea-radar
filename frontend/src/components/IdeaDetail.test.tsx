/* I tre difetti che ho trovato usando l'app invece di leggerla, il 27/07.
 *
 * Con 258 test sul backend e zero sul frontend, questi erano arrivati fino
 * all'utente: il pin che non reagiva, la nota scartata in silenzio, e un dossier
 * di sola lettura per tutta la durata di una PATCH automatica che nessuno aveva
 * chiesto. Sono il motivo per cui questa cartella esiste. */

import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { IdeaDetail } from './IdeaDetail'
import { fakeIdea, mockFetch, renderWithProviders } from '../test/utils'

function open(idea = fakeIdea(), extra: Record<string, unknown> = {}) {
  const mock = mockFetch({
    // Le rotte di `extra` PRIMA: il mock sceglie per prefisso, quindi
    // '/ideas/1' ingoierebbe '/ideas/1/videos' se venisse per prima.
    ...extra,
    '/ideas/1': (init?: RequestInit) =>
      init?.method === 'PATCH'
        ? { ...idea, ...JSON.parse(String(init.body)) }
        : idea,
  })
  renderWithProviders(<IdeaDetail ideaId={1} onClose={() => {}} />)
  return mock
}

describe('IdeaDetail', () => {
  it('il Tab resta dentro il dossier, come promette aria-modal', async () => {
    const user = userEvent.setup()
    open()
    await screen.findByRole('button', { name: /pinna in cima/i })

    const dialog = screen.getByRole('dialog')
    // Si gira tutto il giro: il focus non deve mai finire fuori dal drawer,
    // altrimenti si naviga la pagina che lo screen reader dichiara assente.
    for (let giro = 0; giro < 12; giro++) {
      await user.tab()
      expect(dialog.contains(document.activeElement)).toBe(true)
    }
  })

  it('il pin resta cliccabile mentre parte il "visto" automatico', async () => {
    // Regressione: pin, scarta e "Salva nota" condividevano UNA mutation, e la
    // PATCH `seen: true` all'apertura li disabilitava tutti finché era in volo.
    open()

    const pin = await screen.findByRole('button', { name: /pinna in cima/i })
    expect(pin).toBeEnabled()
    expect(screen.getByRole('button', { name: /^salva nota$/i })).toBeEnabled()
  })

  it('il pin cambia stato subito, senza aspettare il server', async () => {
    const user = userEvent.setup()
    open(fakeIdea({ pinned: false }), {})

    await user.click(await screen.findByRole('button', { name: /pinna in cima/i }))

    // Aggiornamento ottimistico: l'etichetta si ribalta appena, non al 200.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /togli il pin/i })).toBeInTheDocument(),
    )
  })

  it('salva la nota anche subito dopo un pin', async () => {
    // Regressione: `saveNote` faceva `if (!idea || patching) return`, quindi
    // bastava aver cliccato il pin perché la nota venisse scartata IN SILENZIO.
    const user = userEvent.setup()
    const { calls } = open()

    await user.click(await screen.findByRole('button', { name: /pinna in cima/i }))
    await user.type(screen.getByRole('textbox'), 'da guardare bene')
    await user.click(screen.getByRole('button', { name: /^salva nota$/i }))

    await waitFor(() => {
      const notePatch = calls.find(
        (c) => c.method === 'PATCH' && (c.body as { note?: string })?.note !== undefined,
      )
      expect(notePatch?.body).toEqual({ note: 'da guardare bene' })
    })
  })

  it('un salvataggio fallito lo dice, invece di non accadere', async () => {
    const user = userEvent.setup()
    const idea = fakeIdea()
    mockFetch({
      '/ideas/1': (init?: RequestInit) =>
        init?.method === 'PATCH' && String(init.body).includes('note')
          ? new Response('kaputt', { status: 500 })
          : idea,
    })
    renderWithProviders(<IdeaDetail ideaId={1} onClose={() => {}} />)

    await user.type(await screen.findByRole('textbox'), 'una nota')
    await user.click(screen.getByRole('button', { name: /^salva nota$/i }))

    expect(await screen.findByText(/salvataggio fallito/i)).toBeInTheDocument()
  })

  it('non chiama il server se la nota non è cambiata', async () => {
    const user = userEvent.setup()
    const { calls } = open(fakeIdea({ note: 'già salvata' }))

    await screen.findByRole('textbox')
    await user.click(screen.getByRole('button', { name: /^salva nota$/i }))

    const notePatches = calls.filter(
      (c) => c.method === 'PATCH' && (c.body as { note?: string })?.note !== undefined,
    )
    expect(notePatches).toHaveLength(0)
  })

  it('mostra il tema (profilo) accanto al topic', async () => {
    open(fakeIdea({ profile: 'domotica', topic_label: 'Home Assistant' }))

    expect(await screen.findByText('domotica')).toBeInTheDocument()
    expect(screen.getByText('Home Assistant')).toBeInTheDocument()
  })

  it("non mostra alcun tema se nessuno reclama l'idea", async () => {
    open(fakeIdea({ profile: null, label: 'Qualcosa di fuori tema' }))

    await screen.findByText('Qualcosa di fuori tema')
    expect(screen.queryByText('ai-agents')).not.toBeInTheDocument()
  })

  it('marca l\'idea come vista una volta sola', async () => {
    const { calls } = open()

    await screen.findByRole('button', { name: /pinna in cima/i })
    await waitFor(() => {
      const seen = calls.filter(
        (c) => c.method === 'PATCH' && (c.body as { seen?: boolean })?.seen === true,
      )
      expect(seen).toHaveLength(1)
    })
  })

  it('mostra le mosse e l\'angolo di business quando ci sono', async () => {
    open(
      fakeIdea({
        moves: ['Costruisci il wrapper CLI che manca', 'Scrivi il benchmark di riferimento'],
        angle: 'Il cliente sono i team di piattaforma senza GPU.',
      }),
    )

    expect(await screen.findByText(/le mosse/i)).toBeInTheDocument()
    expect(screen.getByText('Costruisci il wrapper CLI che manca')).toBeInTheDocument()
    expect(screen.getByText(/angolo di business/i)).toBeInTheDocument()
    expect(
      screen.getByText('Il cliente sono i team di piattaforma senza GPU.'),
    ).toBeInTheDocument()
  })

  it('niente sezione mosse per un\'idea che non le ha ancora', async () => {
    open(fakeIdea({ moves: null, angle: null }))

    await screen.findByRole('button', { name: /pinna in cima/i })
    expect(screen.queryByText(/le mosse/i)).not.toBeInTheDocument()
  })

  it('il bordo si ridimensiona da tastiera e la scelta viene ricordata', async () => {
    const user = userEvent.setup()
    localStorage.removeItem('idea-radar:drawer-width')
    open()
    const handle = await screen.findByRole('separator')

    handle.focus()
    await user.keyboard('{ArrowLeft}')

    // ← allarga: larghezza esplicita sull'aside e persistita per la prossima volta.
    const aside = screen.getByRole('dialog').querySelector('aside')!
    const dopoUnPasso = parseInt(aside.style.width, 10)
    expect(dopoUnPasso).toBeGreaterThan(0)
    expect(localStorage.getItem('idea-radar:drawer-width')).toBe(String(dopoUnPasso))

    // → restringe.
    await user.keyboard('{ArrowRight}')
    expect(parseInt(aside.style.width, 10)).toBeLessThan(dopoUnPasso)

    // Doppio click: si torna al default responsive (niente width inline).
    await user.dblClick(handle)
    expect(aside.style.width).toBe('')
    expect(localStorage.getItem('idea-radar:drawer-width')).toBeNull()
  })
})

describe('IdeaVideos nel dossier', () => {
  it('non cerca finché non glielo chiedi: la quota la autorizza un click', async () => {
    /* Una ricerca YouTube costa 100 unità delle 10.000 al giorno, e un dossier
       si apre molte più volte di quante i video interessino. */
    const { calls } = open(fakeIdea(), {
      '/ideas/1/videos': {
        configured: true,
        videos: [
          {
            video_id: 'v1',
            title: 'Il runtime spiegato',
            channel: 'Canale Tech',
            published_at: '2026-07-26T10:00:00Z',
            thumbnail: '',
            live: false,
            profile: null,
            url: 'https://www.youtube.com/watch?v=v1',
            embed_url: 'https://www.youtube-nocookie.com/embed/v1',
          },
        ],
      },
    })

    await screen.findByRole('button', { name: /Cerca cosa se ne dice/ })
    expect(calls.some((c) => c.url.includes('/videos'))).toBe(false)

    await userEvent.click(
      screen.getByRole('button', { name: /Cerca cosa se ne dice/ }),
    )

    expect(
      await screen.findByRole('link', { name: /Il runtime spiegato/ }),
    ).toHaveAttribute('href', 'https://www.youtube.com/watch?v=v1')
  })

  it('nessun video è un\'informazione, non un buco', async () => {
    open(fakeIdea(), {
      '/ideas/1/videos': { configured: true, videos: [] },
    })

    await userEvent.click(
      await screen.findByRole('button', { name: /Cerca cosa se ne dice/ }),
    )

    expect(await screen.findByText(/Nessuno ne parla/)).toBeInTheDocument()
  })
})

describe('il mock di fetch', () => {
  it('non lascia passare richieste non previste', async () => {
    mockFetch({})
    const res = await fetch('/qualcosa')
    expect(res.status).toBe(404)
    vi.unstubAllGlobals()
  })
})
