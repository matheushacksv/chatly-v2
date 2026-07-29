<script setup lang="ts">
// Trilha de nós que uma execução percorreu. É o que responde
// "por que esse lead parou no meio do fluxo".
import type { FlowNodeType, FlowRun } from '~/types/api'

defineProps<{ run: FlowRun }>()

const timeFmt = new Intl.DateTimeFormat('pt-BR', {
  day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
})

const meta = (type: string) => FLOW_NODE_META[type as FlowNodeType]
</script>

<template>
  <div class="space-y-3">
    <p
      v-if="!run.steps.length"
      class="text-sm text-muted"
    >
      Essa execução ainda não passou por nenhum bloco.
    </p>

    <ol
      v-else
      class="space-y-2"
    >
      <li
        v-for="(step, index) in run.steps"
        :key="`${step.node}-${index}`"
        class="flex items-start gap-3"
      >
        <div class="size-7 shrink-0 rounded-lg bg-elevated ring-1 ring-default flex items-center justify-center">
          <UIcon
            :name="meta(step.type)?.icon ?? 'i-lucide-circle'"
            class="size-4"
            :class="meta(step.type)?.color"
          />
        </div>
        <div class="min-w-0 flex-1">
          <p class="text-sm">
            {{ meta(step.type)?.label ?? step.type }}
          </p>
          <p class="text-xs text-muted truncate">
            {{ timeFmt.format(new Date(step.at)) }}
            <span v-if="step.result"> · {{ step.result }}</span>
          </p>
        </div>
      </li>
    </ol>

    <p
      v-if="run.status === 'waiting'"
      class="text-xs text-warning"
    >
      Parado esperando no bloco <span class="font-mono">{{ run.node_id }}</span>.
    </p>
  </div>
</template>
