<script setup lang="ts">
import type { NavigationMenuItem } from '@nuxt/ui'

const { user, logout } = useAuth()
const { companies, active, activeId, load, setActive, isOwner } = useCompany()
const { connect } = useEventStream()
const { lastEvent, openContact } = useChatEvents()
const toast = useToast()
const personal = usePersonalChat()

await useAsyncData('companies', async () => {
  await load()
  return true
})

// sem empresa nenhuma, toda tela depende de company_id: manda pro onboarding (index cria a empresa)
const route = useRoute()
watch(() => [route.path, companies.value.length] as const, ([path, count]) => {
  if (!count && path !== '/' && !path.startsWith('/convite')) navigateTo('/', { replace: true })
}, { immediate: true })

// ---- SSE app-wide: notifica handoff de qualquer página ----
let eventSeq = 0
let disconnectStream: (() => void) | null = null

function goToConversation(contactId: number) {
  openContact.value = contactId
  navigateTo('/conversas')
}

function notifyBrowser(title: string, body: string, contactId: number) {
  if (!('Notification' in window) || Notification.permission !== 'granted' || !document.hidden) return
  const notification = new Notification(title, { body })
  notification.onclick = () => {
    window.focus()
    goToConversation(contactId)
  }
}

watch(activeId, (id) => {
  disconnectStream?.()
  disconnectStream = null
  if (!id) return
  disconnectStream = connect(`/companies/${id}/events`, {
    onEvent(event) {
      lastEvent.value = { seq: ++eventSeq, event }
      if (event.type === 'handoff') {
        const name = event.name || 'Um cliente'
        toast.add({
          title: `${name} pediu atendimento humano`,
          description: 'A IA pausou e está aguardando você assumir.',
          color: 'warning',
          icon: 'i-lucide-headset',
          duration: 0,
          actions: [{ label: 'Abrir conversa', onClick: () => goToConversation(event.contact_id) }]
        })
        notifyBrowser(`${name} pediu atendimento humano`, 'Clique pra abrir o Chatly', event.contact_id)
      }
    },
    onReconnect() {
      lastEvent.value = { seq: ++eventSeq, event: { type: 'reconnect', contact_id: 0 } }
    }
  })
}, { immediate: true })
onUnmounted(() => disconnectStream?.())

// ---- WhatsApp pessoal (OpenWA): driver único do estado compartilhado (página + widget) ----
watch(activeId, async () => {
  personal.resetInbox()
  await personal.loadConnection()
  if (personal.connected.value) personal.loadContacts()
}, { immediate: true })

watch(personal.selectedId, personal.loadMessages)

watch(lastEvent, (entry) => {
  const e = entry?.event
  if (!e || e.account_id !== personal.connection.value?.id) return
  if (e.type === 'personal_status') {
    personal.loadConnection()
  } else if (e.type === 'personal_contacts') {
    personal.loadContacts() // sync de fotos terminou no worker
  } else if (e.channel === 'personal' && e.type === 'message') {
    personal.loadContacts()
    if (e.contact_id === personal.selectedId.value) personal.loadMessages()
  }
})

const nav = computed<NavigationMenuItem[]>(() => [
  { label: 'Conversas', icon: 'i-lucide-messages-square', to: '/conversas' },
  { label: 'Meu WhatsApp', icon: 'i-lucide-smartphone', to: '/meu-whatsapp' },
  { label: 'Contatos', icon: 'i-lucide-book-user', to: '/contatos' },
  { label: 'Campanhas', icon: 'i-lucide-megaphone', to: '/campanhas' },
  { label: 'Templates', icon: 'i-lucide-layout-template', to: '/templates' },
  { label: 'Agentes', icon: 'i-lucide-bot', to: '/agentes' },
  // member não administra nada - backend devolve 404 nas rotas de admin de qualquer forma
  ...(isOwner.value
    ? [
        { label: 'Automações', icon: 'i-lucide-workflow', to: '/automacoes' },
        { label: 'Configurações', icon: 'i-lucide-settings', to: '/configuracoes' }
      ]
    : [])
])

// teste grátis acabando ou plano vencido (sem cobrança ligada, a empresa vem como business sem vencimento)
const planNotice = computed(() => {
  const company = active.value
  if (!company?.plan_until) return null
  if (!company.plan_active) {
    return { color: 'error' as const, title: 'Plano expirado', description: 'A IA, campanhas e automações estão pausadas. Fale com a gente para assinar.' }
  }
  if (company.plan !== 'trial') return null
  const days = Math.max(1, Math.ceil((new Date(company.plan_until).getTime() - Date.now()) / 86_400_000))
  return { color: 'primary' as const, title: `Teste grátis: ${days} ${days === 1 ? 'dia' : 'dias'}`, description: 'Fale com a gente para assinar.' }
})

const companyItems = computed(() =>
  companies.value.map(c => ({ label: c.name, value: c.id }))
)

const colorMode = useColorMode()
const isDark = computed(() => colorMode.value === 'dark')
function toggleTheme() {
  colorMode.preference = isDark.value ? 'light' : 'dark'
}

const changePasswordOpen = ref(false)

const userMenuItems = computed(() => [
  [{
    label: 'Trocar senha',
    icon: 'i-lucide-key-round',
    onSelect: () => { changePasswordOpen.value = true }
  }, {
    label: isDark.value ? 'Tema claro' : 'Tema escuro',
    icon: isDark.value ? 'i-lucide-sun' : 'i-lucide-moon',
    onSelect: toggleTheme
  }],
  [{
    label: 'Sair',
    icon: 'i-lucide-log-out',
    color: 'error' as const,
    onSelect: () => logout()
  }]
])
</script>

<template>
  <UDashboardGroup storage="localStorage">
    <UDashboardSidebar collapsible>
      <template #header="{ collapsed }">
        <div
          class="flex items-center gap-2 w-full"
          :class="collapsed ? 'justify-center' : ''"
        >
          <UIcon
            v-if="!collapsed"
            name="i-lucide-message-circle-heart"
            class="size-6 text-primary shrink-0"
          />
          <span
            v-if="!collapsed"
            class="flex-1 font-display font-bold text-lg tracking-tight"
          >Chatly</span>
          <UDashboardSidebarCollapse />
        </div>
      </template>

      <template #default="{ collapsed }">
        <USelect
          v-if="!collapsed && companies.length > 1"
          :model-value="activeId ?? undefined"
          :items="companyItems"
          icon="i-lucide-building-2"
          class="mb-2"
          @update:model-value="setActive($event as number)"
        />
        <UNavigationMenu
          :items="nav"
          orientation="vertical"
          :ui="{ link: collapsed ? 'justify-center' : undefined }"
        />
        <UAlert
          v-if="planNotice && !collapsed"
          :color="planNotice.color"
          variant="subtle"
          icon="i-lucide-sparkles"
          :title="planNotice.title"
          :description="planNotice.description"
          class="mt-auto"
        />
      </template>

      <template #footer="{ collapsed }">
        <UDropdownMenu
          :items="userMenuItems"
          :content="{ align: 'center', collisionPadding: 12 }"
          :ui="{ content: collapsed ? 'w-48' : 'w-(--reka-dropdown-menu-trigger-width)' }"
          class="w-full"
        >
          <UButton
            :avatar="{ alt: user?.email ?? 'U' }"
            :label="collapsed ? undefined : user?.email"
            :trailing-icon="collapsed ? undefined : 'i-lucide-chevrons-up-down'"
            color="neutral"
            variant="ghost"
            block
            :square="collapsed"
            class="data-[state=open]:bg-elevated"
            :ui="{ label: 'truncate text-xs', trailingIcon: 'text-dimmed size-4 shrink-0' }"
          />
        </UDropdownMenu>
      </template>
    </UDashboardSidebar>

    <slot />

    <AppChatWidget />
    <AppChangePassword v-model:open="changePasswordOpen" />
  </UDashboardGroup>
</template>
