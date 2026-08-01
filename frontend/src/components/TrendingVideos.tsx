/* Chi sta parlando adesso di ciò che il radar guarda.
 *
 * Contesto, non segnali: i video non entrano nella pipeline, non diventano idee
 * e non vengono scorati. La ricerca è per tema (i profili di config.yaml), così
 * il pannello risponde alla domanda giusta — "cosa si dice DEI MIEI temi" — e non
 * "cosa va forte su YouTube".
 *
 * UN SOLO player, in cima, che parte da solo MUTO: è l'ambiente di una sala
 * controllo, dove i monitor sono sempre accesi e senza volume. Montare un iframe
 * per ogni video peserebbe più di tutto il resto del radar, quindi la lista sotto
 * resta di miniature e cliccarne una la promuove in cima — con l'audio, perché
 * quel click è una richiesta esplicita di ascoltare.
 *
 * L'audio si accende con un overlay sopra il player, non con un pulsante accanto:
 * un iframe si mangia i click, quindi "clicca il video per sentirlo" ha bisogno
 * di un bersaglio che stia davanti. Appena l'audio è acceso l'overlay sparisce e
 * i comandi di YouTube tornano raggiungibili. */

import { useState } from 'react'
import { Panel } from './ui'
import { useVideos } from '../hooks/useRadarData'
import type { VideoOut } from '../types'
import { timeAgo } from '../dates'

function IconSpeakerOff({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 20 20" fill="none" className={`size-4 ${className}`}>
      <path
        d="M4 8h2.5L10 4.5v11L6.5 12H4V8Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path
        d="m13 8 4 4m0-4-4 4"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  )
}

function IconSpeakerOn({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 20 20" fill="none" className={`size-4 ${className}`}>
      <path
        d="M4 8h2.5L10 4.5v11L6.5 12H4V8Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path
        d="M13 7.5a3.5 3.5 0 0 1 0 5M15.2 5.5a6.5 6.5 0 0 1 0 9"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  )
}

/** Il player. `muted` non è solo un parametro: cambiandolo l'iframe si rimonta
 *  (la `key` lo include) e il video riparte da zero. È deliberato — l'alternativa
 *  è la JS API di YouTube, cioè dipendere da un canale postMessage che non
 *  possiamo testare qui: preferisco perdere cinque secondi di video in modo
 *  prevedibile che avere un pulsante audio che a volte non fa niente. */
function Stage({
  video,
  muted,
  onUnmute,
  onStop,
}: {
  video: VideoOut
  muted: boolean
  onUnmute: () => void
  onStop: () => void
}) {
  const params = new URLSearchParams({
    autoplay: '1',
    mute: muted ? '1' : '0',
    rel: '0',
    playsinline: '1',
    modestbranding: '1',
  })
  // Dentro una webview (app desktop) YouTube rifiuta i contesti che non sa
  // validare — "Error 153". Dichiarare l'origin, insieme all'origine https
  // della webview (useHttpsScheme in tauri.conf.json), gli dà un referer
  // verificabile. Nel browser è innocuo.
  if (window.location.protocol.startsWith('http')) {
    params.set('origin', window.location.origin)
  }
  return (
    <div className="relative overflow-hidden rounded-xl ring-1 ring-phosphor/25">
      <div className="aspect-video bg-black/40">
        <iframe
          key={`${video.video_id}:${muted}`}
          src={`${video.embed_url}?${params}`}
          title={video.title}
          allow="accelerometer; autoplay; encrypted-media; picture-in-picture"
          referrerPolicy="strict-origin-when-cross-origin"
          allowFullScreen
          className="size-full"
        />
      </div>

      {/* Finché è muto l'overlay copre il video: è il bersaglio del click. */}
      {muted && (
        <button
          onClick={onUnmute}
          aria-label={`Attiva l'audio di ${video.title}`}
          /* Al centro, non in basso: in basso ci sono i comandi di YouTube e la
             pastiglia sembrerebbe parte di quelli. */
          className="group absolute inset-0 flex items-center justify-center bg-abyss/25 transition-colors hover:bg-abyss/45"
        >
          <span className="inline-flex items-center gap-1.5 rounded-full bg-abyss/85 px-2.5 py-1.5 text-[10px] font-medium text-slate-300 ring-1 ring-white/10 backdrop-blur transition-colors group-hover:text-phosphor group-hover:ring-phosphor/40">
            <IconSpeakerOff />
            clicca per sentire
          </span>
        </button>
      )}

      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-2 p-2">
        {video.live ? (
          <span className="inline-flex items-center gap-1 rounded bg-flare/90 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-abyss">
            <span className="size-1 animate-pulse rounded-full bg-abyss" />
            live
          </span>
        ) : (
          <span />
        )}
        <button
          onClick={onStop}
          aria-label="Chiudi il player"
          className="pointer-events-auto rounded-full bg-abyss/70 p-1 text-slate-400 ring-1 ring-white/10 backdrop-blur transition-colors hover:text-slate-100"
        >
          <svg viewBox="0 0 16 16" fill="none" className="size-3">
            <path
              d="m4.5 4.5 7 7m0-7-7 7"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>
    </div>
  )
}

export function TrendingVideos() {
  const [live, setLive] = useState(false)
  const [active, setActive] = useState<string | null>(null)
  const [muted, setMuted] = useState(true)
  // Chi spegne il player non vuole che il prossimo refetch lo riaccenda.
  const [stopped, setStopped] = useState(false)
  const { data, isPending, isError } = useVideos({ limit: 7, live })

  const videos = data?.videos ?? []
  const chosen = videos.find((v) => v.video_id === active) ?? null
  // Nessuna scelta esplicita: va in scena il primo, cioè il più visto.
  const onStage = chosen ?? videos[0] ?? null
  // L'audio vive sulla scelta, non sul pannello: se il video scelto sparisce
  // (filtro cambiato, nuovi risultati) si torna all'ambiente muto da sé, invece
  // di lasciare un audio orfano su un video che l'utente non ha chiesto.
  const silent = muted || chosen === null

  const promote = (video: VideoOut) => {
    setActive(video.video_id)
    setMuted(false) // click esplicito su un altro video = voglio sentirlo
    setStopped(false)
  }

  /** Accendere l'audio del video di default lo rende una scelta esplicita. */
  const unmute = () => {
    if (onStage) setActive(onStage.video_id)
    setMuted(false)
  }

  const others = onStage
    ? videos.filter((v) => v.video_id !== onStage.video_id)
    : videos

  return (
    <Panel className="flex flex-col p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="hud text-slate-500">Se ne parla</h3>
        <div className="flex items-center gap-1">
          {onStage && !stopped && (
            <button
              onClick={() => (silent ? unmute() : setMuted(true))}
              aria-label={silent ? 'Attiva audio' : 'Silenzia'}
              aria-pressed={!silent}
              className={`rounded p-1 transition-colors ${
                silent
                  ? 'text-slate-600 hover:text-slate-400'
                  : 'text-phosphor hover:text-phosphor/80'
              }`}
            >
              {silent ? <IconSpeakerOff /> : <IconSpeakerOn />}
            </button>
          )}
          <button
            onClick={() => {
              setLive((v) => !v)
              setActive(null)
              setMuted(true)
              setStopped(false)
            }}
            aria-pressed={live}
            className={`flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[10px] transition-colors ${
              live ? 'bg-flare/15 text-flare' : 'text-slate-600 hover:text-slate-400'
            }`}
          >
            {live && (
              <span className="relative flex size-1.5">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-flare opacity-70" />
                <span className="relative inline-flex size-1.5 rounded-full bg-flare" />
              </span>
            )}
            solo dirette
          </button>
        </div>
      </div>

      {isError && (
        <p className="mt-3 text-xs text-flare">
          Pannello video non raggiungibile.
        </p>
      )}
      {isPending && !data && (
        <div className="mt-3 space-y-2">
          <div className="aspect-video animate-pulse rounded-xl bg-white/[0.04]" />
          <p className="text-xs text-slate-600">Sintonizzazione…</p>
        </div>
      )}

      {/* Senza chiave il pannello spiega cosa fare, invece di restare vuoto. */}
      {data && !data.configured && (
        <p className="mt-3 text-xs leading-relaxed text-slate-600">
          {data.detail}
        </p>
      )}

      {data?.configured && videos.length === 0 && (
        <p className="mt-3 text-xs text-slate-600">
          {live
            ? 'Nessuna diretta sui tuoi temi in questo momento.'
            : 'Nessun video trovato sui tuoi temi.'}
        </p>
      )}

      {onStage && (
        <div className="mt-3 shrink-0">
          {stopped ? (
            <button
              onClick={() => setStopped(false)}
              aria-label="Riprendi la riproduzione"
              className="group relative block w-full overflow-hidden rounded-xl ring-1 ring-white/10"
            >
              <img
                src={onStage.thumbnail}
                alt=""
                className="aspect-video w-full object-cover opacity-50 transition-opacity group-hover:opacity-80"
              />
              <span className="absolute inset-0 flex items-center justify-center">
                <span className="rounded-full bg-abyss/80 px-3 py-1 text-[10px] text-slate-300 ring-1 ring-white/10 group-hover:text-phosphor">
                  riprendi
                </span>
              </span>
            </button>
          ) : (
            <Stage
              video={onStage}
              muted={silent}
              onUnmute={unmute}
              onStop={() => setStopped(true)}
            />
          )}

          <div className="mt-2">
            <a
              href={onStage.url}
              target="_blank"
              rel="noreferrer"
              className="line-clamp-2 block text-xs leading-snug font-medium text-slate-200 transition-colors hover:text-phosphor"
            >
              {onStage.title}
            </a>
            <p className="mt-1 flex items-center gap-1.5 text-[10px] text-slate-500">
              <span className="truncate">{onStage.channel}</span>
              {onStage.published_at && !onStage.live && (
                <span className="shrink-0">· {timeAgo(onStage.published_at)}</span>
              )}
              {onStage.profile && (
                <span className="ml-auto shrink-0 truncate text-slate-600">
                  {onStage.profile}
                </span>
              )}
            </p>
          </div>
        </div>
      )}

      {others.length > 0 && (
        /* `min-h-0` è obbligatorio: senza, un figlio in overflow di un flex
           container lo fa crescere invece di far scorrere il contenuto. */
        <ul className="mt-3 min-h-0 flex-1 space-y-1 overflow-y-auto pr-0.5">
          {others.map((video) => (
            <li key={video.video_id}>
              <button
                onClick={() => promote(video)}
                className="group flex w-full items-start gap-2.5 rounded-lg p-1.5 text-left transition-colors hover:bg-white/[0.04]"
              >
                <span className="relative shrink-0 overflow-hidden rounded-md">
                  <img
                    src={video.thumbnail}
                    alt=""
                    loading="lazy"
                    className="h-[45px] w-20 object-cover opacity-70 transition-opacity group-hover:opacity-100"
                  />
                  {video.live && (
                    <span className="absolute left-1 top-1 rounded bg-flare/90 px-1 text-[8px] font-semibold uppercase text-abyss">
                      live
                    </span>
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="line-clamp-2 block text-[11px] leading-snug text-slate-400 group-hover:text-slate-100">
                    {video.title}
                  </span>
                  <span className="mt-0.5 flex items-center gap-1.5 text-[10px] text-slate-600">
                    <span className="truncate">{video.channel}</span>
                    {video.published_at && !video.live && (
                      <span className="shrink-0">· {timeAgo(video.published_at)}</span>
                    )}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
