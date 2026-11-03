import re

import httpx
from agno.agent import Agent as AgnoAgent
from agno.media import Image
from agno.models.anthropic import Claude
from agno.models.message import Message
from agno.models.openai import OpenAIResponses
from agno.run.base import RunStatus
from django.conf import settings
from django.utils import timezone
from openai import OpenAI
from pgvector.django import CosineDistance

from apps.agents.models import Agent, DocumentChunk

GROQ_STT_URL = 'https://api.groq.com/openai/v1/audio/transcriptions'
EMBEDDING_MODEL = 'text-embedding-3-small'
MEMORY_MAX_CHARS = 4000
MEMORY_PROMPT = (
    'Você mantém anotações sobre um cliente de atendimento via WhatsApp. '
    'Reescreva as anotações incorporando fatos novos e duráveis da conversa recente '
    '(nome, preferências, contexto de compra, decisões). Máximo 20 linhas. '
    'Não inclua opiniões nem transcrição da conversa. Responda apenas com as anotações. '
)
VAR_RE = re.compile(r'\{\{\s*(\w+)\s*\}\}')
WEEKDAYS = ('segunda-feira', 'terça-feira', 'quarta-feira', 'quinta-feira', 'sexta-feira', 'sábado', 'domingo')

SPLIT_PROMPT = (
    '\n\nDivida sua resposta em mensagens curtas de WhatsApp separadas por uma linha em branco. '
    'Cada bloco vira uma mensagem separada no chat. Use de 1 a 4 mensagens, como uma pessoa digitando.'
)

HANDOFF_PROMPT = (
    '\n\nTransferência para atendente humano (ferramenta transferir_para_atendente):\n'
    '- Transfira quando o cliente pedir para falar com uma pessoa, quando estiver irritado ou reclamando, '
    'ou quando você não tiver a informação para responder com segurança. Nunca invente preço, prazo ou política.\n'
    '- Se não estiver claro que o cliente quer um humano, pergunte antes se ele prefere falar com um atendente.\n'
    '- Ao transferir, avise o cliente na mesma resposta que um atendente vai continuar a conversa, sem prometer horário.'
)


def _run(agno_agent: AgnoAgent, *args, **kwargs) -> str:
    result = agno_agent.run(*args, **kwargs)
    if result.status == RunStatus.error:
        raise RuntimeError(f'model run failed: {result.content}')
    return result.content or ''


class _Responses(OpenAIResponses):
    # Agno só reconhece gpt-5 como modelo com raciocínio; o gpt-6 precisa do mesmo encadeamento nas ferramentas
    def _using_reasoning_model(self) -> bool:
        return self.id.startswith('gpt-6') or super()._using_reasoning_model()


def _build_model(agent: Agent) -> Claude | OpenAIResponses:
    credential = agent.credential
    model_id = agent.model
    if credential.provider == 'openai':
        return _Responses(id=model_id, api_key=credential.api_key)
    return Claude(id=model_id, api_key=credential.api_key)


def _knowledge_context(agent: Agent, text: str) -> str:
    chunks = DocumentChunk.objects.filter(document__agent=agent, document__status='ready')
    if not chunks.exists():
        return ''
    vector = embed_text([text])[0]
    top = chunks.order_by(CosineDistance('embedding', vector))[:4]
    return '\n\n'.join(chunk.content for chunk in top)


def embed_text(texts: list[str]) -> list[list[float]]:
    client = OpenAI(api_key=settings.EMBEDDING_API_KEY)
    embeddings = []
    for start in range(0, len(texts), 100):
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts[start : start + 100])
        embeddings.extend(item.embedding for item in response.data)
    return embeddings


def render_prompt(prompt: str, variables: dict[str, str]) -> str:
    """Troca {{nome}}, {{telefone}} e {{campo}} pelos dados do contato. Campo vazio vira "não informado";
    o que não é variável conhecida (ex.: {{1}} citado num exemplo) fica como está."""
    return VAR_RE.sub(lambda m: (variables[m.group(1)] or 'não informado') if m.group(1) in variables else m.group(0), prompt)


def generate_reply(
    agent: Agent, history: list[dict], text: str, image: bytes | None = None, mime_type: str = '', memory='', tools=None, variables=None
) -> str:
    now = timezone.localtime()
    # sem isso a IA não sabe que dia é hoje e erra "segunda que vem", "depois do dia 14"
    system_message = render_prompt(agent.prompt, variables or {})
    system_message += f'\n\nAgora: {WEEKDAYS[now.weekday()]}, {now:%d/%m/%Y %H:%M} (horário de Brasília).'
    if memory:
        system_message += f'\n\nAnotações sobre este cliente:\n<memory>\n{memory}\n</memory>'
    context = _knowledge_context(agent, text)
    if context:
        system_message += f'\n\nUse as informações abaixo quando forem relevantes:\n<knowledge>\n{context}\n</knowledge>'
    if agent.split_messages:
        system_message += SPLIT_PROMPT
    if any(getattr(t, '__name__', '') == 'transferir_para_atendente' for t in tools or []):
        system_message += HANDOFF_PROMPT
    agno_agent = AgnoAgent(
        model=_build_model(agent),
        system_message=system_message,
        additional_input=[Message(role=m['role'], content=m['content']) for m in history],
        tools=tools,
    )
    images = [Image(content=image, mime_type=mime_type)] if image else None
    return _run(agno_agent, text, images=images)


def transcribe_audio(content: bytes, mime_type: str) -> str:
    response = httpx.post(
        GROQ_STT_URL,
        headers={'Authorization': f'Bearer {settings.GROQ_API_KEY}'},
        files={'file': ('audio.ogg', content, mime_type)},
        data={'model': 'whisper-large-v3-turbo'},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()['text']


def update_memory(agent: Agent, memory: str, history: list[dict]) -> str:
    conversation = '\n'.join(f'{m["role"]}: {m["content"]}' for m in history)
    agno_agent = AgnoAgent(model=_build_model(agent), system_message=MEMORY_PROMPT)
    prompt = f'<anotacoes_atuais>\n{memory}\n</anotacoes_atuais>\n\n<conversa_recente>\n{conversation}\n</conversa_recente>'
    return _run(agno_agent, prompt)[:MEMORY_MAX_CHARS]
