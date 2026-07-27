import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Ogni test parte da un DOM pulito: senza, i componenti di un test restano
// montati e le query trovano elementi del test precedente.
afterEach(cleanup)
