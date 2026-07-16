const PUBLIC_PATHS = ['/login', '/register']

export default defineNuxtRouteMiddleware((to) => {
  const { accessToken } = useAuth()
  const isPublic = PUBLIC_PATHS.includes(to.path)

  if (!accessToken.value && !isPublic) {
    // convite aberto deslogado: guarda o token - index.vue retoma depois do login/registro
    if (to.path.startsWith('/convite/')) localStorage.setItem('chatly_invite', String(to.params.token))
    return navigateTo('/login')
  }
  if (accessToken.value && isPublic) return navigateTo('/')
})
