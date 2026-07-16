<script setup lang="ts">
defineProps<{
  title: string
  description?: string
  placeholder?: string
  confirmLabel?: string
  inputType?: string
}>()

const emit = defineEmits<{ close: [string | null] }>()
const value = ref('')

function submit() {
  if (value.value.trim()) emit('close', value.value.trim())
}
</script>

<template>
  <UModal
    :title="title"
    :description="description"
    :ui="{ footer: 'justify-end' }"
    @update:open="v => !v && emit('close', null)"
  >
    <template #body>
      <form @submit.prevent="submit">
        <UInput
          v-model="value"
          :type="inputType ?? 'text'"
          :placeholder="placeholder"
          class="w-full font-mono"
          autocomplete="off"
          autofocus
        />
      </form>
    </template>
    <template #footer>
      <UButton label="Cancelar" color="neutral" variant="ghost" @click="emit('close', null)" />
      <UButton :label="confirmLabel ?? 'Salvar'" :disabled="!value.trim()" @click="submit()" />
    </template>
  </UModal>
</template>
