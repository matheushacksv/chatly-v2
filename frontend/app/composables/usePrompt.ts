import PromptModal from '~/components/PromptModal.vue'

/** Substituto do prompt() nativo: modal com input, resolve a string ou null se cancelar. */
export function usePrompt() {
  const overlay = useOverlay()

  return async (options: {
    title: string
    description?: string
    placeholder?: string
    confirmLabel?: string
    inputType?: string
  }) => {
    const modal = overlay.create(PromptModal, { destroyOnClose: true })
    const result = await modal.open(options).result
    return typeof result === 'string' ? result : null
  }
}
