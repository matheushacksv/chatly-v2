import mimetypes
import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from apps.chats.models import Message, Sticker
from apps.personal.models import PersonalMessage


class Command(BaseCommand):
    help = (
        'Copia pro storage atual os arquivos que o banco referencia, lendo de um S3 antigo '
        '(OLD_MINIO_ENDPOINT_URL/ACCESS_KEY/SECRET_KEY/BUCKET_NAME no ambiente). Mesma chave, então o banco não muda. '
        'Idempotente: pula o que já existe.'
    )

    def handle(self, *args, **options):
        old = boto3.client(
            's3',
            endpoint_url=os.environ['OLD_MINIO_ENDPOINT_URL'],
            aws_access_key_id=os.environ['OLD_MINIO_ACCESS_KEY'],
            aws_secret_access_key=os.environ['OLD_MINIO_SECRET_KEY'],
            config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}),
        )
        old_bucket = os.environ['OLD_MINIO_BUCKET_NAME']

        names = set()
        names.update(Message.objects.exclude(media_file='').exclude(media_file__isnull=True).values_list('media_file', flat=True))
        names.update(PersonalMessage.objects.exclude(media_file='').exclude(media_file__isnull=True).values_list('media_file', flat=True))
        names.update(Sticker.objects.values_list('file', flat=True))
        names.update(get_user_model().objects.exclude(avatar='').exclude(avatar__isnull=True).values_list('avatar', flat=True))

        copied = skipped = missing = 0
        for name in sorted(names):
            if default_storage.exists(name):
                skipped += 1
                continue
            try:
                obj = old.get_object(Bucket=old_bucket, Key=name)
            except ClientError:
                missing += 1
                self.stderr.write(f'faltando na origem: {name}')
                continue
            # Content-Type junto: sem ele o MinIO serve octet-stream e o navegador não toca áudio/vídeo
            content_type = obj.get('ContentType') or mimetypes.guess_type(name)[0] or 'application/octet-stream'
            default_storage.connection.meta.client.put_object(
                Bucket=default_storage.bucket_name, Key=name, Body=obj['Body'].read(), ContentType=content_type
            )
            copied += 1
        self.stdout.write(f'{len(names)} referenciados: {copied} copiados, {skipped} já existiam, {missing} faltando na origem')
