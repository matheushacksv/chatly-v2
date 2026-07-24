// Posição e tamanho da bolha de chat flutuante, persistidos no navegador.
// A bolha gruda na borda esquerda ou direita (eixo X); a altura (eixo Y) é livre.

const KEY = 'chatly_widget'
const BUBBLE = 56 // size-14
const MARGIN = 16 // a margem de tela original (right-4 / bottom-4)
const GAP = 12 // respiro entre bolha e painel (era o gap-3)
const MIN_W = 300
const MIN_H = 280

interface Saved {
  side?: 'left' | 'right'
  y?: number | null
  w?: number
  h?: number
}

const clamp = (v: number, min: number, max: number) => Math.min(Math.max(v, min), max)

function load(): Saved {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '{}')
  } catch {
    return {}
  }
}

/** Posição da bolha, geometria do painel e handlers de arrasto/redimensionamento. */
export function useWidgetPosition() {
  const route = useRoute()
  const saved = load()

  const side = useState<'left' | 'right'>('widget:side', () => saved.side ?? 'right')
  // null = nunca arrastada, cai no padrão da rota (que ainda sobe em /conversas)
  const y = useState<number | null>('widget:y', () => saved.y ?? null)
  const w = useState('widget:w', () => saved.w ?? 384) // era sm:w-96
  const h = useState('widget:h', () => saved.h ?? 512) // era h-[32rem]

  // viewport reativa: todo clamp deriva daqui, então redimensionar a janela reposiciona sozinho
  const vw = useState('widget:vw', () => window.innerWidth)
  const vh = useState('widget:vh', () => window.innerHeight)
  const onResize = () => {
    vw.value = window.innerWidth
    vh.value = window.innerHeight
  }
  onMounted(() => window.addEventListener('resize', onResize))
  onUnmounted(() => window.removeEventListener('resize', onResize))

  const dragX = ref<number | null>(null) // x transiente, só enquanto arrasta
  const dragging = ref(false)
  const dragged = ref(false) // houve arrasto? o clique da bolha usa isso

  const defaultY = computed(() => vh.value - BUBBLE - (route.path === '/conversas' ? 96 : MARGIN))
  const bubbleY = computed(() => clamp(y.value ?? defaultY.value, MARGIN, vh.value - BUBBLE - MARGIN))
  const bubbleX = computed(() => dragX.value ?? (side.value === 'right' ? vw.value - BUBBLE - MARGIN : MARGIN))

  // abre pro lado com mais espaço
  const openUp = computed(() => bubbleY.value + BUBBLE / 2 > vh.value / 2)
  const panelW = computed(() => clamp(w.value, MIN_W, vw.value - 2 * MARGIN))
  const panelH = computed(() => clamp(h.value, MIN_H, vh.value - 2 * MARGIN))
  // encosta na bolha, mas o clamp final garante que o painel nunca vaze da tela
  const panelTop = computed(() => clamp(
    openUp.value ? bubbleY.value - GAP - panelH.value : bubbleY.value + BUBBLE + GAP,
    MARGIN,
    vh.value - panelH.value - MARGIN
  ))

  const bubbleStyle = computed(() => ({
    left: `${bubbleX.value}px`,
    top: `${bubbleY.value}px`,
    transition: dragging.value ? 'none' : 'left .2s, top .2s'
  }))

  const panelStyle = computed(() => ({
    width: `${panelW.value}px`,
    height: `${panelH.value}px`,
    top: `${panelTop.value}px`,
    ...(side.value === 'right' ? { right: `${MARGIN}px` } : { left: `${MARGIN}px` })
  }))

  // handle de resize no canto do painel oposto às bordas ancoradas
  // (1 unidade pra dentro: no canto exato o rounded-2xl comeria as pontas do ícone)
  const nwse = computed(() => (side.value === 'right') === openUp.value)
  const handleClass = computed(() => [
    side.value === 'right' ? 'left-1' : 'right-1',
    openUp.value ? 'top-1' : 'bottom-1',
    nwse.value ? 'cursor-nwse-resize' : 'cursor-nesw-resize'
  ])
  const handleIcon = computed(() => nwse.value ? 'i-lucide-move-diagonal-2' : 'i-lucide-move-diagonal')

  watch([side, y, w, h], () => {
    localStorage.setItem(KEY, JSON.stringify({ side: side.value, y: y.value ?? bubbleY.value, w: w.value, h: h.value }))
  })

  // setPointerCapture: o arrasto continua mesmo com o cursor fora do elemento, sem listener em window
  function track(event: PointerEvent, onMove: (dx: number, dy: number) => void, onEnd?: () => void) {
    const el = event.currentTarget as HTMLElement
    const x0 = event.clientX
    const y0 = event.clientY
    let moved = false
    el.setPointerCapture(event.pointerId)
    dragging.value = true

    const move = (e: PointerEvent) => {
      const dx = e.clientX - x0
      const dy = e.clientY - y0
      // limiar: tremida de clique não vira arrasto
      if (!moved && Math.hypot(dx, dy) < 4) return
      moved = true
      onMove(dx, dy)
    }
    const up = () => {
      el.releasePointerCapture(event.pointerId)
      el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerup', up)
      dragging.value = false
      dragged.value = moved
      onEnd?.()
    }
    el.addEventListener('pointermove', move)
    el.addEventListener('pointerup', up)
  }

  function startDrag(event: PointerEvent) {
    if (event.button !== 0) return
    // o header tem botões (voltar, nova conversa, fechar) e clique neles não pode arrastar;
    // a bolha em si é um button, daí o !== : só bloqueia interativo *dentro* da pegada
    const interactive = (event.target as HTMLElement).closest('button, a, input')
    if (interactive && interactive !== event.currentTarget) return
    const x0 = bubbleX.value
    const y0 = bubbleY.value
    track(event, (dx, dy) => {
      dragX.value = clamp(x0 + dx, MARGIN, vw.value - BUBBLE - MARGIN)
      y.value = clamp(y0 + dy, MARGIN, vh.value - BUBBLE - MARGIN)
      // lado muda durante o arrasto pro painel aberto acompanhar
      side.value = dragX.value + BUBBLE / 2 < vw.value / 2 ? 'left' : 'right'
    }, () => {
      dragX.value = null // solta o x transiente: a computed gruda na borda do side
    })
  }

  function startResize(event: PointerEvent) {
    if (event.button !== 0) return
    const w0 = panelW.value
    const h0 = panelH.value
    const sx = side.value === 'right' ? -1 : 1 // ancorado à direita: puxar pra esquerda alarga
    const sy = openUp.value ? -1 : 1
    track(event, (dx, dy) => {
      w.value = clamp(w0 + dx * sx, MIN_W, vw.value - 2 * MARGIN)
      h.value = clamp(h0 + dy * sy, MIN_H, vh.value - 2 * MARGIN)
    })
  }

  return { bubbleStyle, panelStyle, handleClass, handleIcon, dragged, startDrag, startResize }
}
