<script setup lang="ts">
// Cria, edita e exclui respostas rápidas (/atalho no chat).
import type { QuickReply } from '~/types/api'

const props = defineProps<{ companyId: number }>()

const api = useApi()
const toast = useToast()
const confirmModal = useConfirm()
const { replies, load } = useQuickReplies()
watch(() => props.companyId, load, { immediate: true })

const form = reactive({ id: null as number | null, shortcut: '', text: '' })
const saving = ref(false)

function edit(reply: QuickReply | null) {
  Object.assign(form, reply ? { ...reply } : { id: null, shortcut: '', text: '' })
}

function insertVar(token: string) {
  form.text = form.text && !/\s$/.test(form.text) ? `${form.text} ${token}` : form.text + token
}

async function save() {
  if (!form.shortcut.trim() || !form.text.trim() || saving.value) return
  saving.value = true
  try {
    const base = `/companies/${props.companyId}/quick-replies`
    const body = { shortcut: form.shortcut, text: form.text }
    const saved = form.id
      ? await api<QuickReply>(`${base}/${form.id}`, { method: 'PUT', body })
      : await api<QuickReply>(base, { method: 'POST', body })
    replies.value = [...replies.value.filter(r => r.id !== saved.id), saved].sort((a, b) => a.shortcut.localeCompare(b.shortcut))
    edit(null)
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Não foi possível salvar'), color: 'error' })
  } finally {
    saving.value = false
  }
}

async function remove(reply: QuickReply) {
  if (!await confirmModal({ title: `Excluir /${reply.shortcut}?`, confirmLabel: 'Excluir' })) return
  try {
    await api(`/companies/${props.companyId}/quick-replies/${reply.id}`, { method: 'DELETE' })
    replies.value = replies.value.filter(r => r.id !== reply.id)
    if (form.id === reply.id) edit(null)
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}
</script>

<template>
  <div class="space-y-4">
    <p class="text-sm text-muted">
      No chat, digite <span class="font-mono">/</span> e o atalho pra colocar o texto na mensagem, já com os dados do contato. Você revisa antes de enviar.
    </p>

    <form class="space-y-3 rounded-lg ring-1 ring-default p-3" @submit.prevent="save">
      <UFormField label="Atalho" required help="Sem espaço, ex.: boasvindas, pix, horario">
        <UInput v-model="form.shortcut" class="w-full" placeholder="boasvindas">
          <template #leading>
            <span class="text-muted font-mono">/</span>
          </template>
        </UInput>
      </UFormField>
      <UFormField label="Texto" required>
        <template #hint>
          <VarInsertMenu @insert="insertVar" />
        </template>
        <UTextarea v-model="form.text" :rows="4" autoresize class="w-full" placeholder="Olá {{nome}}, tudo bem? Aqui é do atendimento..." />
      </UFormField>
      <div class="flex justify-end gap-2">
        <UButton v-if="form.id" type="button" label="Cancelar" variant="ghost" color="neutral" @click="edit(null)" />
        <UButton
          type="submit"
          :label="form.id ? 'Salvar' : 'Criar'"
          :icon="form.id ? 'i-lucide-check' : 'i-lucide-plus'"
          :loading="saving"
          :disabled="!form.shortcut.trim() || !form.text.trim()"
        />
      </div>
    </form>

    <ul v-if="replies.length" class="divide-y divide-default rounded-md border border-default">
      <li v-for="reply in replies" :key="reply.id" class="flex items-start gap-2 px-3 py-2">
        <button type="button" class="min-w-0 flex-1 text-left" @click="edit(reply)">
          <p class="text-sm font-mono">
            /{{ reply.shortcut }}
          </p>
          <p class="text-xs text-muted line-clamp-2 whitespace-pre-wrap">
            {{ reply.text }}
          </p>
        </button>
        <UButton icon="i-lucide-trash-2" variant="ghost" color="neutral" size="xs" :aria-label="`Excluir /${reply.shortcut}`" @click="remove(reply)" />
      </li>
    </ul>
    <p v-else class="text-sm text-muted">
      Nenhuma resposta rápida ainda.
    </p>
  </div>
</template>
