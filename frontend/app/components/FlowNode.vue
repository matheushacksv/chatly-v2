<script setup lang="ts">
// Card de nó do canvas. Um componente pra todos os tipos: o que muda é
// o metadado (ícone, rótulo, resumo) e a quantidade de saídas.
import { Handle, Position } from '@vue-flow/core'
import type { FlowNodeData, FlowNodeType } from '~/types/api'

const props = defineProps<{
  type: string
  data: FlowNodeData
  selected?: boolean
}>()

const nodeType = computed(() => props.type as FlowNodeType)
const meta = computed(() => FLOW_NODE_META[nodeType.value])
const summary = computed(() => flowNodeSummary(nodeType.value, props.data))
const outputs = computed(() => flowNodeOutputs(nodeType.value, props.data))

// os handles do vue-flow são absolutos por padrão; aqui eles entram no fluxo
// normal do card pra ficar um por linha de saída, estilo ManyChat
const inlineHandle = { position: 'relative', transform: 'none', top: 'auto', right: 'auto', left: 'auto' } as const
</script>

<template>
  <div
    class="rounded-xl bg-default shadow-sm w-56 text-left"
    :class="selected ? 'ring-2 ring-primary' : 'ring-1 ring-default'"
  >
    <Handle
      v-if="nodeType !== 'start'"
      type="target"
      :position="Position.Top"
    />

    <div class="flex items-center gap-2 px-3 py-2 border-b border-default">
      <UIcon
        :name="meta.icon"
        class="size-4 shrink-0"
        :class="meta.color"
      />
      <span class="text-xs font-semibold truncate">{{ meta.label }}</span>
    </div>

    <p class="px-3 py-2 text-xs text-muted break-words line-clamp-3">
      {{ summary }}
    </p>

    <div
      v-if="outputs.length"
      class="border-t border-default divide-y divide-default"
    >
      <div
        v-for="(output, index) in outputs"
        :key="output.id ?? index"
        class="flex items-center justify-end gap-2 px-3 py-1.5"
      >
        <span
          v-if="output.label"
          class="text-xs text-muted truncate"
        >{{ output.label }}</span>
        <Handle
          :id="output.id"
          type="source"
          :position="Position.Right"
          :style="inlineHandle"
        />
      </div>
    </div>
  </div>
</template>
