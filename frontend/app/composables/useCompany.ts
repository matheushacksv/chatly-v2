import type { Company } from '~/types/api'

const ACTIVE_KEY = 'chatly_company'

/** Companies do usuário + company ativa (persistida em localStorage). */
export function useCompany() {
  const api = useApi()
  const companies = useState<Company[]>('company:list', () => [])
  const activeId = useState<number | null>('company:active', () => {
    const raw = localStorage.getItem(ACTIVE_KEY)
    return raw ? Number(raw) : null
  })

  const active = computed(() => companies.value.find(c => c.id === activeId.value) ?? null)

  // role null/undefined = company recém-criada pelo próprio user = dono; sem company ativa não é dono
  // de nada (antes virava true e mostrava convites/membros pra quem ainda não criou a empresa)
  const isOwner = computed(() => !!active.value && active.value.role !== 'member')

  async function load() {
    companies.value = await api<Company[]>('/companies/')
    if (!companies.value.some(c => c.id === activeId.value)) {
      setActive(companies.value[0]?.id ?? null)
    }
  }

  function setActive(id: number | null) {
    activeId.value = id
    if (id === null) localStorage.removeItem(ACTIVE_KEY)
    else localStorage.setItem(ACTIVE_KEY, String(id))
  }

  async function create(name: string) {
    const company = await api<Company>('/companies/', { method: 'POST', body: { name } })
    companies.value.push(company)
    setActive(company.id)
    return company
  }

  return { companies, activeId, active, isOwner, load, setActive, create }
}
