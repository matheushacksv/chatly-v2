<script setup lang="ts">
defineProps<{
  title: string
  description?: string
  confirmLabel?: string
}>()

const emit = defineEmits<{ close: [boolean] }>()
</script>

<template>
  <UModal
    :title="title"
    :description="description"
    :ui="{ footer: 'justify-end' }"
    @update:open="value => !value && emit('close', false)"
  >
    <template #footer>
      <UButton label="Cancelar" color="neutral" variant="ghost" @click="emit('close', false)" />
      <UButton :label="confirmLabel ?? 'Excluir'" color="error" icon="i-lucide-trash-2" @click="emit('close', true)" />
    </template>
  </UModal>
</template>
