<script setup lang="ts">
// O que a IA fez nesta conversa: retomada agendada (com cancelar) e as ferramentas que ela chamou.
import type { FollowUp, ToolCall } from '~/types/api'

const props = defineProps<{ contactId: number, companyId: number, refreshKey?: number }>()

const api = useApi()
const toast = useToast()
const toolCalls = ref<ToolCall[]>([])
const followups = ref<FollowUp[]>([])
const expanded = ref<number | null>(null)

async function load() {
  const data = await api<{ tool_calls: ToolCall[], followups: FollowUp[] }>(
    `/companies/${props.companyId}/contacts/${props.contactId}/ai-activity`
  )
  toolCalls.value = data.tool_calls
  followups.value = data.followups
}
// refreshKey muda a cada mensagem nova (SSE): a IA pode ter acabado de usar uma ferramenta
watch(() => [props.contactId, props.refreshKey], load, { immediate: true })

const pending = computed(() => followups.value.find(f => f.status === 'pending') ?? null)
const dateFmt = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })

const LABELS: Record<string, string> = {
  transferir_para_atendente: 'Transferiu pra equipe',
  agendar_retomada: 'Agendou retomada',
  salvar_dado_contato: 'Salvou dado',
  etiquetar_contato: 'Etiquetou',
  criar_negocio: 'Criou negócio no CRM',
  anotar_crm: 'Anotou no CRM'
}
// resumo de uma linha: motivo do handoff, campo salvo... o resto fica no "ver detalhes"
const summary = (call: ToolCall) => Object.values(call.arguments).map(v => String(v)).join(' · ')

async function cancel(followup: FollowUp) {
  try {
    await api(`/companies/${props.companyId}/contacts/${props.contactId}/followups/${followup.id}/cancel`, { method: 'POST' })
    await load()
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Não foi possível cancelar'), color: 'error' })
  }
}
</script>

<template>
  <div class="space-y-2">
    <UAlert
      v-if="pending"
      icon="i-lucide-calendar-clock"
      color="info"
      variant="subtle"
      :title="`Retomada em ${dateFmt.format(new Date(pending.send_at))}`"
      :description="pending.text"
      :actions="[{ label: 'Cancelar', color: 'neutral', variant: 'soft', size: 'xs', onClick: () => cancel(pending!) }]"
    />
    <ul v-if="toolCalls.length" class="space-y-1">
      <li v-for="call in toolCalls" :key="call.id" class="text-xs">
        <button type="button" class="w-full text-left flex items-start gap-1.5" @click="expanded = expanded === call.id ? null : call.id">
          <UIcon
            :name="call.ok ? 'i-lucide-circle-check' : 'i-lucide-circle-alert'"
            class="size-3.5 shrink-0 mt-0.5"
            :class="call.ok ? 'text-success' : 'text-error'"
          />
          <span class="min-w-0">
            <span class="font-medium">{{ LABELS[call.name] ?? call.name }}</span>
            <span class="text-muted"> · {{ dateFmt.format(new Date(call.created_at)) }}</span>
            <span v-if="summary(call)" class="block text-muted truncate">{{ summary(call) }}</span>
          </span>
        </button>
        <pre v-if="expanded === call.id" class="mt-1 bg-elevated rounded p-2 whitespace-pre-wrap break-words max-h-48 overflow-y-auto">{{ call.result }}</pre>
      </li>
    </ul>
    <p v-else-if="!pending" class="text-sm text-muted">
      Nenhuma ação ainda.
    </p>
  </div>
</template>
