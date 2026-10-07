from datetime import datetime, time
from io import BytesIO
from zoneinfo import ZoneInfo
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase

from apps import ai
from apps.accounts.models import User
from apps.agents import services, tasks
from apps.agents.models import Agent, Credential, Document, DocumentChunk
from apps.companies.models import Company
from apps.testing import auth_header


def minimal_pdf(text: str) -> bytes:
    """PDF de uma página com `text` (ASCII, sem parênteses), xref válido para o pypdf."""
    stream = f'BT /F1 12 Tf 72 720 Td ({text}) Tj ET'.encode()
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
        b'<< /Length %d >>\nstream\n%s\nendstream' % (len(stream), stream),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    out = BytesIO()
    out.write(b'%PDF-1.4\n')
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b'%d 0 obj\n%s\nendobj\n' % (number, obj))
    xref_position = out.tell()
    out.write(b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1))
    for offset in offsets:
        out.write(b'%010d 00000 n \n' % offset)
    out.write(b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n' % (len(objects) + 1, xref_position))
    out.write(b'%%EOF')
    return out.getvalue()


def vec(*components: float) -> list[float]:
    """Vetor 1536-dim com os primeiros componentes dados e o resto zero."""
    return list(components) + [0.0] * (1536 - len(components))


class BaseAgentsTestCase(TestCase):
    """Fixture de dois tenants completa: user + company + credential + agent de cada lado."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='owner@a.com', password='strongpass123')
        cls.other_user = User.objects.create_user(email='other@a.com', password='strongpass123')
        cls.company = Company.objects.create(name='Minha Empresa', owner=cls.user)
        cls.other_company = Company.objects.create(name='Empresa Alheia', owner=cls.other_user)
        cls.credential = Credential.objects.create(
            company=cls.company, provider='anthropic', api_key='sk-minha-chave-secreta'
        )
        cls.other_credential = Credential.objects.create(
            company=cls.other_company, provider='anthropic', api_key='sk-chave-alheia'
        )
        cls.agent = Agent.objects.create(
            company=cls.company, name='Vendedor', prompt='Você vende.', credential=cls.credential,
            model='claude-opus-4-8',
        )
        cls.other_agent = Agent.objects.create(
            company=cls.other_company, name='Alheio', prompt='...', credential=cls.other_credential,
            model='claude-opus-4-8',
        )

    def setUp(self):
        self.auth = auth_header(self.user)


class CredentialTests(BaseAgentsTestCase):
    def url(self, company_id: int, credential_id: int | None = None) -> str:
        base = f'/api/companies/{company_id}/credentials'
        return f'{base}/{credential_id}' if credential_id else base

    # create

    def test_create_credential(self):
        response = self.client.post(
            self.url(self.company.pk),
            data={'provider': 'openai', 'api_key': 'sk-nova'},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Credential.objects.filter(company=self.company, provider='openai').exists())

    def test_create_credential_does_not_leak_api_key(self):
        response = self.client.post(
            self.url(self.company.pk),
            data={'provider': 'openai', 'api_key': 'sk-nova'},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertNotIn('api_key', response.json())
        self.assertNotIn('sk-nova', response.content.decode())

    def test_create_duplicate_provider_returns_409(self):
        response = self.client.post(
            self.url(self.company.pk),
            data={'provider': 'anthropic', 'api_key': 'sk-outra'},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 409)

    def test_create_same_provider_in_another_company_is_allowed(self):
        # unique é por company, não global - other_company já tem anthropic e isso não bloqueia a minha
        company2 = Company.objects.create(name='Minha Segunda', owner=self.user)
        response = self.client.post(
            self.url(company2.pk),
            data={'provider': 'anthropic', 'api_key': 'sk-segunda'},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 201)

    def test_create_invalid_provider_returns_422(self):
        response = self.client.post(
            self.url(self.company.pk),
            data={'provider': 'gemini', 'api_key': 'sk-x'},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 422)

    def test_create_in_foreign_company_returns_404(self):
        response = self.client.post(
            self.url(self.other_company.pk),
            data={'provider': 'openai', 'api_key': 'sk-invasao'},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Credential.objects.filter(company=self.other_company, provider='openai').exists())

    # list / detail

    def test_list_credentials_only_mine_and_without_api_key(self):
        response = self.client.get(self.url(self.company.pk), headers=self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]['provider'], 'anthropic')
        self.assertNotIn('api_key', body[0])

    def test_get_foreign_credential_returns_404(self):
        response = self.client.get(self.url(self.company.pk, self.other_credential.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)

    # update

    def test_update_credential_rotates_api_key(self):
        response = self.client.patch(
            self.url(self.company.pk, self.credential.pk),
            data={'api_key': 'sk-rotacionada'},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.credential.refresh_from_db()
        self.assertEqual(self.credential.api_key, 'sk-rotacionada')

    # delete

    def test_delete_credential(self):
        unused = Credential.objects.create(company=self.company, provider='openai', api_key='sk-sem-uso')
        response = self.client.delete(self.url(self.company.pk, unused.pk), headers=self.auth)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Credential.objects.filter(pk=unused.pk).exists())

    def test_delete_credential_in_use_returns_409(self):
        response = self.client.delete(self.url(self.company.pk, self.credential.pk), headers=self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Credential.objects.filter(pk=self.credential.pk).exists())

    # criptografia

    def test_api_key_is_encrypted_at_rest(self):
        with connection.cursor() as cursor:
            cursor.execute('SELECT api_key FROM agents_credential WHERE id = %s', [self.credential.pk])
            raw = cursor.fetchone()[0]
        self.assertNotIn('sk-minha-chave-secreta', raw)

    def test_api_key_decrypts_on_read(self):
        self.credential.refresh_from_db()
        self.assertEqual(self.credential.api_key, 'sk-minha-chave-secreta')


class AgentTests(BaseAgentsTestCase):
    def url(self, company_id: int, agent_id: int | None = None) -> str:
        base = f'/api/companies/{company_id}/agents'
        return f'{base}/{agent_id}' if agent_id else base

    def agent_payload(self, **overrides) -> dict:
        payload = {
            'name': 'Suporte',
            'credential_id': self.credential.pk,
            'model': 'claude-opus-4-8',
            'prompt': 'Você atende clientes.',
        }
        payload.update(overrides)
        return payload

    # create

    def test_create_agent(self):
        response = self.client.post(
            self.url(self.company.pk),
            data=self.agent_payload(),
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 201)
        agent = Agent.objects.get(company=self.company, name='Suporte')
        self.assertEqual(agent.credential, self.credential)

    def test_create_agent_with_foreign_credential_returns_404(self):
        # cross-tenant: credencial de outra company não pode ser anexada
        response = self.client.post(
            self.url(self.company.pk),
            data=self.agent_payload(credential_id=self.other_credential.pk),
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Agent.objects.filter(company=self.company, name='Suporte').exists())

    def test_create_agent_with_nonexistent_credential_returns_404(self):
        response = self.client.post(
            self.url(self.company.pk),
            data=self.agent_payload(credential_id=99999),
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 404)

    def test_create_agent_without_model_returns_422(self):
        # sem default de modelo: escolha é obrigação do tenant no cadastro
        payload = self.agent_payload()
        del payload['model']
        response = self.client.post(
            self.url(self.company.pk),
            data=payload,
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 422)

    def test_create_agent_with_response_delay(self):
        response = self.client.post(
            self.url(self.company.pk),
            data=self.agent_payload(response_delay=8),
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['response_delay'], 8)
        self.assertEqual(Agent.objects.get(company=self.company, name='Suporte').response_delay, 8)

    def test_create_agent_defaults_response_delay_to_zero(self):
        # 0 = responde imediato, comportamento pré-debounce
        self.client.post(
            self.url(self.company.pk),
            data=self.agent_payload(),
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(Agent.objects.get(company=self.company, name='Suporte').response_delay, 0)

    def test_create_agent_with_negative_response_delay_returns_422(self):
        # sem o ge=0 do DTO, negativo estoura o check constraint do Postgres → 500
        response = self.client.post(
            self.url(self.company.pk),
            data=self.agent_payload(response_delay=-1),
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 422)

    def test_create_agent_defaults_split_messages_to_false(self):
        # off = comportamento antigo, resposta em bloco único
        response = self.client.post(
            self.url(self.company.pk),
            data=self.agent_payload(),
            content_type='application/json',
            headers=self.auth,
        )
        self.assertFalse(response.json()['split_messages'])
        self.assertFalse(Agent.objects.get(company=self.company, name='Suporte').split_messages)

    def test_create_agent_with_split_messages(self):
        response = self.client.post(
            self.url(self.company.pk),
            data=self.agent_payload(split_messages=True),
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()['split_messages'])
        self.assertTrue(Agent.objects.get(company=self.company, name='Suporte').split_messages)

    def test_create_agent_handoff_resume_hours(self):
        response = self.client.post(self.url(self.company.pk), data=self.agent_payload(), content_type='application/json', headers=self.auth)
        self.assertEqual(response.json()['handoff_resume_hours'], 24)  # padrão

        response = self.client.post(
            self.url(self.company.pk), data=self.agent_payload(name='Sem volta', handoff_resume_hours=0),
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(Agent.objects.get(company=self.company, name='Sem volta').handoff_resume_hours, 0)

    def test_create_agent_negative_handoff_resume_hours_returns_422(self):
        response = self.client.post(
            self.url(self.company.pk), data=self.agent_payload(handoff_resume_hours=-1),
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 422)

    # list / detail

    def test_list_agents_only_mine(self):
        response = self.client.get(self.url(self.company.pk), headers=self.auth)
        self.assertEqual(response.status_code, 200)
        names = [agent['name'] for agent in response.json()]
        self.assertEqual(names, ['Vendedor'])

    def test_get_foreign_agent_returns_404(self):
        response = self.client.get(self.url(self.company.pk, self.other_agent.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)

    # update

    def test_update_agent_partial_keeps_unsent_fields(self):
        # PATCH só com name não pode apagar prompt/credential (exclude_unset)
        response = self.client.patch(
            self.url(self.company.pk, self.agent.pk),
            data={'name': 'Vendedor 2.0'},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.name, 'Vendedor 2.0')
        self.assertEqual(self.agent.prompt, 'Você vende.')
        self.assertEqual(self.agent.credential, self.credential)

    def test_update_agent_response_delay(self):
        response = self.client.patch(
            self.url(self.company.pk, self.agent.pk),
            data={'response_delay': 15},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.response_delay, 15)

    def test_create_agent_ai_hours(self):
        response = self.client.post(
            self.url(self.company.pk), data=self.agent_payload(), content_type='application/json', headers=self.auth,
        )
        # padrão: sempre atende, todos os dias
        self.assertEqual(response.json()['ai_hours_start'], None)
        self.assertEqual(response.json()['ai_days'], [0, 1, 2, 3, 4, 5, 6])

        response = self.client.post(
            self.url(self.company.pk),
            data=self.agent_payload(name='Noturno', ai_hours_start='18:00', ai_hours_end='09:00', ai_days=[0, 1, 2, 3, 4]),
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 201, response.json())
        agent = Agent.objects.get(company=self.company, name='Noturno')
        self.assertEqual((agent.ai_hours_start, agent.ai_hours_end, agent.ai_days), (time(18), time(9), [0, 1, 2, 3, 4]))

    def test_ai_hours_need_start_and_end(self):
        response = self.client.post(
            self.url(self.company.pk), data=self.agent_payload(ai_hours_start='09:00'),
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Agent.objects.filter(company=self.company, name='Suporte').exists())

        # patch parcial também: o fim que falta é o do banco
        response = self.client.patch(
            self.url(self.company.pk, self.agent.pk), data={'ai_hours_end': '18:00'},
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 400)
        self.agent.refresh_from_db()
        self.assertIsNone(self.agent.ai_hours_end)

    def test_ai_days_invalid_returns_422(self):
        for days in ([], [7], [-1]):
            response = self.client.patch(
                self.url(self.company.pk, self.agent.pk), data={'ai_days': days},
                content_type='application/json', headers=self.auth,
            )
            self.assertEqual(response.status_code, 422, days)

    def test_update_agent_ai_hours_and_clear(self):
        url = self.url(self.company.pk, self.agent.pk)
        response = self.client.patch(
            url, data={'ai_hours_start': '09:00', 'ai_hours_end': '18:00'}, content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()['ai_hours_start'], '09:00:00')

        response = self.client.patch(
            url, data={'ai_hours_start': None, 'ai_hours_end': None}, content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.agent.refresh_from_db()
        self.assertIsNone(self.agent.ai_hours_start)

    def test_update_agent_split_messages_toggle(self):
        for value in (True, False):
            response = self.client.patch(
                self.url(self.company.pk, self.agent.pk),
                data={'split_messages': value},
                content_type='application/json',
                headers=self.auth,
            )
            self.assertEqual(response.status_code, 200)
            self.agent.refresh_from_db()
            self.assertEqual(self.agent.split_messages, value)

    def test_update_agent_with_foreign_credential_returns_404(self):
        response = self.client.patch(
            self.url(self.company.pk, self.agent.pk),
            data={'credential_id': self.other_credential.pk},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 404)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.credential, self.credential)

    # delete

    def test_delete_agent(self):
        response = self.client.delete(self.url(self.company.pk, self.agent.pk), headers=self.auth)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Agent.objects.filter(pk=self.agent.pk).exists())

    def test_delete_foreign_agent_returns_404(self):
        response = self.client.delete(self.url(self.company.pk, self.other_agent.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Agent.objects.filter(pk=self.other_agent.pk).exists())


SP = ZoneInfo('America/Sao_Paulo')


def at(day: int, hour: int, minute: int = 0) -> datetime:
    # 2026-10-05 é segunda: at(0, ...) = segunda, at(4, ...) = sexta
    return datetime(2026, 10, 5 + day, hour, minute, tzinfo=SP)


class NextAiOpeningTests(TestCase):
    def agent(self, start, end, days=(0, 1, 2, 3, 4, 5, 6)) -> Agent:
        return Agent(ai_hours_start=start, ai_hours_end=end, ai_days=list(days))

    def test_without_hours_always_open(self):
        self.assertIsNone(services.next_ai_opening(self.agent(None, None), at(0, 3)))

    def test_daytime_window(self):
        agent = self.agent(time(9), time(18))
        # gatilho 23h: atende às 09h do dia seguinte
        self.assertEqual(services.next_ai_opening(agent, at(0, 23)), at(1, 9))
        self.assertEqual(services.next_ai_opening(agent, at(1, 3)), at(1, 9))
        self.assertIsNone(services.next_ai_opening(agent, at(1, 9)))
        self.assertIsNone(services.next_ai_opening(agent, at(1, 17, 59)))
        self.assertEqual(services.next_ai_opening(agent, at(1, 18)), at(2, 9))

    def test_overnight_window(self):
        agent = self.agent(time(18), time(9))
        # gatilho 14h, IA a partir das 18h
        self.assertEqual(services.next_ai_opening(agent, at(0, 14)), at(0, 18))
        self.assertIsNone(services.next_ai_opening(agent, at(0, 23)))
        self.assertIsNone(services.next_ai_opening(agent, at(1, 8, 59)))
        self.assertEqual(services.next_ai_opening(agent, at(1, 9)), at(1, 18))

    def test_days(self):
        weekdays = self.agent(time(9), time(18), days=range(5))
        # sexta 19h e sábado: só segunda 09h
        self.assertEqual(services.next_ai_opening(weekdays, at(4, 19)), at(7, 9))
        self.assertEqual(services.next_ai_opening(weekdays, at(5, 12)), at(7, 9))

        # janela de sexta que vira a meia-noite vale até sábado 09h, mesmo sábado fora dos dias
        overnight = self.agent(time(18), time(9), days=range(5))
        self.assertIsNone(services.next_ai_opening(overnight, at(5, 8)))
        self.assertEqual(services.next_ai_opening(overnight, at(5, 10)), at(7, 18))

    def test_same_start_and_end_is_whole_day(self):
        agent = self.agent(time(0), time(0), days=[0])
        self.assertIsNone(services.next_ai_opening(agent, at(0, 23, 59)))
        self.assertEqual(services.next_ai_opening(agent, at(1, 0)), at(7, 0))

    def test_converts_utc_to_local_time(self):
        agent = self.agent(time(9), time(18))
        # 11h UTC = 08h em São Paulo: ainda fechado
        self.assertEqual(services.next_ai_opening(agent, at(0, 8).astimezone(ZoneInfo('UTC'))), at(0, 9))


class SplitTextTests(TestCase):
    def test_groups_small_paragraphs_into_one_chunk(self):
        chunks = services.split_text('primeiro parágrafo\n\nsegundo parágrafo')
        self.assertEqual(chunks, ['primeiro parágrafo\n\nsegundo parágrafo'])

    def test_starts_new_chunk_when_size_exceeded(self):
        first, second = 'a' * 700, 'b' * 700
        chunks = services.split_text(f'{first}\n\n{second}')
        self.assertEqual(chunks, [first, second])

    def test_slices_paragraph_bigger_than_chunk_size(self):
        chunks = services.split_text('a' * 3000)
        self.assertEqual([len(chunk) for chunk in chunks], [1200, 1200, 600])

    def test_ignores_blank_paragraphs(self):
        self.assertEqual(services.split_text('a\n\n\n\n\n\nb'), ['a\n\nb'])

    def test_empty_text_returns_no_chunks(self):
        self.assertEqual(services.split_text('   \n\n  '), [])


class DocumentEndpointTests(BaseAgentsTestCase):
    def setUp(self):
        super().setUp()
        # sem o mock, o upload publica task no broker real
        patcher = patch('apps.agents.tasks.process_document.delay')
        self.mock_enqueue = patcher.start()
        self.addCleanup(patcher.stop)

    def url(self, agent_id: int, document_id: int | None = None) -> str:
        base = f'/api/companies/{self.company.pk}/agents/{agent_id}/documents'
        return f'{base}/{document_id}' if document_id else base

    def upload(self, agent_id: int, filename: str, data: bytes):
        file = SimpleUploadedFile(filename, data)
        return self.client.post(self.url(agent_id), data={'file': file}, headers=self.auth)

    # upload

    def test_upload_txt_document(self):
        response = self.upload(self.agent.pk, 'faq.txt', 'Horário: 9h às 18h.'.encode())
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['name'], 'faq.txt')
        self.assertEqual(body['status'], 'processing')
        document = Document.objects.get(agent=self.agent)
        self.assertEqual(document.content, 'Horário: 9h às 18h.')
        self.mock_enqueue.assert_called_once_with(document.pk)

    def test_upload_pdf_document_extracts_text(self):
        response = self.upload(self.agent.pk, 'catalogo.pdf', minimal_pdf('Entrega em todo o Brasil'))
        self.assertEqual(response.status_code, 201)
        document = Document.objects.get(agent=self.agent)
        self.assertIn('Entrega em todo o Brasil', document.content)

    def test_upload_output_does_not_leak_content(self):
        # content pode ter centenas de KB - fica fora do DTO
        response = self.upload(self.agent.pk, 'faq.txt', b'conteudo')
        self.assertNotIn('content', response.json())

    def test_upload_unsupported_extension_returns_400(self):
        response = self.upload(self.agent.pk, 'planilha.xlsx', b'dados')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Document.objects.count(), 0)
        self.mock_enqueue.assert_not_called()

    def test_upload_too_large_returns_400(self):
        response = self.upload(self.agent.pk, 'grande.txt', b'a' * (services.MAX_DOCUMENT_SIZE + 1))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Document.objects.count(), 0)

    def test_upload_without_extractable_text_returns_400(self):
        response = self.upload(self.agent.pk, 'vazio.txt', b'   \n\n  ')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Document.objects.count(), 0)
        self.mock_enqueue.assert_not_called()

    def test_upload_to_foreign_agent_returns_404(self):
        response = self.upload(self.other_agent.pk, 'faq.txt', b'conteudo')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Document.objects.count(), 0)

    # list / detail

    def test_list_documents_only_from_my_agent(self):
        Document.objects.create(agent=self.agent, name='meu.txt', content='meu')
        Document.objects.create(agent=self.other_agent, name='alheio.txt', content='alheio')
        response = self.client.get(self.url(self.agent.pk), headers=self.auth)
        self.assertEqual(response.status_code, 200)
        names = [document['name'] for document in response.json()]
        self.assertEqual(names, ['meu.txt'])

    def test_get_document_detail(self):
        document = Document.objects.create(agent=self.agent, name='faq.txt', content='abc')
        response = self.client.get(self.url(self.agent.pk, document.pk), headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'processing')

    def test_get_foreign_document_returns_404(self):
        document = Document.objects.create(agent=self.other_agent, name='alheio.txt', content='abc')
        response = self.client.get(self.url(self.agent.pk, document.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)

    # delete

    def test_delete_document_removes_chunks(self):
        document = Document.objects.create(agent=self.agent, name='faq.txt', content='abc')
        DocumentChunk.objects.create(document=document, content='abc', embedding=vec(1.0))
        response = self.client.delete(self.url(self.agent.pk, document.pk), headers=self.auth)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Document.objects.filter(pk=document.pk).exists())
        self.assertEqual(DocumentChunk.objects.count(), 0)

    def test_delete_nonexistent_document_returns_404(self):
        response = self.client.delete(self.url(self.agent.pk, 99999), headers=self.auth)
        self.assertEqual(response.status_code, 404)


class ProcessDocumentTests(BaseAgentsTestCase):
    @patch('apps.ai.embed_text')
    def test_task_chunks_embeds_and_marks_ready(self, mock_embed):
        mock_embed.side_effect = lambda texts: [vec(0.5) for _ in texts]
        first, second = 'a' * 700, 'b' * 700
        document = Document.objects.create(agent=self.agent, name='faq.txt', content=f'{first}\n\n{second}')

        tasks.process_document(document.pk)

        document.refresh_from_db()
        self.assertEqual(document.status, 'ready')
        mock_embed.assert_called_once_with([first, second])
        chunks = list(document.chunks.order_by('pk'))
        self.assertEqual([chunk.content for chunk in chunks], [first, second])
        self.assertEqual(list(chunks[0].embedding), vec(0.5))

    @patch('apps.ai.embed_text')
    def test_task_unknown_document_is_silent(self, mock_embed):
        tasks.process_document(99999)
        mock_embed.assert_not_called()

    # nota: em chamada direta (fora de worker) self.retry re-levanta a exceção original

    @patch('apps.ai.embed_text')
    def test_task_failure_before_max_retries_keeps_processing(self, mock_embed):
        mock_embed.side_effect = RuntimeError('embeddings down')
        document = Document.objects.create(agent=self.agent, name='faq.txt', content='abc')

        with self.assertRaises(RuntimeError):
            tasks.process_document(document.pk)

        document.refresh_from_db()
        self.assertEqual(document.status, 'processing')
        self.assertEqual(document.chunks.count(), 0)

    @patch('apps.ai.embed_text')
    def test_task_failure_at_max_retries_marks_failed(self, mock_embed):
        mock_embed.side_effect = RuntimeError('embeddings down')
        document = Document.objects.create(agent=self.agent, name='faq.txt', content='abc')

        tasks.process_document.push_request(retries=3)
        try:
            with self.assertRaises(RuntimeError):
                tasks.process_document(document.pk)
        finally:
            tasks.process_document.pop_request()

        document.refresh_from_db()
        self.assertEqual(document.status, 'failed')

    @patch('apps.ai.embed_text')
    def test_task_rerun_replaces_old_chunks(self, mock_embed):
        # idempotência: retry após falha parcial não duplica chunks
        mock_embed.side_effect = lambda texts: [vec(0.5) for _ in texts]
        document = Document.objects.create(agent=self.agent, name='faq.txt', content='abc')
        stale = DocumentChunk.objects.create(document=document, content='velho', embedding=vec(1.0))

        tasks.process_document(document.pk)

        self.assertFalse(DocumentChunk.objects.filter(pk=stale.pk).exists())
        self.assertEqual([chunk.content for chunk in document.chunks.all()], ['abc'])


class KnowledgeContextTests(BaseAgentsTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.document = Document.objects.create(agent=cls.agent, name='faq.txt', content='...', status='ready')

    def add_chunk(self, content: str, embedding: list[float], document: Document | None = None) -> DocumentChunk:
        return DocumentChunk.objects.create(document=document or self.document, content=content, embedding=embedding)

    @patch('apps.ai.embed_text')
    def test_agent_without_documents_skips_embedding(self, mock_embed):
        self.document.delete()
        self.assertEqual(ai._knowledge_context(self.agent, 'oi'), '')
        mock_embed.assert_not_called()

    @patch('apps.ai.embed_text')
    def test_returns_top_4_closest_chunks_in_order(self, mock_embed):
        mock_embed.return_value = [vec(1.0)]
        self.add_chunk('c5', vec(0.0, 1.0))  # ortogonal à query, fica de fora
        self.add_chunk('c3', vec(1.0, 0.5))
        self.add_chunk('c1', vec(1.0))
        self.add_chunk('c4', vec(1.0, 1.0))
        self.add_chunk('c2', vec(1.0, 0.25))

        context = ai._knowledge_context(self.agent, 'qual o horário?')

        self.assertEqual(context, 'c1\n\nc2\n\nc3\n\nc4')
        mock_embed.assert_called_once_with(['qual o horário?'])

    @patch('apps.ai.embed_text')
    def test_does_not_leak_other_agent_chunks(self, mock_embed):
        # cross-tenant: só existe doc ready no agent alheio → meu agent nem embeda
        self.document.delete()
        foreign = Document.objects.create(agent=self.other_agent, name='alheio.txt', content='...', status='ready')
        self.add_chunk('segredo alheio', vec(1.0), document=foreign)

        self.assertEqual(ai._knowledge_context(self.agent, 'oi'), '')
        mock_embed.assert_not_called()

    @patch('apps.ai.embed_text')
    def test_ignores_documents_not_ready(self, mock_embed):
        Document.objects.filter(pk=self.document.pk).update(status='processing')
        self.add_chunk('ainda processando', vec(1.0))

        self.assertEqual(ai._knowledge_context(self.agent, 'oi'), '')
        mock_embed.assert_not_called()

    @patch('apps.ai.Claude')
    @patch('apps.ai.AgnoAgent')
    @patch('apps.ai.embed_text')
    def test_generate_reply_injects_knowledge_into_system_message(self, mock_embed, mock_agent_cls, mock_claude):
        mock_embed.return_value = [vec(1.0)]
        mock_agent_cls.return_value.run.return_value.content = 'Entregamos em 3 dias.'
        self.add_chunk('Entrega em 3 dias úteis', vec(1.0))

        reply = ai.generate_reply(self.agent, [], 'qual o prazo de entrega?')

        self.assertEqual(reply, 'Entregamos em 3 dias.')
        system_message = mock_agent_cls.call_args.kwargs['system_message']
        self.assertIn(self.agent.prompt, system_message)
        self.assertIn('<knowledge>', system_message)
        self.assertIn('Entrega em 3 dias úteis', system_message)

    @patch('apps.ai.Claude')
    @patch('apps.ai.AgnoAgent')
    def test_generate_reply_without_documents_keeps_plain_prompt(self, mock_agent_cls, mock_claude):
        self.document.delete()
        mock_agent_cls.return_value.run.return_value.content = 'Olá!'

        ai.generate_reply(self.agent, [], 'oi')

        self.assertEqual(mock_agent_cls.call_args.kwargs['system_message'], self.agent.prompt)
