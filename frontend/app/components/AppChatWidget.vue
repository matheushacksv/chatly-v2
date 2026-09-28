<script setup lang="ts">
// Bolha de chat flutuante (estilo Messenger/LinkedIn), disponível em qualquer página.
// Compartilha estado com a página /meu-whatsapp via usePersonalChat.
const toast = useToast()
const route = useRoute()
const { activeId: companyId } = useCompany()
const { connection, connected, contacts, selected, selectedId, messages, stickers, widgetOpen: open, send, sendMedia, loadStickers, saveSticker, sendSticker, deleteSticker } = usePersonalChat()

// /meu-whatsapp já é o chat inteiro, bolha lá é redundante
const hidden = computed(() => route.path === '/meu-whatsapp')

const { bubbleStyle, panelStyle, handleClass, handleIcon, dragged, startDrag, startResize } = useWidgetPosition()

function toggle() {
  if (dragged.value) {
    dragged.value = false // soltou depois de arrastar: não abre/fecha
    return
  }
  open.value = !open.value
}
const newChatOpen = ref(false)
const draft = ref('')
const sending = ref(false)

// anexo: arquivo vira chip de preview, campo de texto vira legenda (mesmo padrão de conversas.vue)
const attachment = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const attachmentPreview = ref<string | null>(null)
watch(attachment, (file, _, onCleanup) => {
  attachmentPreview.value = file?.type.startsWith('image/') ? URL.createObjectURL(file) : null
  const url = attachmentPreview.value
  if (url) onCleanup(() => URL.revokeObjectURL(url))
})
function onFilePicked(event: Event) {
  const input = event.target as HTMLInputElement
  attachment.value = input.files?.[0] ?? null
  input.value = ''
}
const formatSize = (bytes: number) =>
  bytes < 2 ** 20 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 2 ** 20).toFixed(1)} MB`
const isPlaceholder = (content: string) => /^\[.+\]$/.test(content)

// figurinhas
const stickerPickerOpen = ref(false)
watch(stickerPickerOpen, open => open && loadStickers())
async function onSaveSticker(id: number) {
  try {
    await saveSticker(id)
    toast.add({ title: 'Figurinha salva', icon: 'i-lucide-sticker' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao salvar figurinha'), color: 'error' })
  }
}
async function onSendSticker(id: number) {
  if (sending.value) return
  sending.value = true
  stickerPickerOpen.value = false
  try {
    await sendSticker(id)
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao enviar figurinha'), color: 'error' })
  } finally {
    sending.value = false
  }
}
async function onDeleteSticker(id: number) {
  try {
    await deleteSticker(id)
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

// gravação de áudio
const { recording, seconds, canRecord, start: startRec, stop: stopRec, cancel: cancelRec } = useAudioRecorder()
const recLabel = computed(() => `${Math.floor(seconds.value / 60)}:${String(seconds.value % 60).padStart(2, '0')}`)
async function startRecording() {
  try {
    await startRec()
  } catch {
    toast.add({ title: 'Não foi possível acessar o microfone', color: 'error' })
  }
}
async function stopAndSend() {
  if (sending.value) return
  sending.value = true
  try {
    const file = await stopRec()
    if (!file) {
      toast.add({ title: 'Áudio muito curto', description: 'Grave pelo menos 1 segundo.', color: 'warning' })
      return
    }
    await sendMedia(file, '')
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao enviar áudio'), color: 'error' })
  } finally {
    sending.value = false
  }
}

const chatBody = ref<HTMLElement | null>(null)
function scrollBottom() {
  nextTick(() => {
    const el = chatBody.value
    if (el) el.scrollTop = el.scrollHeight
  })
}
watch(messages, scrollBottom)
watch(open, v => v && scrollBottom())

async function submit() {
  const content = draft.value.trim()
  if ((!content && !attachment.value) || sending.value) return
  sending.value = true
  try {
    if (attachment.value) {
      await sendMedia(attachment.value, content)
      attachment.value = null
    } else {
      await send(content)
    }
    draft.value = ''
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao enviar'), color: 'error' })
  } finally {
    sending.value = false
  }
}

function goConnect() {
  open.value = false
  navigateTo('/meu-whatsapp')
}

const time = (iso: string) => new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
</script>

<template>
  <div v-if="!hidden">
    <!-- painel -->
    <div
      v-if="open"
      class="fixed z-50 flex flex-col rounded-2xl bg-default ring-1 ring-default shadow-xl overflow-hidden"
      :style="panelStyle"
    >
      <!-- header (também é a pegada de arrasto do painel aberto) -->
      <div
        class="flex items-center gap-2 px-3 py-2.5 border-b border-default bg-elevated/50 cursor-grab active:cursor-grabbing select-none touch-none"
        @pointerdown="startDrag"
      >
        <UButton
          v-if="selected"
          icon="i-lucide-arrow-left"
          color="neutral"
          variant="ghost"
          size="xs"
          aria-label="Voltar"
          @click="selectedId = null"
        />
        <template v-if="selected">
          <UAvatar
            :src="selected.avatar_url || undefined"
            :alt="selected.name || selected.wa_id"
            size="xs"
          />
          <div class="min-w-0 leading-tight flex-1">
            <p class="font-medium text-sm truncate">
              {{ selected.name || selected.wa_id }}
            </p>
            <p class="text-[11px] text-muted font-mono truncate">
              {{ selected.wa_id }}
            </p>
          </div>
        </template>
        <template v-else>
          <UIcon
            name="i-lucide-message-circle"
            class="size-4 text-primary"
          />
          <span class="font-display font-semibold text-sm flex-1">Meu WhatsApp</span>
        </template>
        <UButton
          v-if="connected && !selected"
          icon="i-lucide-message-square-plus"
          color="neutral"
          variant="ghost"
          size="xs"
          aria-label="Nova conversa"
          @click="newChatOpen = true"
        />
        <UButton
          icon="i-lucide-x"
          color="neutral"
          variant="ghost"
          size="xs"
          aria-label="Fechar"
          @click="open = false"
        />
      </div>

      <!-- não conectado -->
      <div
        v-if="!connected"
        class="flex-1 flex flex-col items-center justify-center gap-3 text-center p-6"
      >
        <UIcon
          name="i-lucide-smartphone"
          class="size-8 text-muted"
        />
        <p class="text-sm text-muted">
          {{ connection?.status === 'connecting' ? 'Conexão em andamento...' : 'Conecte seu WhatsApp pra conversar por aqui.' }}
        </p>
        <UButton
          label="Ir para conexão"
          icon="i-lucide-qr-code"
          size="sm"
          @click="goConnect"
        />
      </div>

      <!-- conectado, sem conversa selecionada: lista -->
      <div
        v-else-if="!selected"
        class="flex-1 overflow-y-auto"
      >
        <p
          v-if="!contacts.length"
          class="p-6 text-sm text-muted text-center"
        >
          Nenhuma conversa ainda. Comece uma no botão "+" acima, ou espere alguém te mandar mensagem.
        </p>
        <button
          v-for="contact in contacts"
          :key="contact.id"
          class="w-full text-left px-3 py-2.5 flex items-center gap-3 hover:bg-elevated/60 transition-colors"
          @click="selectedId = contact.id"
        >
          <UAvatar
            :src="contact.avatar_url || undefined"
            :alt="contact.name || contact.wa_id"
            size="sm"
          />
          <div class="flex-1 min-w-0">
            <span class="font-medium text-sm truncate block">{{ contact.name || contact.wa_id }}</span>
            <span class="text-[11px] text-muted font-mono truncate block">{{ contact.wa_id }}</span>
          </div>
        </button>
      </div>

      <!-- conectado, conversa aberta: thread -->
      <template v-else>
        <div
          ref="chatBody"
          class="flex-1 overflow-y-auto space-y-1.5 p-3"
        >
          <div
            v-for="message in messages"
            :key="message.id"
            class="flex"
            :class="message.direction === 'out' ? 'justify-end' : 'justify-start'"
          >
            <UAvatar
              v-if="message.direction === 'in'"
              :src="selected.avatar_url || undefined"
              :alt="selected.name || selected.wa_id"
              size="3xs"
              class="self-end mr-1.5 shrink-0"
            />
            <div
              class="max-w-[80%] rounded-2xl px-3 py-2 text-sm shadow-sm"
              :class="message.direction === 'out'
                ? 'bg-primary text-inverted rounded-br-sm'
                : 'bg-elevated rounded-bl-sm'"
            >
              <ChatMedia
                v-if="message.message_type !== 'text'"
                :message="message"
                :company-id="companyId ?? 0"
                :proxy="false"
              />
              <UButton
                v-if="message.message_type === 'sticker' && message.direction === 'in'"
                label="Salvar"
                icon="i-lucide-bookmark-plus"
                variant="ghost"
                color="neutral"
                size="xs"
                @click="onSaveSticker(message.id)"
              />
              <p
                v-if="message.message_type === 'text' || !isPlaceholder(message.content)"
                class="whitespace-pre-wrap break-words"
                :class="message.message_type !== 'text' ? 'mt-1' : ''"
              >
                {{ message.content }}
              </p>
              <span class="block text-[10px] opacity-60 mt-0.5 text-right">{{ time(message.created_at) }}</span>
            </div>
          </div>
        </div>
        <div
          v-if="attachment"
          class="flex items-center gap-2 px-2 pt-2"
        >
          <img
            v-if="attachmentPreview"
            :src="attachmentPreview"
            class="size-8 rounded object-cover ring-1 ring-default"
          >
          <UIcon
            v-else
            name="i-lucide-file"
            class="size-4 text-muted shrink-0"
          />
          <div class="min-w-0 text-xs flex-1">
            <p class="truncate font-medium">
              {{ attachment.name }}
            </p>
            <p class="text-muted">
              {{ formatSize(attachment.size) }}
            </p>
          </div>
          <UButton
            icon="i-lucide-x"
            size="xs"
            variant="ghost"
            color="neutral"
            aria-label="Remover anexo"
            @click="attachment = null"
          />
        </div>
        <form
          class="flex items-center gap-2 p-2 border-t border-default"
          @submit.prevent="submit"
        >
          <template v-if="recording">
            <UButton
              icon="i-lucide-trash-2"
              color="error"
              variant="ghost"
              size="sm"
              class="rounded-full shrink-0"
              aria-label="Cancelar gravação"
              @click="cancelRec"
            />
            <div class="flex-1 flex items-center gap-2 text-sm">
              <span class="size-2.5 rounded-full bg-error animate-pulse" />
              <span class="font-mono tabular-nums">{{ recLabel }}</span>
            </div>
            <UButton
              icon="i-lucide-send"
              size="sm"
              class="rounded-full shrink-0"
              :loading="sending"
              aria-label="Enviar áudio"
              @click="stopAndSend"
            />
          </template>
          <template v-else>
            <input
              ref="fileInput"
              type="file"
              class="hidden"
              @change="onFilePicked"
            >
            <UButton
              icon="i-lucide-paperclip"
              variant="ghost"
              color="neutral"
              size="sm"
              class="rounded-full shrink-0"
              aria-label="Anexar arquivo"
              @click="fileInput?.click()"
            />
            <UPopover v-model:open="stickerPickerOpen">
              <UButton
                icon="i-lucide-sticker"
                variant="ghost"
                color="neutral"
                size="sm"
                class="rounded-full shrink-0"
                aria-label="Figurinhas"
              />
              <template #content>
                <div class="p-3 w-64">
                  <p
                    v-if="!stickers.length"
                    class="text-sm text-muted"
                  >
                    Nenhuma figurinha salva. Passe o mouse numa figurinha recebida e clique em "Salvar".
                  </p>
                  <div
                    v-else
                    class="grid grid-cols-4 gap-2"
                  >
                    <div
                      v-for="sticker in stickers"
                      :key="sticker.id"
                      class="relative group"
                    >
                      <button
                        type="button"
                        class="cursor-pointer"
                        @click="onSendSticker(sticker.id)"
                      >
                        <img
                          :src="sticker.url"
                          class="size-12 object-contain"
                          alt="Figurinha"
                        >
                      </button>
                      <UButton
                        icon="i-lucide-x"
                        size="xs"
                        color="error"
                        variant="solid"
                        class="absolute -top-1 -right-1 rounded-full opacity-0 group-hover:opacity-100 transition-opacity"
                        aria-label="Excluir figurinha"
                        @click="onDeleteSticker(sticker.id)"
                      />
                    </div>
                  </div>
                </div>
              </template>
            </UPopover>
            <UButton
              v-if="canRecord && !attachment && !draft.trim()"
              icon="i-lucide-mic"
              variant="ghost"
              color="neutral"
              size="sm"
              class="rounded-full shrink-0"
              aria-label="Gravar áudio"
              @click="startRecording"
            />
            <UInput
              v-model="draft"
              :placeholder="attachment ? 'Legenda (opcional)...' : 'Mensagem...'"
              class="flex-1"
              :ui="{ base: 'rounded-full' }"
              autocomplete="off"
            />
            <UButton
              type="submit"
              icon="i-lucide-send"
              :loading="sending"
              :disabled="!draft.trim() && !attachment"
            />
          </template>
        </form>
      </template>

      <!-- handle de redimensionamento, no canto oposto às bordas ancoradas -->
      <div
        class="hidden sm:flex absolute size-4 items-center justify-center text-dimmed hover:text-muted touch-none"
        :class="handleClass"
        @pointerdown="startResize"
      >
        <UIcon
          :name="handleIcon"
          class="size-3"
        />
      </div>
    </div>

    <!-- bolha -->
    <UButton
      :icon="open ? 'i-lucide-chevron-down' : 'i-lucide-message-circle'"
      color="primary"
      size="xl"
      class="fixed z-50 rounded-full shadow-lg size-14 justify-center touch-none"
      :style="bubbleStyle"
      :aria-label="open ? 'Fechar chat' : 'Abrir chat'"
      @pointerdown="startDrag"
      @click="toggle"
    >
      <span
        v-if="connected && !open"
        class="absolute top-1 right-1 size-2.5 rounded-full bg-success ring-2 ring-primary"
      />
    </UButton>

    <AppPersonalNewChat v-model:open="newChatOpen" />
  </div>
</template>
