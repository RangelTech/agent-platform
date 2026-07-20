import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { fetchMe, getToken, login as apiLogin, logout as apiLogout, setToken, type Me } from './api'

interface AuthState {
  user: Me | null
  loading: boolean
  signIn: (email: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  refresh: () => Promise<void>
  can: (resource: string, action: string) => boolean
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!getToken()) {
      setLoading(false)
      return
    }
    fetchMe()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    // api() fires this when the backend rejects the session.
    const onExpired = () => setUser(null)
    window.addEventListener('auth:expired', onExpired)
    return () => window.removeEventListener('auth:expired', onExpired)
  }, [])

  async function signIn(email: string, password: string) {
    const { token } = await apiLogin(email, password)
    setToken(token)
    setUser(await fetchMe())
  }

  async function signOut() {
    await apiLogout().catch(() => undefined)
    setToken(null)
    setUser(null)
  }

  async function refresh() {
    if (!getToken()) return
    setUser(await fetchMe())
  }

  function can(resource: string, action: string) {
    if (!user) return false
    if (user.is_master) return true
    return (user.permissions[resource] ?? []).includes(action)
  }

  return (
    <AuthContext.Provider value={{ user, loading, signIn, signOut, refresh, can }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth precisa estar dentro de AuthProvider')
  return ctx
}
