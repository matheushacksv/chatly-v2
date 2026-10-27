<script setup lang="ts">
// Editor de fluxo: paleta à esquerda, canvas no meio, inspetor do nó à direita.
// O estado do grafo vive no store do vue-flow (useVueFlow), não em refs locais:
// é ele que já aplica arrasto, seleção e zoom.
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import type { Connection, GraphNode, Node, NodeTypesObject } from '@vue-flow/core'
import { MarkerType, Panel, VueFlow, useVueFlow } from '@vue-flow/core'
import FlowNode from '~/components/FlowNode.vue'
import type { Flow, FlowGraph, FlowNodeData, FlowNodeType, Template } from '~/types/api'

const props = defineProps<{ flow: Flow, companyId: number }>()
const emit = defineEmits<{ saved: [Flow], close: [] }>()

const api = useApi()
const toast = useToast()
const { nodes, edges, addNodes, addEdges, removeNodes, removeEdges, findNode, screenToFlowCoordinate } = useVueFlow()

// todos os tipos usam o mesmo card; o cast é porque FlowNode declara só as props que usa
const nodeTypes = Object.fromEntries(
  FLOW_NODE_TYPES.map(type => [type, markRaw(FlowNode)])
) as unknown as NodeTypesObject

// rótulo do gatilho estampado no card do Início
const triggerLabel = (() => {
  const base = FLOW_TRIGGER_LABELS[props.flow.trigger] ?? 'Início do fluxo'
  const keyword = (props.flow.trigger_config as { keyword?: string } | undefined)?.keyword
  return props.flow.trigger === 'keyword' && keyword ? `${base}: "${keyword}"` : base
})()

const START_ID = 'start'

// todo fluxo tem exatamente um bloco Início (único, sem entrada, não-deletável).
// Fluxo antigo sem ele: injeta e liga no root atual pra não perder a entrada.
function buildInitial() {
  const nodes: Node[] = props.flow.graph.nodes.map(n => ({ ...n }))
  const edges = props.flow.graph.edges.map(e => ({ ...e, markerEnd: MarkerType.ArrowClosed }))
  const existing = nodes.find(n => n.type === 'start')
  if (existing) {
    Object.assign(existing, { deletable: false, data: { ...existing.data, text: triggerLabel } })
    return { nodes, edges }
  }
  const targets = new Set(edges.map(e => e.target))
  const root = nodes.find(n => !targets.has(n.id))
  nodes.unshift({ id: START_ID, type: 'start', position: { x: 32, y: 32 }, data: { text: triggerLabel }, deletable: false })
  if (root) edges.push({ id: `e-start-${root.id}`, source: START_ID, target: root.id, sourceHandle: null, markerEnd: MarkerType.ArrowClosed })
  return { nodes, edges }
}

const { nodes: initialNodes, edges: initialEdges } = buildInitial()

const selectedId = ref<string | null>(null)
const selected = computed<GraphNode | null>(() => (selectedId.value ? findNode(selectedId.value) ?? null : null))

// templates da conta: pra barrar save de nó template com variável faltando/em branco
const templates = ref<Template[]>([])
onMounted(async () => {
  try {
    templates.value = await api<Template[]>(`/companies/${props.companyId}/accounts/${props.flow.account}/templates`)
  } catch {
    // sem a lista ainda dá pra pegar param em branco; a contagem exata o backend revalida ao ativar
  }
})

// devolve a 1ª pendência de um nó template, ou null se tudo certo
function templateIssue(): string | null {
  for (const node of nodes.value) {
    if (node.type !== 'template') continue
    const data = node.data as FlowNodeData
    if (!data.template_name) return 'Um bloco de template está sem template escolhido'
    const tpl = templates.value.find(t => t.name === data.template_name && t.language === data.language)
    const params = data.body_params ?? []
    const need = tpl?.param_count ?? params.length
    if (params.length !== need || params.some(p => !p.trim())) {
      return `Preencha as ${need} variável(is) do template "${data.template_name}"`
    }
  }
  return null
}

const outputsOf = (node: { type: string, data: FlowNodeData }) => flowNodeOutputs(node.type as FlowNodeType, node.data)

// botão removido no inspetor deixa aresta pendurada num handle que não existe mais
watch(nodes, () => {
  const valid = new Set(nodes.value.flatMap(n => outputsOf(n).map(o => `${n.id}:${o.id ?? ''}`)))
  const orphans = edges.value.filter(e => !valid.has(`${e.source}:${e.sourceHandle ?? ''}`))
  if (orphans.length) removeEdges(orphans.map(e => e.id))
}, { deep: true })

// ---- canvas ----

function onConnect(connection: Connection) {
  const handle = connection.sourceHandle ?? null
  // uma saída aponta pra um destino só
  const replaced = edges.value.filter(e => e.source === connection.source && (e.sourceHandle ?? null) === handle)
  if (replaced.length) removeEdges(replaced.map(e => e.id))
  addEdges([{
    ...connection,
    id: `e${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`,
    markerEnd: MarkerType.ArrowClosed
  }])
}

function addNode(type: FlowNodeType, position: { x: number, y: number }) {
  const node = newFlowNode(type, position)
  addNodes([node])
  selectedId.value = node.id
}

const canvas = ref<HTMLElement | null>(null)

function onDrop(event: DragEvent) {
  const type = event.dataTransfer?.getData('application/flow-node') as FlowNodeType
  if (!type) return
  addNode(type, screenToFlowCoordinate({ x: event.clientX, y: event.clientY }))
}

function onPaletteDragStart(event: DragEvent, type: FlowNodeType) {
  event.dataTransfer?.setData('application/flow-node', type)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}

// clicar na paleta cai no meio do canvas (atalho pra quem não quer arrastar)
function onPaletteClick(type: FlowNodeType) {
  const box = canvas.value?.getBoundingClientRect()
  const point = box
    ? screenToFlowCoordinate({ x: box.left + box.width / 2, y: box.top + box.height / 3 })
    : { x: 0, y: 0 }
  addNode(type, point)
}

function removeSelected() {
  if (!selectedId.value || selected.value?.type === 'start') return
  removeNodes([selectedId.value])
  selectedId.value = null
}

function select(id: string | null) {
  selectedId.value = id
}

// ---- avisos (o backend revalida ao ativar) ----

const warnings = computed(() => {
  const list: string[] = []
  const startNode = nodes.value.find(n => n.type === 'start')
  const nonStart = nodes.value.filter(n => n.type !== 'start')
  if (!nonStart.length) return ['Fluxo vazio: arraste um bloco e ligue no Início']
  // o primeiro passo é o alvo da saída do Início
  const startEdge = edges.value.find(e => e.source === startNode?.id)
  if (!startEdge) {
    list.push('Ligue o bloco Início ao primeiro passo')
  } else if (props.flow.trigger === 'lead') {
    const first = findNode(startEdge.target)
    if (first && first.type !== 'template') {
      list.push('Lead recebido cai fora da janela de 24h: o primeiro bloco tem que ser um template')
    }
  }
  // blocos (fora o Início) sem nenhuma aresta de entrada = soltos, não rodam
  const targets = new Set(edges.value.map(e => e.target))
  const orphans = nonStart.filter(n => !targets.has(n.id))
  if (orphans.length) list.push(`${orphans.length} bloco(s) sem entrada: não vão rodar`)
  const connected = new Set(edges.value.map(e => `${e.source}:${e.sourceHandle ?? ''}`))
  const loose = nonStart.filter(n => outputsOf(n).some(o => !connected.has(`${n.id}:${o.id ?? ''}`)))
  if (loose.length) list.push(`${loose.length} bloco(s) com saída solta: o fluxo termina ali`)
  const tpl = templateIssue()
  if (tpl) list.push(tpl)
  return list
})

// ---- persistência ----

const saving = ref(false)
const name = ref(props.flow.name)
const activating = ref(false)

function serialize(): FlowGraph {
  return {
    nodes: nodes.value.map(n => ({
      id: n.id,
      type: n.type as FlowNodeType,
      position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
      data: n.data as FlowNodeData
    })),
    edges: edges.value.map(e => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle ?? null
    }))
  }
}

async function save(): Promise<Flow | null> {
  const issue = templateIssue()
  if (issue) {
    toast.add({ title: issue, color: 'error', icon: 'i-lucide-triangle-alert' })
    return null
  }
  saving.value = true
  try {
    const saved = await api<Flow>(`/automations/${props.companyId}/flows/${props.flow.id}`, {
      method: 'PUT',
      body: {
        name: name.value.trim() || props.flow.name,
        account_id: props.flow.account,
        trigger: props.flow.trigger,
        trigger_config: props.flow.trigger_config,
        graph: serialize()
      }
    })
    emit('saved', saved)
    return saved
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao salvar o fluxo'), color: 'error' })
    return null
  } finally {
    saving.value = false
  }
}

async function saveAndNotify() {
  if (await save()) toast.add({ title: 'Fluxo salvo', icon: 'i-lucide-check', color: 'success' })
}

async function toggleActive() {
  activating.value = true
  try {
    // ativar valida o desenho no servidor, então o que está na tela precisa estar salvo antes
    if (!(await save())) return
    const saved = await api<Flow>(`/automations/${props.companyId}/flows/${props.flow.id}/activate`, {
      method: 'POST',
      body: { active: !props.flow.active }
    })
    emit('saved', saved)
    toast.add({
      title: saved.active ? 'Fluxo ativado' : 'Fluxo pausado',
      icon: saved.active ? 'i-lucide-play' : 'i-lucide-pause',
      color: 'success'
    })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao ativar o fluxo'), color: 'error' })
  } finally {
    activating.value = false
  }
}

// opções dos dropdowns do inspetor: variáveis guardadas pelos blocos Aguardar e textos dos blocos Botões
const flowVars = computed(() => [...new Set(nodes.value.filter(n => n.type === 'wait').map(n => (n.data as FlowNodeData).var || 'resposta'))])
const flowButtons = computed(() => [...new Set(nodes.value.filter(n => n.type === 'buttons')
  .flatMap(n => ((n.data as FlowNodeData).buttons ?? []).map(b => b.title)).filter(Boolean))])
</script>

<template>
  <div class="flex-1 min-h-0 flex">
    <!-- paleta -->
    <aside class="w-44 shrink-0 border-r border-default overflow-y-auto p-2 space-y-1">
      <p class="px-1 py-1 text-xs font-semibold text-muted uppercase tracking-wide">
        Blocos
      </p>
      <button
        v-for="type in FLOW_PALETTE_TYPES"
        :key="type"
        draggable="true"
        class="w-full flex items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs hover:bg-elevated cursor-grab active:cursor-grabbing"
        @dragstart="onPaletteDragStart($event, type)"
        @click="onPaletteClick(type)"
      >
        <UIcon
          :name="FLOW_NODE_META[type].icon"
          class="size-4 shrink-0"
          :class="FLOW_NODE_META[type].color"
        />
        <span class="truncate">{{ FLOW_NODE_META[type].label }}</span>
      </button>
    </aside>

    <!-- canvas -->
    <div
      ref="canvas"
      class="flex-1 min-w-0 relative"
      @drop="onDrop"
      @dragover.prevent
    >
      <VueFlow
        :nodes="initialNodes"
        :edges="initialEdges"
        :node-types="nodeTypes"
        :min-zoom="0.3"
        :max-zoom="1.6"
        delete-key-code="Delete"
        fit-view-on-init
        @connect="onConnect"
        @node-click="select($event.node.id)"
        @pane-click="select(null)"
      >
        <Background :gap="16" />
        <Controls :show-interactive="false" />

        <Panel
          position="top-right"
          class="flex items-center gap-2"
        >
          <UInput
            v-model="name"
            size="sm"
            icon="i-lucide-pencil"
            class="w-56"
            aria-label="Nome do fluxo"
            @keydown.enter="saveAndNotify()"
          />
          <UButton
            label="Salvar"
            icon="i-lucide-save"
            variant="soft"
            size="sm"
            :loading="saving"
            @click="saveAndNotify()"
          />
          <UButton
            :label="flow.active ? 'Pausar' : 'Ativar'"
            :icon="flow.active ? 'i-lucide-pause' : 'i-lucide-play'"
            :color="flow.active ? 'warning' : 'primary'"
            size="sm"
            :loading="activating"
            @click="toggleActive()"
          />
          <UButton
            icon="i-lucide-x"
            color="neutral"
            variant="ghost"
            size="sm"
            @click="emit('close')"
          />
        </Panel>

        <Panel
          v-if="warnings.length"
          position="bottom-center"
          class="max-w-lg"
        >
          <div class="rounded-lg bg-warning/10 ring-1 ring-warning/30 px-3 py-2 text-xs text-warning space-y-0.5">
            <p
              v-for="warning in warnings"
              :key="warning"
            >
              {{ warning }}
            </p>
          </div>
        </Panel>
      </VueFlow>
    </div>

    <!-- inspetor (o Início não tem config) -->
    <aside
      v-if="selected && selected.type !== 'start'"
      class="w-80 shrink-0 border-l border-default overflow-y-auto p-4 space-y-4"
    >
      <FlowNodeInspector
        :key="selected.id"
        v-model="selected.data"
        :type="selected.type as FlowNodeType"
        :company-id="companyId"
        :account-id="flow.account"
        :flow-vars="flowVars"
        :flow-buttons="flowButtons"
      />
      <UButton
        label="Remover bloco"
        icon="i-lucide-trash-2"
        color="error"
        variant="soft"
        size="sm"
        block
        @click="removeSelected()"
      />
    </aside>
  </div>
</template>
