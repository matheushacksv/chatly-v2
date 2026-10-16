<script setup lang="ts">
// UFormField com a ajuda num (i) ao lado do label (`tip`) em vez do texto fixo embaixo
defineOptions({ inheritAttrs: false })
defineProps<{ label?: string, tip?: string }>()
</script>

<template>
  <UFormField v-bind="$attrs" :label="label">
    <template v-if="tip" #label>
      <span class="inline-flex items-center gap-1">{{ label }}<AppTip :text="tip" /></span>
    </template>
    <template v-for="(_, name) in $slots" #[name]="scope">
      <slot :name="name" v-bind="scope ?? {}" />
    </template>
  </UFormField>
</template>
