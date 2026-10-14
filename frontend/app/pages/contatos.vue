<script setup lang="ts">
import type { Contact, CsvRecipient, Paginated, Tag, WhatsAppAccount } from '~/types/api'

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

// filtro por etiqueta
const tagFilter = ref(0) // 0 = todas
const tags = ref<Tag[]>([])
const tagItems = computed(() => tags.value.map(t => ({ label: t.name, value: t.id })))
watch(companyId, async (id) => {
  tags.value = id ? await api<Tag[]>(`/companies/${id}/tags`) : []
}, { immediate: true })
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
      `/companies/${companyId.value}/whatsapp-accounts/${accountId.value}/contacts?limit=100&search=${encodeURIComponent(search.value)}${tagFilter.value ? `&tag=${tagFilter.value}` : ''}`
    )
    contacts.value = page.items
    total.value = page.count
  } finally {
    loading.value = false
  }
}
watch([accountId, tagFilter], () => loadContacts(), { immediate: true })

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

// ---- import CSV: o modal de mapeamento diz o que é cada coluna; backend revalida os números ----
const csvInput = ref<HTMLInputElement | null>(null)
const fieldsOpen = ref(false)
const csvText = ref('')
const mappingOpen = ref(false)
const importing = ref(false)

function onCsvPicked(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    csvText.value = String(reader.result)
    mappingOpen.value = true
  }
  reader.readAsText(file)
  ;(event.target as HTMLInputElement).value = ''
}

async function runImport({ rows }: { rows: CsvRecipient[] }) {
  if (!rows.length || importing.value) return
  importing.value = true
  try {
    const result = await api<{ created: number, existing: number }>(
      `/companies/${companyId.value}/whatsapp-accounts/${accountId.value}/contacts/import`,
      { method: 'POST', body: { numbers: rows } }
    )
    toast.add({
      title: `${result.created} contato(s) criado(s), ${result.existing} já existiam`,
      icon: 'i-lucide-users'
    })
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
            label="Campos"
            icon="i-lucide-list-plus"
            size="sm"
            variant="soft"
            @click="() => { fieldsOpen = true }"
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
        <USelect
          v-if="tagItems.length"
          v-model="tagFilter"
          :items="[{ label: 'Todas as etiquetas', value: 0 }, ...tagItems]"
          icon="i-lucide-tag"
          placeholder="Etiqueta"
          size="sm"
          class="w-44"
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
          <AppContactTags
            :contact="contact"
            :company-id="companyId ?? 0"
            size="xs"
            @updated="updated => contacts = contacts.map(c => (c.id === updated.id ? { ...c, tags: updated.tags } : c))"
          />
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

      <CsvMappingModal
        v-if="companyId"
        v-model:open="mappingOpen"
        :text="csvText"
        :company-id="companyId"
        confirm-label="Importar {n} contato(s)"
        @confirm="runImport"
      />
    </template>
  </UDashboardPanel>
  <UModal v-model:open="fieldsOpen" title="Campos personalizados">
    <template #body>
      <ContactFieldsManager v-if="companyId" :company-id="companyId" />
    </template>
  </UModal>
</template>
