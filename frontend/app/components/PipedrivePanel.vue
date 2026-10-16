<script setup lang="ts">
import type {
  Contact,
  PipedriveActivity,
  PipedriveConfig,
  PipedriveDeal,
  PipedriveNote,
  PipedrivePerson,
  PipedrivePersonResolve,
  PipedrivePipeline
} from '~/types/api'

const props = defineProps<{
  companyId: number
  contact: Contact
}>()

const api = useApi()
const toast = useToast()
const confirmModal = useConfirm()

const config = ref<PipedriveConfig | null>(null)
const pipelines = ref<PipedrivePipeline[]>([])
const person = ref<PipedrivePerson | null>(null)
const suggestions = ref<PipedrivePerson[]>([])
const deals = ref<PipedriveDeal[]>([])
const loading = ref(false)

// busca manual / troca de vínculo
const searching = ref(false)
const searchTerm = ref('')
const searchResults = ref<PipedrivePerson[]>([])
const searchLoading = ref(false)

// criar pessoa
const personName = ref('')
const creatingPerson = ref(false)

// novo negócio
const showDealForm = ref(false)
const dealTitle = ref('')
const dealValue = ref<number | null>(null)
const creatingDeal = ref(false)

// notas e atividades por deal (lazy, ao expandir)
const expandedDeal = ref<number | null>(null)
const notesByDeal = ref<Record<number, PipedriveNote[]>>({})
const activitiesByDeal = ref<Record<number, PipedriveActivity[]>>({})
const noteContent = ref('')
const savingNote = ref(false)

const base = computed(() => `/companies/${props.companyId}`)

async function load() {
  loading.value = true
  person.value = null
  suggestions.value = []
  deals.value = []
  searching.value = false
  searchTerm.value = ''
  searchResults.value = []
  showDealForm.value = false
  expandedDeal.value = null
  notesByDeal.value = {}
  activitiesByDeal.value = {}
  try {
    config.value = await api<PipedriveConfig>(`${base.value}/pipedrive`)
  } catch {
    config.value = null // 404 = não conectado, painel não renderiza
    loading.value = false
    return
  }
  try {
    const resolve = await api<PipedrivePersonResolve>(`${base.value}/contacts/${props.contact.id}/pipedrive/person`)
    person.value = resolve.person
    suggestions.value = resolve.suggestions
    personName.value = props.contact.name || ''
    if (person.value) {
      await Promise.all([loadDeals(), loadPipelines()])
    }
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao consultar o Pipedrive'), color: 'error' })
  } finally {
    loading.value = false
  }
}
watch(() => props.contact.id, load, { immediate: true })

async function loadPipelines() {
  if (pipelines.value.length) return
  pipelines.value = await api<PipedrivePipeline[]>(`${base.value}/pipedrive/pipelines`)
}

async function loadDeals() {
  deals.value = await api<PipedriveDeal[]>(`${base.value}/contacts/${props.contact.id}/pipedrive/deals`)
}

function stageItems(deal: PipedriveDeal) {
  return pipelines.value.find(p => p.id === deal.pipeline_id)?.stages.map(s => ({ label: s.name, value: s.id })) ?? []
}

const valueFmt = (deal: PipedriveDeal) => {
  if (!deal.value) return null
  try {
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: deal.currency || 'BRL' }).format(deal.value)
  } catch {
    return `${deal.value} ${deal.currency ?? ''}`
  }
}

// ---- vínculo ----
async function linkPerson(target: PipedrivePerson) {
  try {
    person.value = await api<PipedrivePerson>(`${base.value}/contacts/${props.contact.id}/pipedrive/person`, {
      method: 'PUT', body: { person_id: target.id }
    })
    suggestions.value = []
    searching.value = false
    toast.add({ title: 'Contato vinculado ao Pipedrive', color: 'success' })
    await Promise.all([loadDeals(), loadPipelines()])
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao vincular'), color: 'error' })
  }
}

async function createPerson() {
  if (!personName.value.trim()) return
  creatingPerson.value = true
  try {
    person.value = await api<PipedrivePerson>(`${base.value}/contacts/${props.contact.id}/pipedrive/person`, {
      method: 'POST', body: { name: personName.value.trim() }
    })
    suggestions.value = []
    searching.value = false
    toast.add({ title: 'Pessoa criada no Pipedrive', color: 'success' })
    await Promise.all([loadDeals(), loadPipelines()])
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao criar pessoa'), color: 'error' })
  } finally {
    creatingPerson.value = false
  }
}

async function searchPersons() {
  if (searchTerm.value.trim().length < 2) return
  searchLoading.value = true
  try {
    searchResults.value = await api<PipedrivePerson[]>(
      `${base.value}/pipedrive/persons`, { query: { term: searchTerm.value.trim() } }
    )
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha na busca'), color: 'error' })
  } finally {
    searchLoading.value = false
  }
}

// ---- negócios ----
async function createDeal() {
  if (!dealTitle.value.trim()) return
  creatingDeal.value = true
  try {
    await api(`${base.value}/contacts/${props.contact.id}/pipedrive/deals`, {
      method: 'POST', body: { title: dealTitle.value.trim(), value: dealValue.value }
    })
    dealTitle.value = ''
    dealValue.value = null
    showDealForm.value = false
    toast.add({ title: 'Negócio criado', color: 'success' })
    if (!person.value) await load() // person pode ter sido criada/vinculada pelo backend
    else await loadDeals()
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao criar negócio'), color: 'error' })
  } finally {
    creatingDeal.value = false
  }
}

async function moveDealStage(deal: PipedriveDeal, stage_id: number) {
  try {
    await api(`${base.value}/pipedrive/deals/${deal.id}`, { method: 'PATCH', body: { stage_id } })
    deal.stage_id = stage_id
    toast.add({ title: 'Etapa atualizada', color: 'success' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao mover etapa'), color: 'error' })
  }
}

async function closeDeal(deal: PipedriveDeal, status: 'won' | 'lost') {
  const ok = await confirmModal({
    title: status === 'won' ? `Marcar "${deal.title}" como ganho?` : `Marcar "${deal.title}" como perdido?`,
    confirmLabel: status === 'won' ? 'Ganhou' : 'Perdeu'
  })
  if (!ok) return
  try {
    await api(`${base.value}/pipedrive/deals/${deal.id}`, { method: 'PATCH', body: { status } })
    deals.value = deals.value.filter(d => d.id !== deal.id)
    toast.add({ title: status === 'won' ? 'Negócio ganho' : 'Negócio perdido', color: 'success' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

// ---- notas e atividades ----
async function toggleDeal(deal: PipedriveDeal) {
  if (expandedDeal.value === deal.id) {
    expandedDeal.value = null
    return
  }
  expandedDeal.value = deal.id
  noteContent.value = ''
  if (!notesByDeal.value[deal.id]) {
    try {
      const [notes, activities] = await Promise.all([
        api<PipedriveNote[]>(`${base.value}/pipedrive/deals/${deal.id}/notes`),
        api<PipedriveActivity[]>(`${base.value}/pipedrive/deals/${deal.id}/activities`)
      ])
      notesByDeal.value[deal.id] = notes
      activitiesByDeal.value[deal.id] = activities
    } catch (error) {
      toast.add({ title: apiErrorDetail(error, 'Falha ao carregar detalhes do negócio'), color: 'error' })
    }
  }
}

async function createNote(dealId: number) {
  if (!noteContent.value.trim()) return
  savingNote.value = true
  try {
    const note = await api<PipedriveNote>(`${base.value}/pipedrive/deals/${dealId}/notes`, {
      method: 'POST', body: { content: noteContent.value.trim() }
    })
    notesByDeal.value[dealId] = [note, ...(notesByDeal.value[dealId] ?? [])]
    noteContent.value = ''
    toast.add({ title: 'Anotação salva', color: 'success' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error, 'Falha ao salvar anotação'), color: 'error' })
  } finally {
    savingNote.value = false
  }
}

async function completeActivity(dealId: number, activity: PipedriveActivity) {
  try {
    await api(`${base.value}/pipedrive/activities/${activity.id}/done`, { method: 'POST' })
    activitiesByDeal.value[dealId] = (activitiesByDeal.value[dealId] ?? []).filter(a => a.id !== activity.id)
    toast.add({ title: 'Atividade concluída', color: 'success' })
  } catch (error) {
    toast.add({ title: apiErrorDetail(error), color: 'error' })
  }
}

const noteDateFmt = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit' })
const noteDate = (raw: string | null) => {
  if (!raw) return ''
  const parsed = new Date(raw.replace(' ', 'T') + 'Z')
  return Number.isNaN(parsed.getTime()) ? raw : noteDateFmt.format(parsed)
}
</script>

<template>
  <div v-if="config" class="space-y-3">
    <!-- o título "Pipedrive" vem da seção recolhível de Conversas -->
    <div v-if="person && !searching" class="flex justify-end -mb-2">
      <UButton
        v-if="person && !searching"
        label="Trocar"
        variant="ghost"
        color="neutral"
        size="xs"
        @click="() => { searching = true }"
      />
    </div>

    <div v-if="loading" class="space-y-2">
      <USkeleton class="h-5 w-full" />
      <USkeleton class="h-5 w-2/3" />
    </div>

    <template v-else>
      <!-- vinculado -->
      <div v-if="person && !searching" class="space-y-3">
        <div class="flex items-center gap-2 rounded-lg ring-1 ring-default px-3 py-2">
          <UIcon name="i-lucide-user-round-check" class="size-4 text-primary shrink-0" />
          <div class="min-w-0">
            <p class="text-sm font-medium truncate">{{ person.name }}</p>
            <p v-if="person.phones.length" class="text-xs text-muted font-mono truncate">{{ person.phones[0] }}</p>
          </div>
        </div>

        <!-- negócios abertos -->
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <p class="text-xs text-muted">Negócios abertos</p>
            <UButton
              :icon="showDealForm ? 'i-lucide-x' : 'i-lucide-plus'"
              variant="ghost"
              color="neutral"
              size="xs"
              aria-label="Novo negócio"
              @click="() => { showDealForm = !showDealForm }"
            />
          </div>

          <form v-if="showDealForm" class="space-y-2" @submit.prevent="createDeal">
            <UInput v-model="dealTitle" placeholder="Título do negócio" size="sm" class="w-full" />
            <div class="flex gap-2">
              <UInput v-model.number="dealValue" type="number" placeholder="Valor (opcional)" size="sm" class="flex-1" />
              <UButton type="submit" label="Criar" size="sm" :loading="creatingDeal" :disabled="!dealTitle.trim()" />
            </div>
          </form>

          <p v-if="!deals.length && !showDealForm" class="text-sm text-muted">Nenhum negócio aberto.</p>

          <div v-for="deal in deals" :key="deal.id" class="rounded-lg ring-1 ring-default">
            <button type="button" class="w-full flex items-center gap-2 px-3 py-2 text-left" @click="toggleDeal(deal)">
              <div class="flex-1 min-w-0">
                <p class="text-sm font-medium truncate">{{ deal.title }}</p>
                <p v-if="valueFmt(deal)" class="text-xs text-muted">{{ valueFmt(deal) }}</p>
              </div>
              <UIcon
                :name="expandedDeal === deal.id ? 'i-lucide-chevron-up' : 'i-lucide-chevron-down'"
                class="size-4 text-muted shrink-0"
              />
            </button>

            <div v-if="expandedDeal === deal.id" class="px-3 pb-3 space-y-3">
              <div class="flex gap-1.5 items-center">
                <USelect
                  :model-value="deal.stage_id ?? undefined"
                  :items="stageItems(deal)"
                  size="xs"
                  class="flex-1"
                  placeholder="Etapa"
                  @update:model-value="moveDealStage(deal, $event as number)"
                />
                <UButton icon="i-lucide-check" color="success" variant="soft" size="xs" aria-label="Ganhou" @click="closeDeal(deal, 'won')" />
                <UButton icon="i-lucide-x" color="error" variant="soft" size="xs" aria-label="Perdeu" @click="closeDeal(deal, 'lost')" />
              </div>

              <!-- atividades pendentes -->
              <div v-if="activitiesByDeal[deal.id]?.length" class="space-y-1">
                <p class="text-xs text-muted">Atividades pendentes</p>
                <div
                  v-for="activity in activitiesByDeal[deal.id]"
                  :key="activity.id"
                  class="flex items-center gap-2 text-sm"
                >
                  <UButton
                    icon="i-lucide-circle"
                    variant="ghost"
                    color="neutral"
                    size="xs"
                    aria-label="Concluir atividade"
                    @click="completeActivity(deal.id, activity)"
                  />
                  <span class="flex-1 min-w-0 truncate">{{ activity.subject || activity.type }}</span>
                  <span v-if="activity.due_date" class="text-xs text-muted shrink-0">{{ noteDate(activity.due_date) }}</span>
                </div>
              </div>

              <!-- notas -->
              <div class="space-y-2">
                <form class="flex gap-1.5" @submit.prevent="createNote(deal.id)">
                  <UInput v-model="noteContent" placeholder="Nova anotação" size="xs" class="flex-1" />
                  <UButton type="submit" icon="i-lucide-send" size="xs" variant="soft" :loading="savingNote" :disabled="!noteContent.trim()" aria-label="Salvar anotação" />
                </form>
                <div
                  v-for="note in notesByDeal[deal.id] ?? []"
                  :key="note.id"
                  class="text-xs bg-elevated rounded p-2 space-y-1"
                >
                  <!-- notas do Pipedrive chegam em HTML: strip de tags, nunca v-html -->
                  <p class="whitespace-pre-wrap break-words" v-text="note.content.replace(/<[^>]*>/g, '')" />
                  <p class="text-muted">{{ noteDate(note.add_time) }}</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- sem vínculo ou trocando -->
      <div v-else class="space-y-3">
        <div v-if="suggestions.length && !searching" class="space-y-2">
          <p class="text-xs text-muted">Encontrado no Pipedrive:</p>
          <div
            v-for="suggestion in suggestions"
            :key="suggestion.id"
            class="flex items-center gap-2 rounded-lg ring-1 ring-default px-3 py-2"
          >
            <div class="flex-1 min-w-0">
              <p class="text-sm font-medium truncate">{{ suggestion.name }}</p>
              <p v-if="suggestion.phones.length" class="text-xs text-muted font-mono truncate">{{ suggestion.phones[0] }}</p>
            </div>
            <UButton label="Vincular" size="xs" variant="soft" @click="linkPerson(suggestion)" />
          </div>
        </div>

        <form class="flex gap-1.5" @submit.prevent="searchPersons">
          <UInput v-model="searchTerm" placeholder="Buscar pessoa no CRM" size="sm" class="flex-1" />
          <UButton type="submit" icon="i-lucide-search" size="sm" variant="soft" :loading="searchLoading" :disabled="searchTerm.trim().length < 2" aria-label="Buscar" />
        </form>

        <div v-if="searchResults.length" class="space-y-2">
          <div
            v-for="result in searchResults"
            :key="result.id"
            class="flex items-center gap-2 rounded-lg ring-1 ring-default px-3 py-2"
          >
            <div class="flex-1 min-w-0">
              <p class="text-sm font-medium truncate">{{ result.name }}</p>
              <p v-if="result.phones.length" class="text-xs text-muted font-mono truncate">{{ result.phones[0] }}</p>
            </div>
            <UButton label="Vincular" size="xs" variant="soft" @click="linkPerson(result)" />
          </div>
        </div>

        <form v-if="!person" class="flex gap-1.5" @submit.prevent="createPerson">
          <UInput v-model="personName" placeholder="Nome para criar no CRM" size="sm" class="flex-1" />
          <UButton type="submit" label="Criar" size="sm" :loading="creatingPerson" :disabled="!personName.trim()" />
        </form>
        <UButton v-if="person" label="Cancelar troca" variant="ghost" color="neutral" size="xs" block @click="() => { searching = false }" />
      </div>
    </template>
  </div>
</template>
