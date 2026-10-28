// Metadados dos tipos de nó do editor de automações.
// Fonte única pra paleta, card do canvas, inspetor e linha do tempo.

import type { FlowNode, FlowNodeData, FlowNodeType } from '~/types/api'

interface NodeMeta {
  label: string
  icon: string
  /** classe de cor do ícone (Tailwind) */
  color: string
  hint: string
  data: () => FlowNodeData
}

export const FLOW_NODE_META: Record<FlowNodeType, NodeMeta> = {
  start: {
    label: 'Início',
    icon: 'i-lucide-flag',
    color: 'text-success',
    hint: 'Ponto de partida do fluxo: onde o gatilho entra',
    data: () => ({})
  },
  template: {
    label: 'Template',
    icon: 'i-lucide-layout-template',
    color: 'text-primary',
    hint: 'Única forma de abrir conversa fora da janela de 24h',
    data: () => ({ template_name: '', language: '', body_params: [], header_media_id: '' })
  },
  message: {
    label: 'Mensagem',
    icon: 'i-lucide-message-square',
    color: 'text-info',
    hint: 'Texto livre, só funciona dentro da janela de 24h',
    data: () => ({ text: '' })
  },
  buttons: {
    label: 'Pergunta com botões',
    icon: 'i-lucide-list-checks',
    color: 'text-info',
    hint: 'Até 3 botões de 20 caracteres, uma saída por botão',
    data: () => ({ text: '', buttons: [{ id: 'b1', title: 'Sim' }, { id: 'b2', title: 'Não' }] })
  },
  wait: {
    label: 'Aguardar resposta',
    icon: 'i-lucide-hourglass',
    color: 'text-warning',
    hint: 'Pausa até o contato responder e guarda o texto numa variável',
    data: () => ({ var: 'resposta', timeout_minutes: 0 })
  },
  collect: {
    label: 'Coletar dado',
    icon: 'i-lucide-text-cursor-input',
    color: 'text-warning',
    hint: 'Pergunta, espera a resposta e salva num campo do contato (ex.: e-mail)',
    data: () => ({ text: '', field: '', timeout_minutes: 0 })
  },
  condition: {
    label: 'Condição',
    icon: 'i-lucide-git-branch',
    color: 'text-warning',
    hint: 'Compara a última resposta ou uma variável',
    data: () => ({ source: 'last_message', op: 'contains', value: '' })
  },
  delay: {
    label: 'Espera',
    icon: 'i-lucide-timer',
    color: 'text-warning',
    hint: 'Segura o fluxo por um tempo antes do próximo nó',
    data: () => ({ minutes: 60 })
  },
  crm: {
    label: 'Pipedrive',
    icon: 'i-lucide-handshake',
    color: 'text-success',
    hint: 'Cria pessoa, negócio, anotação, move de estágio ou etiqueta o negócio',
    data: () => ({ action: 'deal', title: '', text: '' })
  },
  http: {
    label: 'Requisição HTTP',
    icon: 'i-lucide-globe',
    color: 'text-success',
    hint: 'Chama outro sistema (https). Segue por Sucesso (2xx) ou Falha',
    data: () => ({ method: 'POST', url: '', headers: [], body: '{\n  "nome": "{{nome}}",\n  "telefone": "{{telefone}}",\n  "botao": "{{botao}}"\n}' })
  },
  tag: {
    label: 'Etiqueta',
    icon: 'i-lucide-tag',
    color: 'text-info',
    hint: 'Adiciona ou remove uma etiqueta do contato',
    data: () => ({ action: 'add' })
  },
  pause: {
    label: 'Pausar IA',
    icon: 'i-lucide-pause',
    color: 'text-warning',
    hint: 'Pausa a IA sem avisar a equipe. O fluxo segue, ou para aqui se for o último bloco',
    data: () => ({})
  },
  handoff: {
    label: 'Passar pro humano',
    icon: 'i-lucide-user-round',
    color: 'text-error',
    hint: 'Pausa a IA e avisa a equipe. Encerra o fluxo',
    data: () => ({})
  },
  ai: {
    label: 'Entregar pra IA',
    icon: 'i-lucide-bot',
    color: 'text-primary',
    hint: 'Encerra o fluxo e devolve a conversa pro agente',
    data: () => ({})
  },
  end: {
    label: 'Encerrar',
    icon: 'i-lucide-circle-stop',
    color: 'text-muted',
    hint: 'Fim do fluxo: para aqui sem passar pra IA nem pro humano',
    data: () => ({})
  }
}

export const FLOW_NODE_TYPES = Object.keys(FLOW_NODE_META) as FlowNodeType[]

// paleta não arrasta o Início: ele é único e já vem pronto no canvas
export const FLOW_PALETTE_TYPES = FLOW_NODE_TYPES.filter(type => type !== 'start')

export const FLOW_TRIGGER_LABELS: Record<string, string> = {
  lead: 'Lead recebido',
  first_message: 'Primeira mensagem do contato',
  keyword: 'Palavra-chave',
  campaign: 'Clique em botão de campanha'
}

const CONDITION_OPS: Record<string, string> = {
  contains: 'contém',
  equals: 'é igual a',
  regex: 'casa com',
  filled: 'está preenchido',
  has_tag: 'tem a etiqueta',
  window_open: 'janela de 24h aberta'
}

const CONDITION_SOURCES: Record<string, string> = {
  last_message: 'resposta',
  button: 'botão clicado'
}

const CRM_ACTIONS: Record<string, string> = {
  person: 'Criar/vincular pessoa',
  deal: 'Criar negócio',
  note: 'Salvar anotação',
  stage: 'Mover de estágio',
  label: 'Etiqueta no negócio',
  field: 'Preencher campo'
}

export const FLOW_CRM_ACTION_ITEMS = Object.entries(CRM_ACTIONS).map(([value, label]) => ({ label, value }))
export const FLOW_CONDITION_OP_ITEMS = Object.entries(CONDITION_OPS).map(([value, label]) => ({ label, value }))

/** Saídas do nó. Uma saída sem id vira aresta com sourceHandle null. */
export function flowNodeOutputs(type: FlowNodeType, data: FlowNodeData): { id: string | undefined, label: string }[] {
  if (type === 'handoff' || type === 'ai' || type === 'end') return []
  if (type === 'buttons') return (data.buttons ?? []).map(b => ({ id: b.id, label: b.title || 'Sem nome' }))
  if (type === 'condition') return [{ id: 'sim', label: 'Sim' }, { id: 'nao', label: 'Não' }]
  if (type === 'http') return [{ id: 'sucesso', label: 'Sucesso' }, { id: 'falha', label: 'Falha' }]
  if ((type === 'wait' || type === 'collect') && data.timeout_minutes) {
    return [{ id: undefined, label: 'Respondeu' }, { id: 'timeout', label: 'Não respondeu' }]
  }
  return [{ id: undefined, label: '' }]
}

/** Resumo de uma linha exibido no card do canvas. */
export function flowNodeSummary(type: FlowNodeType, data: FlowNodeData): string {
  switch (type) {
    case 'start':
      return data.text || 'Início do fluxo'
    case 'template':
      return data.template_name || 'Escolha um template'
    case 'message':
    case 'buttons':
      return data.text || 'Escreva a mensagem'
    case 'collect':
      return data.field ? `Salva em {{${data.field}}}` : 'Escolha o campo'
    case 'wait':
      return `Guarda em {{${data.var || 'resposta'}}}`
    case 'condition':
      if (data.op === 'window_open') return 'Janela de 24h aberta? (Sim = IA responde direto)'
      if (data.op === 'has_tag') return 'Contato tem a etiqueta escolhida?'
      if (data.op === 'filled') return `${CONDITION_SOURCES[data.source ?? ''] ?? `{{${data.source}}}`} está preenchido?`
      return `${CONDITION_SOURCES[data.source ?? 'last_message'] ?? `{{${data.source}}}`} ${CONDITION_OPS[data.op ?? 'contains']} ${(data.op !== 'regex' && data.values?.length ? data.values : [data.value || '...']).map(v => `"${v}"`).join(' ou ')}`
    case 'delay':
      return formatMinutes(data.minutes ?? 0)
    case 'crm':
      if (data.action === 'field') return data.field ? `Preenche campo com ${data.value || '...'}` : 'Escolha o campo'
      if (data.action === 'label') return data.label_mode === 'remove' ? 'Remove etiqueta do negócio' : 'Etiqueta o negócio'
      return CRM_ACTIONS[data.action ?? 'deal'] ?? ''
    case 'http':
      return data.url ? `${data.method ?? 'POST'} ${data.url}` : 'Informe a URL'
    case 'tag':
      return data.action === 'remove' ? 'Remove etiqueta' : 'Adiciona etiqueta'
    default:
      return FLOW_NODE_META[type].hint
  }
}

export function formatMinutes(minutes: number): string {
  if (minutes >= 1440) {
    const days = Math.round(minutes / 1440)
    return `${days} dia${days > 1 ? 's' : ''}`
  }
  if (minutes >= 60) {
    const hours = Math.round(minutes / 60)
    return `${hours} hora${hours > 1 ? 's' : ''}`
  }
  return `${minutes} min`
}

/** Nó novo com a config padrão do tipo. */
export function newFlowNode(type: FlowNodeType, position: { x: number, y: number }): FlowNode {
  return {
    id: `n${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`,
    type,
    position,
    data: FLOW_NODE_META[type].data()
  }
}
