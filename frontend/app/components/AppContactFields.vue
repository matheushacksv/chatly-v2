<script setup lang="ts">
// Campos personalizados do contato: um input por campo, salva ao sair do campo.
import type { Contact } from '~/types/api'

const props = defineProps<{ contact: Contact, companyId: number }>()
const emit = defineEmits<{ updated: [contact: Contact] }>()

const api = useApi()
const toast = useToast()
const { fields, load } = useContactFields()
onMounted(() => {
  if (!fields.value.length) load()
})

const draft = ref<Record<string, string>>({})
// só reseta quando troca de contato ou os valores salvos mudam (SSE de outra aba), não a cada reload da lista
watch(() => [props.contact.id, JSON.stringify(props.contact.custom_fields)], () => {
  draft.value = { ...props.contact.custom_fields }
}, { immediate: true })

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
  <div v-if="fields.length" class="space-y-2">
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
  </div>
  <p v-else class="text-sm text-muted">
    Nenhum campo criado. Crie em Configurações → Contatos.
  </p>
</template>
