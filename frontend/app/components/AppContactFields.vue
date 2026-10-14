<script setup lang="ts">
// Campos personalizados do contato: um input por campo, salva ao sair do campo.
import type { Contact, ContactField } from '~/types/api'

const props = defineProps<{ contact: Contact, companyId: number }>()
const emit = defineEmits<{ updated: [contact: Contact] }>()

const api = useApi()
const toast = useToast()
const { fields, load } = useContactFields()
const { isOwner } = useCompany()
onMounted(() => {
  if (!fields.value.length) load()
})

const draft = ref<Record<string, string>>({})
// só reseta quando troca de contato ou os valores salvos mudam (SSE de outra aba), não a cada reload da lista
watch(() => [props.contact.id, JSON.stringify(props.contact.custom_fields)], () => {
  draft.value = { ...props.contact.custom_fields }
}, { immediate: true })

// dono cria campo novo aqui mesmo, sem ir em Configurações
const newLabel = ref('')
const creating = ref(false)
async function createField() {
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

async function save(key: string) {
  const value = (draft.value[key] ?? '').trim()
  if (value === (props.contact.custom_fields[key] ?? '')) return
  try {
    emit('updated', await api<Contact>(`/companies/${props.companyId}/contacts/${props.contact.id}`, {
      method: 'PATCH',
      body: { custom_fields: { [key]: value } }
    }))
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Não foi possível salvar o campo'), color: 'error' })
  }
}
</script>

<template>
  <div class="space-y-2">
    <UFormField v-for="field in fields" :key="field.id" :label="field.label" size="sm">
      <UInput
        v-model="draft[field.key]"
        size="sm"
        class="w-full"
        placeholder="Vazio"
        @blur="save(field.key)"
        @keydown.enter="($event.target as HTMLInputElement).blur()"
      />
    </UFormField>
    <p v-if="!fields.length && !isOwner" class="text-sm text-muted">
      Nenhum campo criado. Peça ao dono da empresa pra criar.
    </p>
    <form v-if="isOwner" class="flex gap-2" @submit.prevent="createField">
      <UInput
        v-model="newLabel"
        size="sm"
        maxlength="60"
        class="flex-1"
        :placeholder="fields.length ? 'Novo campo' : 'Criar campo, ex.: Plano'"
        aria-label="Nome do novo campo"
      />
      <UButton type="submit" icon="i-lucide-plus" size="sm" variant="soft" :loading="creating" :disabled="!newLabel.trim()" aria-label="Criar campo" />
    </form>
  </div>
</template>
