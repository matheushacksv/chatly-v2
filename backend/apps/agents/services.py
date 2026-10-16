import json
import re
from datetime import datetime, time, timedelta
from io import BytesIO
from urllib.parse import quote

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, QuerySet
from django.utils import timezone
from ninja import UploadedFile
from pypdf import PdfReader

from apps.agents.models import Agent, AgentTool, Credential, Document
from apps.companies.models import Company
from core.exceptions import Conflict, NotFound, ValidationError

MAX_DOCUMENT_SIZE = 10 * 1024 * 1024  # 10MB
CHUNK_SIZE = 1200  # caracteres

# * ------ Helpers ------


def _extract_text(name: str, data: bytes) -> str:
    if name.lower().endswith('.pdf'):
        reader = PdfReader(BytesIO(data))
        return '\n'.join(page.extract_text() or '' for page in reader.pages)
    return data.decode('utf-8', errors='ignore')


def split_text(text: str) -> list[str]:
    chunks, current = [], ''
    for paragraph in text.split('\n\n'):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(current) + len(paragraph) > CHUNK_SIZE and current:
            chunks.append(current)
            current = ''
        while len(paragraph) > CHUNK_SIZE:
            chunks.append(paragraph[:CHUNK_SIZE])
            paragraph = paragraph[CHUNK_SIZE:]
        current = f'{current}\n\n{paragraph}'.strip() if current else paragraph
    if current:
        chunks.append(current)
    return chunks


# * ------ Credentials ------


def create_credential(company: Company, provider: str, api_key: str) -> Credential:
    try:
        return Credential.objects.create(company=company, provider=provider, api_key=api_key)
    except IntegrityError:
        raise Conflict('credential for this provider already exists')


def get_credential(company: Company, credential_id: int) -> Credential:
    credential = Credential.objects.filter(company=company, pk=credential_id).first()
    if credential is None:
        raise NotFound('credential not found')
    return credential


def list_credentials(company: Company) -> QuerySet[Credential]:
    return Credential.objects.filter(company=company)


def update_credential(
    company: Company,
    credential_id: int,
    api_key: str,
) -> Credential:
    credential = get_credential(company, credential_id)
    credential.api_key = api_key
    credential.save(update_fields=['api_key'])
    return credential


def delete_credential(company: Company, credential_id: int) -> None:
    credential = get_credential(company, credential_id)
    try:
        credential.delete()
    except ProtectedError:
        raise Conflict('credential is in use by agents')


# * ------ Agent ------


def create_agent(
    company: Company,
    credential_id: int,
    name: str,
    model: str,
    prompt: str,
    description: str = '',
    response_delay: int = 0,
    split_messages: bool = False,
    handoff_resume_hours: int = 24,
    ai_schedule: list | None = None,
    native_tools: list[str] | None = None,
) -> Agent:
    credential = get_credential(company, credential_id)
    agent = Agent(
        company=company,
        name=name,
        description=description if description else '',
        model=model,
        credential=credential,
        prompt=prompt,
        response_delay=response_delay,
        split_messages=split_messages,
        handoff_resume_hours=handoff_resume_hours,
        ai_schedule=_clean_schedule(ai_schedule),
        native_tools=['handoff', 'pipedrive'] if native_tools is None else list(dict.fromkeys(native_tools)),
    )
    agent.save()
    return agent


def _clean_schedule(schedule: list | None) -> list | None:
    """Normaliza pra [[["HH:MM", "HH:MM"], ...] x 7]. Faixa não vira a meia-noite: fim "00:00" = fim do dia."""
    if schedule is None:
        return None
    if len(schedule) != 7:
        raise ValidationError('ai schedule needs 7 days')
    clean = []
    for ranges in schedule:
        day = []
        for start, end in ranges:
            start, end = time.fromisoformat(str(start)), time.fromisoformat(str(end))
            if end <= start and end != time(0):
                raise ValidationError('ai hours end must be after start (use 00:00 for midnight)')
            day.append([f'{start:%H:%M}', f'{end:%H:%M}'])
        clean.append(sorted(day))
    if not any(clean):
        raise ValidationError('ai schedule needs at least one range')
    return clean


def next_ai_opening(agent: Agent, now: datetime) -> datetime | None:
    # None = IA atende agora; senão, quando abre a próxima faixa
    if not agent.ai_schedule:
        return None
    now = timezone.localtime(now)
    for offset in range(8):
        day = now.date() + timedelta(days=offset)
        for start, end in agent.ai_schedule[day.weekday()]:
            opens = datetime.combine(day, time.fromisoformat(start), tzinfo=now.tzinfo)
            closes = datetime.combine(day + timedelta(days=1 if end == '00:00' else 0), time.fromisoformat(end), tzinfo=now.tzinfo)
            if now < opens:
                return opens
            if now < closes:
                return None
    return None


def get_agent(company: Company, agent_id: int) -> Agent:
    agent = Agent.objects.filter(company=company, pk=agent_id).first()
    if agent is None:
        raise NotFound('agent not found')
    return agent


def list_agents(company: Company) -> QuerySet[Agent]:
    return Agent.objects.filter(company=company)


def update_agent(company: Company, agent_id: int, data: dict) -> Agent:
    agent = get_agent(company, agent_id)
    credential_id = data.pop('credential_id', None)
    if credential_id is not None:
        agent.credential = get_credential(company, credential_id)

    if data.get('native_tools') is not None:
        data['native_tools'] = list(dict.fromkeys(data['native_tools']))
    if 'ai_schedule' in data:
        data['ai_schedule'] = _clean_schedule(data['ai_schedule'])
    for field, value in data.items():
        setattr(agent, field, value)
    agent.save()
    return agent


def delete_agent(company: Company, agent_id: int) -> None:
    agent = get_agent(company, agent_id)
    try:
        agent.delete()
    except ProtectedError:
        raise Conflict('agent is in use by whatsapp accounts')


# * ------ RAG ------


def create_document(company: Company, agent_id: int, file: UploadedFile) -> Document:
    agent = get_agent(company, agent_id)
    name = file.name or ''

    if not name.lower().endswith(('.pdf', '.txt', '.md')):
        raise ValidationError('unsupported file type (pdf, txt, md)')

    if (file.size or 0) > MAX_DOCUMENT_SIZE:
        raise ValidationError('file too large (max 10MB)')

    content = _extract_text(name, file.read())
    if not content.strip():
        raise ValidationError('no extractable text in file')

    document = Document.objects.create(agent=agent, name=name, content=content)

    from apps.agents.tasks import process_document

    process_document.delay(document.pk)

    return document


def get_document(company: Company, agent_id: int, document_id) -> Document:
    agent = get_agent(company, agent_id)
    document = Document.objects.filter(pk=document_id, agent=agent).first()
    if document is None:
        raise NotFound('document not found')
    return document


def list_documents(company, agent_id: int) -> QuerySet[Document]:
    agent = get_agent(company, agent_id)
    return Document.objects.filter(agent=agent)


def delete_document(company, agent_id: int, document_id: int) -> None:
    document = get_document(company, agent_id, document_id)
    document.delete()


# * ------ Ferramentas personalizadas (HTTP) ------

TOOL_NAME_RE = re.compile(r'^[a-z][a-z0-9_]{1,63}$')
VAR_RE = re.compile(r'\{\{\s*(\w+)\s*\}\}')
# nomes das ferramentas nativas (apps/chats/ai_tools.py): uma personalizada não pode esconder uma delas
RESERVED_TOOL_NAMES = {'transferir_para_atendente', 'agendar_retomada', 'salvar_dado_contato', 'etiquetar_contato', 'criar_negocio', 'anotar_crm'}
TOOL_METHODS = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')


def list_agent_tools(company: Company, agent_id: int) -> QuerySet[AgentTool]:
    return AgentTool.objects.filter(agent=get_agent(company, agent_id)).order_by('name')


def get_agent_tool(company: Company, agent_id: int, tool_id: int) -> AgentTool:
    tool = AgentTool.objects.filter(agent=get_agent(company, agent_id), pk=tool_id).first()
    if tool is None:
        raise NotFound('tool not found')
    return tool


def _apply_tool_data(tool: AgentTool, data: dict) -> None:
    if 'secret_headers' in data:
        # valor vazio = mantém o segredo já salvo (o front nunca recebe o valor de volta)
        current = json.loads(tool.secret_headers or '{}')
        tool.secret_headers = json.dumps({
            h['key'].strip(): h['value'] or current.get(h['key'].strip(), '')
            for h in data.pop('secret_headers') if h['key'].strip()
        })
    for field, value in data.items():
        setattr(tool, field, value)
    if not TOOL_NAME_RE.match(tool.name):
        raise ValidationError('nome da ferramenta: letras minúsculas, números e _, começando com letra')
    if tool.name in RESERVED_TOOL_NAMES:
        raise ValidationError(f'{tool.name} é o nome de uma ferramenta nativa')
    names = [p['name'] for p in tool.parameters]
    if any(not TOOL_NAME_RE.match(n) for n in names) or len(set(names)) != len(names):
        raise ValidationError('parâmetros: nomes únicos com letras minúsculas, números e _')
    if tool.method not in TOOL_METHODS:
        raise ValidationError(f'método inválido: {tool.method}')
    if not tool.url.startswith('https://'):
        raise ValidationError('a URL precisa começar com https://')
    if not tool.description.strip():
        raise ValidationError('descreva quando a IA deve usar a ferramenta')


def create_agent_tool(company: Company, agent_id: int, data: dict) -> AgentTool:
    tool = AgentTool(agent=get_agent(company, agent_id))
    _apply_tool_data(tool, data)
    try:
        with transaction.atomic():
            tool.save()
    except IntegrityError:
        raise Conflict(f'já existe uma ferramenta {tool.name} nesse agente')
    return tool


def update_agent_tool(company: Company, agent_id: int, tool_id: int, data: dict) -> AgentTool:
    tool = get_agent_tool(company, agent_id, tool_id)
    _apply_tool_data(tool, data)
    try:
        with transaction.atomic():
            tool.save()
    except IntegrityError:
        raise Conflict(f'já existe uma ferramenta {tool.name} nesse agente')
    return tool


def delete_agent_tool(company: Company, agent_id: int, tool_id: int) -> None:
    get_agent_tool(company, agent_id, tool_id).delete()


def run_agent_tool(tool: AgentTool, arguments: dict, variables: dict[str, str]) -> tuple[bool, str]:
    """Executa a chamada com os argumentos da IA + dados do contato. Nunca levanta: a falha volta como texto pra IA."""
    from apps.automations import outbound

    values = {**variables, **{k: '' if v is None else str(v) for k, v in arguments.items()}}

    def render(text: str, escape=lambda v: v) -> str:
        return VAR_RE.sub(lambda m: escape(values[m.group(1)]) if m.group(1) in values else m.group(0), text or '')

    headers = {h['key'].strip(): render(h.get('value', '')) for h in tool.headers if (h.get('key') or '').strip()}
    headers |= json.loads(tool.secret_headers or '{}')
    if tool.body and not any(k.lower() == 'content-type' for k in headers):
        headers['Content-Type'] = 'application/json'
    is_json = any(k.lower() == 'content-type' and 'json' in v.lower() for k, v in headers.items())
    # dentro de JSON, aspas e quebras de linha do valor não podem quebrar o documento
    body = render(tool.body, lambda v: json.dumps(v)[1:-1]) if is_json else render(tool.body)
    try:
        status, text = outbound.fetch(tool.method, render(tool.url, lambda v: quote(v, safe='')), headers, body)
    except Exception as exc:  # URL recusada, timeout, DNS
        return False, f'Falha ao chamar: {exc}'
    return 200 <= status < 300, f'HTTP {status}\n{text}'
