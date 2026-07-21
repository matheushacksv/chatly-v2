<script setup lang="ts">
import type { Template, WhatsAppAccount } from '~/types/api'

const api = useApi()
const toast = useToast()
const confirmModal = useConfirm()
const { activeId: companyId, isOwner } = useCompany()

// ---- contas WhatsApp ----
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

// ---- templates ----
const templates = ref<Template[]>([])
const loading = ref(false)

async function load() {
  if (!companyId.value || !accountId.value) {
    templates.value = []
    return
  }
  loading.value = true
  try {
    templates.value = await api<Template[]>(`/companies/${companyId.value}/accounts/${accountId.value}/templates`)
  } finally {
    loading.value = false
  }
}
watch(accountId, load, { immediate: true })

const statusBadge: Record<string, { label: string, color: 'success' | 'warning' | 'error' | 'neutral' }> = {
  APPROVED: { label: 'Aprovado', color: 'success' },
  PENDING: { label: 'Em análise', color: 'warning' },
  REJECTED: { label: 'Rejeitado', color: 'error' },
  PAUSED: { label: 'Pausado', color: 'neutral' }
}

// ---- editor ----
const editorOpen = ref(false)
const editingId = ref<string | null>(null)

function openCreate() {
  editingId.value = null
  editorOpen.value = true
}
function openEdit(template: Template) {
  editingId.value = template.id
  editorOpen.value = true
}

// ---- excluir ----
async function remove(template: Template) {
  const ok = await confirmModal({
    title: `Excluir o template "${template.name}"?`,
    description: 'Some do WhatsApp Manager e não pode mais ser usado em campanhas ou mensagens.'
  })
  if (!ok) return
  try {
    await api(
      `/companies/${companyId.value}/accounts/${accountId.value}/templates/${template.id}?name=${encodeURIComponent(template.name)}`,
      { method: 'DELETE' }
    )
    templates.value = templates.value.filter(t => t.id !== template.id)
    toast.add({ title: 'Template excluído', icon: 'i-lucide-trash-2' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao excluir template'), color: 'error' })
  }
}
</script>

<template>
  <UDashboardPanel id="templates">
    <template #header>
      <UDashboardNavbar title="Templates">
        <template #right>
          <USelect
            v-if="accountItems.length > 1"
            v-model="accountId"
            :items="accountItems"
            icon="i-lucide-phone"
            size="sm"
          />
          <UButton v-if="isOwner" label="Novo template" icon="i-lucide-plus" :disabled="!accountId" @click="openCreate()" />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div v-if="!accountId" class="p-4 text-sm text-muted">
        Nenhuma conta WhatsApp conectada. Configure uma em
        <NuxtLink to="/configuracoes" class="text-primary">Configurações</NuxtLink>.
      </div>

      <div v-else-if="loading" class="flex justify-center py-16">
        <UIcon name="i-lucide-loader-circle" class="size-5 animate-spin text-muted" />
      </div>

      <div v-else-if="templates.length" class="space-y-3">
        <div
          v-for="template in templates"
          :key="`${template.name}|${template.language}`"
          class="rounded-xl ring-1 ring-default bg-elevated/40 p-4 flex items-center gap-4"
        >
          <div class="size-9 rounded-lg bg-primary/10 ring-1 ring-primary/20 flex items-center justify-center shrink-0">
            <UIcon name="i-lucide-layout-template" class="size-5 text-primary" />
          </div>
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <p class="font-display font-semibold truncate">{{ template.name }}</p>
              <UBadge :color="statusBadge[template.status]?.color ?? 'neutral'" variant="subtle" size="sm">
                {{ statusBadge[template.status]?.label ?? template.status }}
              </UBadge>
              <UBadge variant="subtle" color="neutral" size="sm">{{ template.language }}</UBadge>
              <UBadge variant="subtle" color="neutral" size="sm">{{ template.category }}</UBadge>
            </div>
            <p class="text-sm text-muted truncate">{{ template.body_text || 'Sem corpo.' }}</p>
          </div>
          <div v-if="isOwner" class="flex gap-1 shrink-0">
            <UButton icon="i-lucide-pencil" variant="ghost" color="neutral" size="xs" @click="openEdit(template)" />
            <UButton icon="i-lucide-trash-2" variant="ghost" color="error" size="xs" @click="remove(template)" />
          </div>
        </div>
      </div>

      <div v-else class="flex-1 flex flex-col items-center justify-center gap-3 text-muted py-16">
        <div class="size-16 rounded-2xl bg-elevated ring-1 ring-default flex items-center justify-center">
          <UIcon name="i-lucide-layout-template" class="size-8" />
        </div>
        <p class="text-sm">Nenhum template ainda.</p>
        <UButton v-if="isOwner" label="Novo template" icon="i-lucide-plus" variant="soft" @click="openCreate()" />
      </div>
    </template>
  </UDashboardPanel>

  <TemplateEditor
    v-if="companyId && accountId"
    v-model:open="editorOpen"
    :company-id="companyId"
    :account-id="accountId"
    :template-id="editingId"
    @saved="load()"
  />
</template>
