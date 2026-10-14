<script setup lang="ts">
// Lista, cria e exclui campos personalizados. Usado em Configurações e na página de Contatos.
import type { ContactField } from '~/types/api'

const props = defineProps<{ companyId: number }>()

const api = useApi()
const toast = useToast()
const confirmModal = useConfirm()
const { isOwner } = useCompany()
const { fields, load } = useContactFields()
watch(() => props.companyId, load, { immediate: true })

const newLabel = ref('')
const creating = ref(false)

async function create() {
  if (!newLabel.value.trim() || creating.value) return
  creating.value = true
  try {
    const field = await api<ContactField>(`/companies/${props.companyId}/contact-fields`, { method: 'POST', body: { label: newLabel.value } })
    fields.value = [...fields.value, field].sort((a, b) => a.label.localeCompare(b.label))
    newLabel.value = ''
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Não foi possível criar o campo'), color: 'error' })
  } finally {
    creating.value = false
  }
}

async function remove(field: ContactField) {
  const ok = await confirmModal({
    title: `Excluir o campo "${field.label}"?`,
    description: `Some dos contatos, e templates ou fluxos que usam {{${field.key}}} deixam de preencher.`,
    confirmLabel: 'Excluir'
  })
  if (!ok) return
  try {
    await api(`/companies/${props.companyId}/contact-fields/${field.id}`, { method: 'DELETE' })
    fields.value = fields.value.filter(f => f.id !== field.id)
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}
</script>

<template>
  <div class="space-y-3">
    <p class="text-sm text-muted">
      Dados extras do contato (plano, CPF, aniversário...). Viram variáveis em templates, campanhas e fluxos,
      entram como coluna na importação de CSV e a IA enxerga e preenche.
    </p>
    <form v-if="isOwner" class="flex gap-2" @submit.prevent="create">
      <UInput v-model="newLabel" placeholder="Novo campo, ex.: Plano" maxlength="60" class="flex-1" aria-label="Nome do novo campo" />
      <UButton type="submit" label="Criar" icon="i-lucide-plus" variant="soft" :loading="creating" :disabled="!newLabel.trim()" />
    </form>
    <p v-else class="text-xs text-muted">
      Só o dono da empresa cria e exclui campos.
    </p>
    <ul v-if="fields.length" class="divide-y divide-default rounded-md border border-default">
      <li v-for="field in fields" :key="field.id" class="flex items-center justify-between gap-2 px-3 py-2">
        <span class="text-sm">
          {{ field.label }}
          <code class="ml-2 text-xs text-muted" v-text="`{{${field.key}}}`" />
        </span>
        <UButton
          v-if="isOwner"
          icon="i-lucide-trash-2"
          variant="ghost"
          color="neutral"
          size="xs"
          :aria-label="`Excluir campo ${field.label}`"
          @click="remove(field)"
        />
      </li>
    </ul>
    <p v-else class="text-sm text-muted">
      Nenhum campo ainda.
    </p>
  </div>
</template>
