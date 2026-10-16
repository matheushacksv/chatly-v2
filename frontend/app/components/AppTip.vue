<script setup lang="ts">
// (i) com a explicação no hover, no lugar de texto fixo na tela
defineProps<{ text: string }>()
</script>

<template>
  <UTooltip :text="text" :ui="{ content: 'h-auto max-w-xs py-1.5', text: 'whitespace-normal' }">
    <UIcon
      name="i-lucide-info"
      class="size-3.5 shrink-0 text-dimmed hover:text-muted cursor-help align-middle"
      tabindex="0"
      :aria-label="text"
    />
  </UTooltip>
</template>
