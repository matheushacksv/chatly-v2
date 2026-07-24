<script setup lang="ts">
// Modal de "Nova conversa" do canal pessoal, compartilhado pela página /meu-whatsapp
// e pelo widget flutuante. Lista o diretório (agenda do celular + contatos do canal
// oficial) e aceita número digitado que não é contato de lugar nenhum.
const open = defineModel<boolean>('open', { required: true })

const toast = useToast()
const { directory, loadDirectory, startConversation } = useEvolutionChat()

const search = ref('')
const loading = ref(false)
const busy = ref(false)

const digits = computed(() => search.value.replace(/\D/g, ''))

const filtered = computed(() => {
  const term = search.value.trim().toLowerCase()
  // ponytail: corta em 100 itens pro DOM não crescer sem limite (agenda real tem ~200)
  if (!term) return directory.value.slice(0, 100)
  const num = digits.value
  return directory.value
    .filter(entry => entry.name.toLowerCase().includes(term) || (!!num && entry.wa_id.includes(num)))
    .slice(0, 100)
})

// número digitado que não bate com ninguém do diretório: vira opção "usar este número"
const rawNumber = computed(() => {
  const num = digits.value
  return num.length >= 10 && !directory.value.some(entry => entry.wa_id === num) ? num : ''
})

watch(open, async (isOpen) => {
  search.value = ''
  if (!isOpen) return
  loading.value = true
  try {
    await loadDirectory()
  } catch {
    // diretório é conveniência: sem ele ainda dá pra digitar o número
  } finally {
    loading.value = false
  }
})

async function start(phone: string, name = '') {
  if (busy.value) return
  busy.value = true
  try {
    await startConversation(phone, name)
    open.value = false
  } catch (error) {
    const detail = apiErrorDetail(error, '')
    toast.add({
      title: detail.includes('not on whatsapp') ? 'Esse número não tem WhatsApp' : detail || 'Não foi possível abrir a conversa',
      color: 'error'
    })
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <UModal
    v-model:open="open"
    title="Nova conversa"
    description="Escolha um contato da sua agenda, do CRM, ou digite um número com DDI."
  >
    <template #body>
      <div class="space-y-3">
        <UInput
          v-model="search"
          icon="i-lucide-search"
          placeholder="Buscar nome ou número..."
          class="w-full"
          autocomplete="off"
          autofocus
        />

        <button
          v-if="rawNumber"
          class="w-full text-left px-3 py-2.5 rounded-lg flex items-center gap-3 bg-primary/5 hover:bg-primary/10 transition-colors"
          :disabled="busy"
          @click="start(rawNumber)"
        >
          <UIcon
            name="i-lucide-message-square-plus"
            class="size-4 text-primary shrink-0"
          />
          <span class="text-sm">
            Usar número <span class="font-mono font-medium">{{ rawNumber }}</span>
          </span>
        </button>

        <div class="h-72 overflow-y-auto -mx-2">
          <p
            v-if="loading"
            class="p-4 text-sm text-muted text-center"
          >
            Carregando contatos...
          </p>
          <p
            v-else-if="!filtered.length"
            class="p-4 text-sm text-muted text-center"
          >
            {{ search.trim() ? 'Nenhum contato encontrado.' : 'Nenhum contato disponível.' }}
          </p>
          <button
            v-for="entry in filtered"
            :key="entry.wa_id"
            class="w-full text-left px-3 py-2 flex items-center gap-3 rounded-lg hover:bg-elevated/60 transition-colors"
            :disabled="busy"
            @click="start(entry.wa_id, entry.name)"
          >
            <UAvatar
              :alt="entry.name || 'Sem nome'"
              size="sm"
            />
            <div class="flex-1 min-w-0">
              <span class="font-medium text-sm truncate block">{{ entry.name || 'Sem nome' }}</span>
              <span class="text-[11px] text-muted font-mono truncate block">{{ entry.wa_id }}</span>
            </div>
            <UBadge
              :color="entry.source === 'agenda' ? 'neutral' : 'primary'"
              variant="subtle"
              size="sm"
            >
              {{ entry.source }}
            </UBadge>
          </button>
        </div>
      </div>
    </template>
  </UModal>
</template>
