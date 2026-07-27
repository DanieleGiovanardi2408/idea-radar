import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { TrendingVideos } from './TrendingVideos'
import { mockFetch, renderWithProviders } from '../test/utils'
import type { VideoOut, VideosOut } from '../types'

function video(overrides: Partial<VideoOut> = {}): VideoOut {
  return {
    video_id: 'abc123',
    title: 'Costruire agenti che funzionano davvero',
    channel: 'Canale Tech',
    published_at: new Date(Date.now() - 3 * 3_600_000).toISOString(),
    thumbnail: 'https://i.ytimg.com/abc123.jpg',
    live: false,
    profile: 'ai-agents',
    url: 'https://www.youtube.com/watch?v=abc123',
    embed_url: 'https://www.youtube-nocookie.com/embed/abc123',
    ...overrides,
  }
}

const second = video({
  video_id: 'def456',
  title: 'Domotica locale senza cloud',
  channel: 'Altro Canale',
  published_at: new Date(Date.now() - 2 * 86_400_000).toISOString(),
  embed_url: 'https://www.youtube-nocookie.com/embed/def456',
})

function show(data: Partial<VideosOut> = {}) {
  const payload: VideosOut = {
    configured: true,
    videos: [video()],
    detail: null,
    cached: false,
    ...data,
  }
  const mock = mockFetch({ '/videos': payload, '/stats': { last_run: null } })
  renderWithProviders(<TrendingVideos />)
  return mock
}

function frames(): HTMLIFrameElement[] {
  return Array.from(document.querySelectorAll('iframe'))
}

describe('TrendingVideos', () => {
  it('senza la chiave spiega cosa fare, invece di restare vuoto', async () => {
    show({
      configured: false,
      videos: [],
      detail: 'Serve YOUTUBE_API_KEY in backend/.env (chiave gratuita: …).',
    })

    expect(await screen.findByText(/Serve YOUTUBE_API_KEY/)).toBeInTheDocument()
  })

  it('elenca i video con canale e quanto tempo è passato', async () => {
    show({ videos: [video(), second] })

    expect(
      await screen.findByText('Costruire agenti che funzionano davvero'),
    ).toBeInTheDocument()
    expect(screen.getByText('Canale Tech')).toBeInTheDocument()
    expect(screen.getByText(/3h/)).toBeInTheDocument()
    // il secondo resta una miniatura nella lista, con la sua età
    expect(screen.getByText('Domotica locale senza cloud')).toBeInTheDocument()
    expect(screen.getByText(/2g/)).toBeInTheDocument()
  })

  it('il primo video parte da solo, muto, e con un solo player', async () => {
    // Un iframe per video peserebbe più di tutto il resto del radar.
    show({ videos: [video(), second] })

    await waitFor(() => expect(frames()).toHaveLength(1))
    const src = frames()[0].src
    expect(src).toContain('autoplay=1')
    expect(src).toContain('mute=1')
    // nocookie: il pannello non piazza cookie di Google sul radar.
    expect(src).toContain('youtube-nocookie.com/embed/abc123')
  })

  it("cliccando il video si accende l'audio", async () => {
    const user = userEvent.setup()
    show()

    await waitFor(() => expect(frames()).toHaveLength(1))
    await user.click(screen.getByRole('button', { name: /attiva l'audio/i }))

    await waitFor(() => expect(frames()[0].src).toContain('mute=0'))
    // acceso l'audio, l'overlay non copre più i comandi di YouTube
    expect(
      screen.queryByRole('button', { name: /attiva l'audio/i }),
    ).not.toBeInTheDocument()
  })

  it("cliccare un altro video lo porta in scena, con l'audio", async () => {
    const user = userEvent.setup()
    show({ videos: [video(), second] })

    await user.click(
      await screen.findByRole('button', { name: /domotica locale/i }),
    )

    await waitFor(() => {
      expect(frames()).toHaveLength(1)
      expect(frames()[0].src).toContain('embed/def456')
      expect(frames()[0].src).toContain('mute=0')
    })
  })

  it('il player si può spegnere e torna la miniatura', async () => {
    const user = userEvent.setup()
    show()

    await user.click(
      await screen.findByRole('button', { name: /chiudi il player/i }),
    )

    expect(frames()).toHaveLength(0)
    expect(screen.getByRole('button', { name: /riprendi/i })).toBeInTheDocument()
  })

  it('marca le dirette e chiede al server solo quelle su richiesta', async () => {
    const user = userEvent.setup()
    const { calls } = show({
      videos: [video(), video({ video_id: 'x', live: true })],
    })

    expect(await screen.findByText('live')).toBeInTheDocument()
    expect(calls.some((c) => c.url.includes('live=true'))).toBe(false)

    await user.click(screen.getByRole('button', { name: /solo dirette/i }))

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('live=true'))).toBe(true),
    )
  })

  it('dice quando non ci sono dirette, invece di sembrare rotto', async () => {
    const user = userEvent.setup()
    mockFetch({
      '/videos': () => ({
        configured: true,
        videos: [],
        detail: null,
        cached: false,
      }),
      '/stats': { last_run: null },
    })
    renderWithProviders(<TrendingVideos />)

    await user.click(await screen.findByRole('button', { name: /solo dirette/i }))

    expect(
      await screen.findByText(/Nessuna diretta sui tuoi temi/),
    ).toBeInTheDocument()
  })
})
