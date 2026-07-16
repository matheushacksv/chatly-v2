from io import BytesIO

from django.db import IntegrityError
from django.db.models import ProtectedError, QuerySet
from ninja import UploadedFile
from pypdf import PdfReader

from apps.agents.models import Agent, Credential, Document
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
) -> Agent:
    credential = get_credential(company, credential_id)
    return Agent.objects.create(
        company=company,
        name=name,
        description=description if description else '',
        model=model,
        credential=credential,
        prompt=prompt,
        response_delay=response_delay,
        split_messages=split_messages,
    )


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
