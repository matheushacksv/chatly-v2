import type { TagColor } from '~/types/api'

export const TAG_COLOR_ITEMS: { label: string, value: TagColor }[] = [
  { label: 'Cinza', value: 'neutral' },
  { label: 'Azul', value: 'info' },
  { label: 'Verde', value: 'success' },
  { label: 'Amarelo', value: 'warning' },
  { label: 'Vermelho', value: 'error' },
  { label: 'Destaque', value: 'primary' }
]
