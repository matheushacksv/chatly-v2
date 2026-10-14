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

/** Destino de uma coluna do CSV: telefone, nome, um campo personalizado, campo novo a criar ou ignorar */
export type CsvTarget = 'phone' | 'name' | 'skip' | `field:${string}` | `new:${string}`

export const phoneDigits = (cell = '') => {
  const digits = cell.replace(/\D/g, '')
  return digits.length >= 10 && digits.length <= 15 ? digits : null
}

/** Lê o CSV em células. Aceita "," ou ";" (Excel em pt-BR) e células entre aspas com separador dentro. */
export function readCsv(text: string): string[][] {
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/).filter(l => l.trim())
  const first = lines[0] ?? ''
  const sep = (first.match(/;/g)?.length ?? 0) > (first.match(/,/g)?.length ?? 0) ? ';' : ','
  return lines.map((line) => {
    const cells: string[] = []
    let cell = ''
    let quoted = false
    for (let i = 0; i < line.length; i++) {
      const ch = line[i]
      if (ch === '"' && quoted && line[i + 1] === '"') {
        cell += '"'
        i++
      } else if (ch === '"') {
        quoted = !quoted
      } else if (ch === sep && !quoted) {
        cells.push(cell.trim())
        cell = ''
      } else {
        cell += ch
      }
    }
    cells.push(cell.trim())
    return cells
  })
}

/** 1ª linha é cabeçalho quando nenhuma célula dela é telefone */
export const looksLikeHeader = (row: string[] = []) => row.length > 0 && !row.some(c => phoneDigits(c))

/**
 * Sugestão inicial do mapeamento: coluna com telefone (ou chamada telefone/numero/whatsapp) vira telefone,
 * "nome" vira nome, nome igual a um campo (variável ou rótulo) vira o campo. Resto: ignorar.
 */
export function suggestMapping(rows: string[][], hasHeader: boolean, fields: ContactField[]): CsvTarget[] {
  const width = Math.max(0, ...rows.slice(0, 20).map(r => r.length))
  const header = hasHeader ? rows[0] ?? [] : []
  const sample = rows.slice(hasHeader ? 1 : 0, 20)
  const byName = new Map(fields.flatMap(f => [[normalize(f.key), f.key], [normalize(f.label), f.key]]))
  const targets: CsvTarget[] = []
  for (let i = 0; i < width; i++) {
    const name = normalize(header[i] ?? '')
    const isPhoneCol = ['telefone', 'numero', 'whatsapp', 'celular', 'phone'].includes(name)
      || (sample.length > 0 && sample.filter(r => phoneDigits(r[i])).length >= sample.length / 2) // maioria é telefone
    if (isPhoneCol && !targets.includes('phone')) targets.push('phone')
    else if (name === 'nome' || name === 'name' || (!hasHeader && i === 1)) targets.push(targets.includes('name') ? 'skip' : 'name')
    else if (byName.has(name)) targets.push(`field:${byName.get(name)}`)
    else targets.push('skip')
  }
  return targets
}

/** Aplica o mapeamento: linha sem telefone válido vai pra invalid. Campos novos usam a variável que o backend devolveu. */
export function applyMapping(rows: string[][], hasHeader: boolean, targets: CsvTarget[], newKeys: Record<string, string> = {}) {
  const recipients: CsvRecipient[] = []
  const invalid: string[] = []
  for (const cells of rows.slice(hasHeader ? 1 : 0)) {
    const phoneCol = targets.indexOf('phone')
    const number = phoneDigits(cells[phoneCol])
    if (!number) {
      invalid.push(cells.join(' | '))
      continue
    }
    const row: CsvRecipient = { number, name: '', fields: {} }
    targets.forEach((target, i) => {
      const value = cells[i]?.trim() ?? ''
      if (target === 'name') row.name = value
      else if (target.startsWith('field:')) row.fields[target.slice(6)] = value
      else if (target.startsWith('new:') && newKeys[target]) row.fields[newKeys[target]!] = value
    })
    recipients.push(row)
  }
  return { rows: recipients, invalid }
}
