/* Piccolo sistema di motion: niente librerie, solo rAF e CSS.
   Tutto rispetta prefers-reduced-motion (i numeri saltano al valore finale). */

import { useEffect, useRef, useState } from 'react'

export function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

/** Conta da 0 (o dal valore precedente) al target con easing, via rAF. */
export function useCountUp(target: number, durationMs = 750): number {
  const [value, setValue] = useState(() => (prefersReducedMotion() ? target : 0))
  const fromRef = useRef(0)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    if (prefersReducedMotion()) {
      setValue(target)
      return
    }
    const from = fromRef.current
    const start = performance.now()
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / durationMs)
      const eased = 1 - Math.pow(1 - t, 3)
      const current = from + (target - from) * eased
      setValue(current)
      if (t < 1) {
        rafRef.current = requestAnimationFrame(tick)
      } else {
        fromRef.current = target
      }
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      fromRef.current = target
    }
  }, [target, durationMs])

  return value
}

export function AnimatedNumber({
  value,
  decimals = 0,
}: {
  value: number
  decimals?: number
}) {
  const animated = useCountUp(value)
  return <>{animated.toFixed(decimals)}</>
}

/** Ritardo di ingresso scaglionato, con un tetto per le liste lunghe. */
export function staggerDelay(index: number, stepMs = 45, maxSteps = 14): {
  animationDelay: string
} {
  return { animationDelay: `${Math.min(index, maxSteps) * stepMs}ms` }
}
