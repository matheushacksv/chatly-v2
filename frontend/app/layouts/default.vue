<script setup lang="ts">
import type { NavigationMenuItem } from '@nuxt/ui'

const { user, logout } = useAuth()
const { companies, activeId, load, setActive, isOwner } = useCompany()
const { connect } = useEventStream()
const { lastEvent, openContact } = useChatEvents()
const toast = useToast()

await useAsyncData('companies', async () => {
  await load()
  return true
})

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

const nav = computed<NavigationMenuItem[]>(() => [
  { label: 'Conversas', icon: 'i-lucide-messages-square', to: '/conversas' },
  { label: 'Contatos', icon: 'i-lucide-book-user', to: '/contatos' },
  { label: 'Campanhas', icon: 'i-lucide-megaphone', to: '/campanhas' },
  { label: 'Agentes', icon: 'i-lucide-bot', to: '/agentes' },
  // member não administra nada - backend devolve 404 nas rotas de admin de qualquer forma
  ...(isOwner.value ? [{ label: 'Configurações', icon: 'i-lucide-settings', to: '/configuracoes' }] : [])
])

const companyItems = computed(() =>
  companies.value.map(c => ({ label: c.name, value: c.id }))
)

const colorMode = useColorMode()
const isDark = computed(() => colorMode.value === 'dark')
function toggleTheme() {
  colorMode.preference = isDark.value ? 'light' : 'dark'
}

const userMenuItems = computed(() => [
  [{
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
        <div class="flex items-center gap-2 w-full" :class="collapsed ? 'justify-center' : ''">
          <UIcon v-if="!collapsed" name="i-lucide-message-circle-heart" class="size-6 text-primary shrink-0" />
          <span v-if="!collapsed" class="flex-1 font-display font-bold text-lg tracking-tight">Chatly</span>
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
  </UDashboardGroup>
</template>
