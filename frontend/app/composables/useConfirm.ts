import ConfirmModal from '~/components/ConfirmModal.vue'

/** Substituto do confirm() nativo: modal Nuxt UI, resolve true só no botão de confirmar. */
export function useConfirm() {
  const overlay = useOverlay()

  return async (options: { title: string, description?: string, confirmLabel?: string }) => {
    const modal = overlay.create(ConfirmModal, { destroyOnClose: true })
    return (await modal.open(options).result) === true
  }
}
