import time

from botocore.exceptions import BotoCoreError, ClientError
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Cria o bucket de mídia se não existir (privado: sem política pública). Idempotente, roda no startup.'

    def add_arguments(self, parser):
        parser.add_argument('--wait', type=int, default=30, help='segundos esperando o MinIO subir')

    def handle(self, *args, wait, **options):
        client = default_storage.connection.meta.client
        bucket = default_storage.bucket_name
        deadline = time.monotonic() + wait
        while True:
            try:
                client.head_bucket(Bucket=bucket)
                self.stdout.write(f'bucket {bucket} ok')
                return
            except ClientError as exc:
                if exc.response.get('Error', {}).get('Code') in ('404', 'NoSuchBucket'):
                    client.create_bucket(Bucket=bucket)
                    self.stdout.write(f'bucket {bucket} criado')
                    return
                raise CommandError(f'MinIO recusou o bucket {bucket}: {exc}')
            except BotoCoreError as exc:
                # MinIO ainda subindo (connection refused): tenta de novo até o prazo
                if time.monotonic() >= deadline:
                    raise CommandError(f'MinIO não respondeu em {wait}s: {exc}')
                time.sleep(1)
