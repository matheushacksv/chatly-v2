import type { ContactField, CsvRecipient } from '~/types/api'

/** Campos personalizados da empresa ativa, compartilhados entre as páginas. */
export function useContactFields() {
  const api = useApi()
  const { activeId } = useCompany()
  const fields = useState<ContactField[]>('contact-fields', () => [])

  async function load() {
    fields.value = activeId.value ? await api<ContactField[]>(`/companies/${activeId.value}/contact-fields`) : []
  }

  // variáveis aceitas em templates e fluxos, na ordem do menu
  const variables = computed(() => [
    { label: 'Nome', key: 'nome' },
    { label: 'Telefone', key: 'telefone' },
    ...fields.value.map(f => ({ label: f.label, key: f.key }))
  ])

  return { fields, load, variables }
}

const normalize = (s: string) => s.trim().toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[\s-]+/g, '_')

/**
 * CSV de contatos. Sem cabeçalho: "numero,nome" por linha (formato antigo).
 * Com cabeçalho (1ª célula não é número): 1ª coluna é o número, "nome" é o nome e as outras colunas
 * casam com os campos personalizados pela variável ou pelo rótulo. Aceita "," ou ";" (Excel em pt-BR).
 */
export function parseContactsCsv(text: string, fields: ContactField[]) {
  // ponytail: split simples, sem aspas; célula com vírgula dentro quebra a linha
  const lines = text.split(/\r?\n/).filter(l => l.trim())
  const sep = (lines[0] ?? '').includes(';') && !(lines[0] ?? '').includes(',') ? ';' : ','
  const isNumber = (cell = '') => {
    const digits = cell.replace(/\D/g, '')
    return digits.length >= 10 && digits.length <= 15 ? digits : null
  }

  let columns: (string | null)[] = [null, 'nome'] // null = número; 'nome'; key de campo; '' = ignorada
  const matched: string[] = []
  const ignored: string[] = []
  const header = lines[0]?.split(sep) ?? []
  const hasHeader = header.length > 0 && !isNumber(header[0])
  if (hasHeader) {
    lines.shift()
    const byName = new Map(fields.flatMap(f => [[normalize(f.key), f.key], [normalize(f.label), f.key]]))
    columns = header.map((cell, i) => {
      if (i === 0) return null
      const name = normalize(cell)
      if (name === 'nome') return 'nome'
      const key = byName.get(name)
      if (key) matched.push(fields.find(f => f.key === key)!.label)
      else if (cell.trim()) ignored.push(cell.trim())
      return key ?? ''
    })
  }

  const rows: CsvRecipient[] = []
  const invalid: string[] = []
  for (const line of lines) {
    const cells = line.split(sep)
    const number = isNumber(cells[0])
    if (!number) {
      invalid.push(line.trim())
      continue
    }
    const row: CsvRecipient = { number, name: '', fields: {} }
    cells.forEach((cell, i) => {
      const col = columns[i]
      if (col === 'nome') row.name = hasHeader ? cell.trim() : cells.slice(1).join(sep).trim()
      else if (col) row.fields[col] = cell.trim()
    })
    rows.push(row)
  }
  return { rows, invalid, matched, ignored }
}
