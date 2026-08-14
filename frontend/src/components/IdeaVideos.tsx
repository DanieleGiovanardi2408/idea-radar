/* "Cosa dicono di questa cosa": la ricerca YouTube sul label di UN'idea.
 *
 * Fratello povero di TrendingVideos, e di proposito. Là il pannello è una sala
 * controllo — un player acceso, l'audio, le dirette — perché è una vista che
 * sta aperta; qui siamo dentro un dossier che si legge e si chiude, e servono
 * quattro miniature che portano su YouTube.
 *
 * Sopratutto: NON parte da sola. Una ricerca costa 100 unità delle 10.000 al
 * giorno di quota, e un dossier si apre molte più volte di quante i video
 * interessino davvero — quindi la spesa la autorizza un click. La cache del
 * backend (15 minuti) rende gratis i ripensamenti.
 */

import { useState } from 'react'
import { useIdeaVideos } from '../hooks/useRadarData'
import { timeAgo } from '../dates'

export function IdeaVideos({ ideaId }: { ideaId: number }) {
  const [asked, setAsked] = useState(false)
  const { data, isFetching, isError } = useIdeaVideos(ideaId, asked)

  if (!asked) {
    return (
      <button
        onClick={() => setAsked(true)}
        className="glass glass-hover rounded-lg px-2.5 py-1.5 text-xs font-medium text-phosphor"
      >
        ✦ Cerca cosa se ne dice su YouTube
      </button>
    )
  }

  if (isFetching && !data) {
    return <p className="text-xs text-slate-600">Sintonizzazione…</p>
  }
  if (isError) {
    return <p className="text-xs text-flare">Ricerca non riuscita.</p>
  }
  if (data && !data.configured) {
    return <p className="text-xs leading-relaxed text-slate-600">{data.detail}</p>
  }
  if (!data?.videos.length) {
    return (
      <p className="text-xs text-slate-600">
        Nessuno ne parla — il che, per un'idea in salita, è il punto.
      </p>
    )
  }

  return (
    <ul className="space-y-1">
      {data.videos.map((video) => (
        <li key={video.video_id}>
          <a
            href={video.url}
            target="_blank"
            rel="noreferrer"
            // Il nome del link è il titolo del video: la miniatura è
            // decorativa (alt vuoto) e il canale non serve a distinguerlo.
            aria-label={video.title}
            className="group flex items-start gap-2.5 rounded-lg p-1.5 transition-colors hover:bg-white/[0.04]"
          >
            <img
              src={video.thumbnail}
              alt=""
              loading="lazy"
              className="h-[45px] w-20 shrink-0 rounded-md object-cover opacity-70 transition-opacity group-hover:opacity-100"
            />
            <span className="min-w-0 flex-1">
              <span className="line-clamp-2 block text-[11px] leading-snug text-slate-400 group-hover:text-slate-100">
                {video.title}
              </span>
              <span className="mt-0.5 flex items-center gap-1.5 text-[10px] text-slate-600">
                <span className="truncate">{video.channel}</span>
                {video.published_at && (
                  <span className="shrink-0">· {timeAgo(video.published_at)}</span>
                )}
              </span>
            </span>
          </a>
        </li>
      ))}
    </ul>
  )
}
