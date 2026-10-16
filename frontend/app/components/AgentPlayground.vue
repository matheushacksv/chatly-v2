<script setup lang="ts">
// Chat de teste do agente: contato fictício, ferramentas simuladas, usa o prompt do formulário mesmo sem salvar.
import type { NativeTool } from '~/types/api'

const props = defineProps<{ companyId: number, agentId: number, prompt: string, nativeTools: NativeTool[] }>()

type Turn = { role: 'user', content: string } | { role: 'assistant', parts: string[], toolCalls: { name: string, arguments: Record<string, unknown> }[] }

const api = useApi()
const toast = useToast()
const { fields: contactFields, load } = useContactFields()
onMounted(load)

const contact = reactive({ name: 'Cliente de teste', phone: '5511999999999', memory: '', fields: {} as Record<string, string> })
const turns = ref<Turn[]>([])
const draft = ref('')
const sending = ref(false)
const showContact = ref(false)
const scroller = useTemplateRef<HTMLElement>('scroller')

const TOOL_LABELS: Record<string, string> = {
  transferir_para_atendente: 'Transferiria pra equipe',
  agendar_retomada: 'Agendaria retomada',
  salvar_dado_contato: 'Salvou dado (só no teste)',
  etiquetar_contato: 'Etiquetaria',
  criar_negocio: 'Criaria negócio no CRM',
  anotar_crm: 'Anotaria no CRM'
}

async function send() {
  const text = draft.value.trim()
  if (!text || sending.value) return
  turns.value.push({ role: 'user', content: text })
  draft.value = ''
  sending.value = true
  scrollDown()
  try {
    const result = await api<{ parts: string[], tool_calls: { name: string, arguments: Record<string, unknown> }[], fields: Record<string, string> }>(
      `/companies/${props.companyId}/agents/${props.agentId}/playground`,
      {
        method: 'POST',
        body: {
          messages: turns.value.map(t => t.role === 'user' ? t : { role: 'assistant', content: t.parts.join('\n\n') }),
          name: contact.name,
          phone: contact.phone,
          fields: contact.fields,
          memory: contact.memory,
          prompt: props.prompt,
          native_tools: props.nativeTools
        }
      }
    )
    turns.value.push({ role: 'assistant', parts: result.parts, toolCalls: result.tool_calls })
    // "salvar dado" da IA aparece no contato fictício, como apareceria no real
    contact.fields = { ...result.fields }
  } catch (error) {
    turns.value.pop()
    draft.value = text
    toast.add({ title: apiErrorDetail(error, 'A IA não respondeu'), color: 'error' })
  } finally {
    sending.value = false
    scrollDown()
  }
}

function scrollDown() {
  nextTick(() => scroller.value?.scrollTo({ top: scroller.value.scrollHeight, behavior: 'smooth' }))
}

function reset() {
  turns.value = []
  contact.fields = {}
}

const summary = (args: Record<string, unknown>) => Object.values(args).map(v => String(v)).join(' · ')
</script>

<template>
  <div class="space-y-3">
    <div class="flex items-center justify-between gap-2">
      <p class="text-sm text-muted flex items-center gap-1.5">
        Converse como se fosse o cliente
        <AppTip text="Usa o prompt e as ferramentas do formulário, mesmo sem salvar. Nada é enviado nem executado de verdade" />
      </p>
      <div class="flex gap-1 shrink-0">
        <UButton
          type="button"
          :label="showContact ? 'Ocultar contato' : 'Contato fictício'"
          icon="i-lucide-user-cog"
          variant="ghost"
          size="sm"
          @click="() => { showContact = !showContact }"
        />
        <UButton type="button" label="Reiniciar" icon="i-lucide-rotate-ccw" variant="ghost" color="neutral" size="sm" :disabled="!turns.length" @click="reset" />
      </div>
    </div>

    <div v-if="showContact" class="rounded-lg ring-1 ring-default p-3 grid gap-3 sm:grid-cols-2">
      <UFormField label="Nome" size="sm">
        <UInput v-model="contact.name" class="w-full" size="sm" />
      </UFormField>
      <UFormField label="Telefone" size="sm">
        <UInput v-model="contact.phone" class="w-full" size="sm" />
      </UFormField>
      <UFormField v-for="field in contactFields" :key="field.id" :label="field.label" size="sm">
        <UInput v-model="contact.fields[field.key]" class="w-full" size="sm" placeholder="Vazio" />
      </UFormField>
      <UFormField label="Memória da IA" size="sm" class="sm:col-span-2" help="O que a IA já teria anotado sobre o cliente">
        <UTextarea v-model="contact.memory" :rows="2" class="w-full" size="sm" />
      </UFormField>
    </div>

    <div ref="scroller" class="h-96 overflow-y-auto rounded-lg ring-1 ring-default bg-elevated/30 p-3 space-y-2">
      <p v-if="!turns.length" class="text-sm text-muted text-center pt-32">
        Mande a primeira mensagem como o cliente mandaria.
      </p>
      <template v-for="(turn, i) in turns" :key="i">
        <div v-if="turn.role === 'user'" class="flex justify-start">
          <p class="max-w-[80%] rounded-2xl rounded-bl-sm bg-elevated ring-1 ring-default px-3 py-2 text-sm whitespace-pre-wrap break-words">
            {{ turn.content }}
          </p>
        </div>
        <template v-else>
          <div v-for="(part, j) in turn.parts" :key="`${i}-${j}`" class="flex justify-end">
            <p class="max-w-[80%] rounded-2xl rounded-br-sm bg-primary/10 ring-1 ring-primary/20 px-3 py-2 text-sm whitespace-pre-wrap break-words">
              {{ part }}
            </p>
          </div>
          <div v-for="(call, j) in turn.toolCalls" :key="`${i}-t${j}`" class="flex justify-end">
            <p class="max-w-[80%] text-xs text-muted flex items-start gap-1.5">
              <UIcon name="i-lucide-wrench" class="size-3.5 shrink-0 mt-0.5" />
              <span>
                <span class="font-medium">{{ TOOL_LABELS[call.name] ?? `Chamaria ${call.name}` }}</span>
                <span v-if="summary(call.arguments)"> · {{ summary(call.arguments) }}</span>
              </span>
            </p>
          </div>
        </template>
      </template>
      <div v-if="sending" class="flex justify-end">
        <UIcon name="i-lucide-loader-circle" class="size-4 animate-spin text-muted" />
      </div>
    </div>

    <!-- sem <form>: o playground fica dentro do formulário do agente, e Enter não pode salvar o agente -->
    <div class="flex gap-2">
      <UInput
        v-model="draft"
        class="flex-1"
        placeholder="Escreva como o cliente..."
        :disabled="sending"
        autofocus
        @keydown.enter.prevent="send"
      />
      <UButton type="button" icon="i-lucide-send" :loading="sending" :disabled="!draft.trim()" aria-label="Enviar" @click="send" />
    </div>
  </div>
</template>
