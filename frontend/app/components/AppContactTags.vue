<script setup lang="ts">
// Etiquetas do contato: chips com x pra remover + menu pra adicionar ou criar.
import type { Contact, Tag } from '~/types/api'

const props = defineProps<{ contact: Contact, companyId: number, size?: 'xs' | 'sm' }>()
const emit = defineEmits<{ updated: [contact: Contact] }>()

const api = useApi()
const toast = useToast()
const allTags = ref<Tag[]>([])
const busy = ref(false)

const available = computed(() => {
  const mine = new Set(props.contact.tags.map(t => t.id))
  return allTags.value.filter(t => !mine.has(t.id)).map(t => ({ label: t.name, value: t.id }))
})

async function loadTags(open: boolean) {
  if (open) allTags.value = await api<Tag[]>(`/companies/${props.companyId}/tags`)
}

async function call(method: 'POST' | 'DELETE', tagId: number) {
  busy.value = true
  try {
    emit('updated', await api<Contact>(`/companies/${props.companyId}/contacts/${props.contact.id}/tags/${tagId}`, { method }))
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Não foi possível alterar a etiqueta'), color: 'error' })
  } finally {
    busy.value = false
  }
}

async function create(name: string) {
  try {
    const tag = await api<Tag>(`/companies/${props.companyId}/tags`, { method: 'POST', body: { name } })
    await call('POST', tag.id)
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Não foi possível criar a etiqueta'), color: 'error' })
  }
}
</script>

<template>
  <div class="flex flex-wrap items-center gap-1">
    <UBadge
      v-for="tag in contact.tags"
      :key="tag.id"
      :color="tag.color"
      variant="subtle"
      :size="size ?? 'sm'"
      class="gap-0.5"
    >
      {{ tag.name }}
      <UButton
        icon="i-lucide-x"
        variant="link"
        :color="tag.color"
        size="xs"
        class="p-0"
        :aria-label="`Remover etiqueta ${tag.name}`"
        :disabled="busy"
        @click.stop="call('DELETE', tag.id)"
      />
    </UBadge>
    <USelectMenu
      :model-value="undefined"
      :items="available"
      value-key="value"
      create-item
      icon="i-lucide-tag"
      :placeholder="contact.tags.length ? '' : 'Etiqueta'"
      variant="ghost"
      size="xs"
      :loading="busy"
      :search-input="{ placeholder: 'Buscar ou criar...' }"
      :content="{ align: 'start' }"
      class="w-auto"
      aria-label="Adicionar etiqueta"
      @update:open="loadTags"
      @update:model-value="(id: number) => id && call('POST', id)"
      @create="create"
    />
  </div>
</template>
