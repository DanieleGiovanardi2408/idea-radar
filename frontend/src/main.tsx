import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.tsx'

// Un solo client per l'app: la cache di React Query è lo stato server-side.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1, // backend locale: se non risponde subito, inutile insistere
      refetchOnWindowFocus: false, // il polling condizionale basta e avanza
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
