<script setup lang="ts">
import type { Contact, Paginated, WhatsAppAccount } from '~/types/api'

const api = useApi()
const toast = useToast()
const prompt = usePrompt()
const confirm = useConfirm()
const { activeId: companyId } = useCompany()
const { openContact, openContactData } = useChatEvents()

// ---- contas WhatsApp (padrão de conversas) ----
const { data: accounts } = await useAsyncData(
  () => `accounts:${companyId.value}`,
  () => api<WhatsAppAccount[]>(`/companies/${companyId.value}/whatsapp-accounts`),
  { watch: [companyId], default: () => [] as WhatsAppAccount[] }
)
const accountId = ref<number | null>(null)
watch(accounts, list => { accountId.value = list?.[0]?.id ?? null }, { immediate: true })

const accountItems = computed(() =>
  (accounts.value ?? []).map(a => ({ label: a.phone_number_id, value: a.id }))
)

// ---- lista + busca server-side ----
const contacts = ref<Contact[]>([])
const total = ref(0)
const search = ref('')
const loading = ref(false)

async function loadContacts() {
  if (!companyId.value || !accountId.value) {
    contacts.value = []
    total.value = 0
    return
  }
  loading.value = true
  try {
    const page = await api<Paginated<Contact>>(
      `/companies/${companyId.value}/whatsapp-accounts/${accountId.value}/contacts?limit=100&search=${encodeURIComponent(search.value)}`
    )
    contacts.value = page.items
    total.value = page.count
  } finally {
    loading.value = false
  }
}
watch(accountId, () => loadContacts(), { immediate: true })

let searchTimer: ReturnType<typeof setTimeout> | undefined
watch(search, () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(loadContacts, 300)
})

// ---- conversar: entrega o objeto pra página de conversas (contato mudo não está na lista de lá) ----
function talkTo(contact: Contact) {
  openContactData.value = contact
  openContact.value = contact.id
  navigateTo('/conversas')
}

// ---- renomear ----
async function rename(contact: Contact) {
  const name = await prompt({
    title: 'Renomear contato',
    description: contact.wa_id,
    placeholder: contact.name || 'Nome do contato',
    confirmLabel: 'Renomear'
  })
  if (name === null) return
  try {
    const updated = await api<Contact>(
      `/companies/${companyId.value}/contacts/${contact.id}`,
      { method: 'PATCH', body: { name } }
    )
    // PATCH devolve sem annotations - só o nome interessa
    contacts.value = contacts.value.map(c => (c.id === updated.id ? { ...c, name: updated.name } : c))
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao renomear'), color: 'error' })
  }
}

// ---- excluir ----
async function remove(contact: Contact) {
  const ok = await confirm({
    title: `Excluir ${contact.name || contact.wa_id}?`,
    description: 'Apaga também todas as mensagens e a memória da IA deste contato. Não dá pra desfazer.',
    confirmLabel: 'Excluir'
  })
  if (!ok) return
  try {
    await api(`/companies/${companyId.value}/contacts/${contact.id}`, { method: 'DELETE' })
    contacts.value = contacts.value.filter(c => c.id !== contact.id)
    total.value -= 1
    toast.add({ title: 'Contato excluído', icon: 'i-lucide-trash-2' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao excluir'), color: 'error' })
  }
}

// ---- import CSV: "numero,nome" por linha, parse no browser - backend revalida ----
const csvInput = ref<HTMLInputElement | null>(null)
const importNumbers = ref<{ number: string, name: string }[]>([])
const importInvalid = ref<string[]>([])
const importModalOpen = ref(false)
const importing = ref(false)

function onCsvPicked(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    importNumbers.value = []
    importInvalid.value = []
    for (const line of String(reader.result).split(/\r?\n/)) {
      if (!line.trim()) continue
      const [rawNumber = '', ...rest] = line.split(',')
      const digits = rawNumber.replace(/\D/g, '')
      if (digits.length >= 10 && digits.length <= 15) {
        importNumbers.value.push({ number: digits, name: rest.join(',').trim() })
      } else {
        importInvalid.value.push(line.trim())
      }
    }
    importModalOpen.value = true
  }
  reader.readAsText(file)
  ;(event.target as HTMLInputElement).value = ''
}

async function runImport() {
  if (!importNumbers.value.length || importing.value) return
  importing.value = true
  try {
    const result = await api<{ created: number, existing: number }>(
      `/companies/${companyId.value}/whatsapp-accounts/${accountId.value}/contacts/import`,
      { method: 'POST', body: { numbers: importNumbers.value } }
    )
    toast.add({
      title: `${result.created} contato(s) criado(s), ${result.existing} já existiam`,
      icon: 'i-lucide-users'
    })
    importModalOpen.value = false
    loadContacts()
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao importar'), color: 'error' })
  } finally {
    importing.value = false
  }
}

// ---- helpers ----
const dateFmt = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit' })
const lastTalk = (iso: string | null) => (iso ? dateFmt.format(new Date(iso)) : null)
</script>

<template>
  <UDashboardPanel id="contatos">
    <template #header>
      <UDashboardNavbar title="Contatos">
        <template #right>
          <USelect
            v-if="accountItems.length > 1"
            v-model="accountId"
            :items="accountItems"
            icon="i-lucide-phone"
            size="sm"
          />
          <UButton
            v-if="accountId"
            label="Importar CSV"
            icon="i-lucide-upload"
            size="sm"
            @click="csvInput?.click()"
          />
          <input ref="csvInput" type="file" accept=".csv,.txt" class="hidden" @change="onCsvPicked">
        </template>
      </UDashboardNavbar>
      <UDashboardToolbar>
        <UInput
          v-model="search"
          icon="i-lucide-search"
          placeholder="Buscar por nome ou número..."
          class="w-full max-w-sm"
          size="sm"
        />
        <span class="text-xs text-muted ml-auto tabular-nums">{{ total }} contato(s)</span>
      </UDashboardToolbar>
    </template>

    <template #body>
      <div v-if="!accountId" class="p-4 text-sm text-muted">
        Nenhuma conta WhatsApp conectada. Configure uma em
        <NuxtLink to="/configuracoes" class="text-primary">Configurações</NuxtLink>.
      </div>

      <div v-else class="divide-y divide-default -my-2">
        <div
          v-for="contact in contacts"
          :key="contact.id"
          class="flex items-center gap-3 py-2.5 group"
        >
          <UAvatar :alt="contact.name || contact.wa_id" size="md" />
          <div class="flex-1 min-w-0">
            <p class="font-medium truncate">{{ contact.name || 'Sem nome' }}</p>
            <p class="text-xs text-muted font-mono truncate">{{ contact.wa_id }}</p>
          </div>
          <UBadge v-if="!contact.last_message_at" color="neutral" variant="subtle" size="sm">
            Sem conversa
          </UBadge>
          <span v-else class="text-xs text-muted tabular-nums" title="Última mensagem">
            {{ lastTalk(contact.last_message_at) }}
          </span>
          <div class="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <UButton
              icon="i-lucide-pencil"
              variant="ghost"
              color="neutral"
              size="sm"
              aria-label="Renomear"
              title="Renomear"
              @click="rename(contact)"
            />
            <UButton
              icon="i-lucide-trash-2"
              variant="ghost"
              color="error"
              size="sm"
              aria-label="Excluir"
              title="Excluir"
              @click="remove(contact)"
            />
            <UButton
              label="Conversar"
              icon="i-lucide-message-circle"
              variant="soft"
              size="sm"
              @click="talkTo(contact)"
            />
          </div>
        </div>

        <p v-if="!contacts.length && !loading" class="p-4 text-sm text-muted">
          {{ search ? 'Nenhum contato encontrado.' : 'Nenhum contato ainda - importe um CSV ou aguarde mensagens chegarem.' }}
        </p>
      </div>

      <UModal
        v-model:open="importModalOpen"
        title="Importar contatos"
        description="Uma linha por contato: numero,nome (nome opcional)"
      >
        <template #body>
          <div class="space-y-4">
            <UAlert
              icon="i-lucide-users"
              color="primary"
              variant="subtle"
              :title="`${importNumbers.length} número(s) válido(s)`"
            />
            <UAlert
              v-if="importInvalid.length"
              icon="i-lucide-triangle-alert"
              color="warning"
              variant="subtle"
              :title="`${importInvalid.length} linha(s) ignorada(s) - número inválido`"
              :description="importInvalid.slice(0, 3).join(' · ')"
            />
            <UButton
              block
              icon="i-lucide-upload"
              :label="`Importar ${importNumbers.length} contato(s)`"
              :loading="importing"
              :disabled="!importNumbers.length"
              @click="runImport()"
            />
          </div>
        </template>
      </UModal>
    </template>
  </UDashboardPanel>
</template>
