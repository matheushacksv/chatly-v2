from functools import cached_property

import boto3
from botocore.config import Config
from django.conf import settings
from storages.backends.s3 import S3Storage
from storages.utils import clean_name


class PrivateMediaStorage(S3Storage):
    """MinIO privado: o backend grava pela rede interna, o navegador recebe URL assinada no endereço público.

    A assinatura SigV4 cobre host e caminho, então tem que ser gerada com o endereço que o navegador
    vai usar (Caddy `/media/*` -> MinIO, bucket `media`), não com o `http://minio:9000` interno.
    Assinar é conta local: nenhuma chamada de rede sai daqui.
    """

    @cached_property
    def _public_client(self):
        return boto3.client(
            's3',
            endpoint_url=settings.MINIO_PUBLIC_URL,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region_name or 'us-east-1',
            config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}),
        )

    def url(self, name, parameters=None, expire=None, http_method=None):
        params = {**(parameters or {}), 'Bucket': self.bucket_name, 'Key': self._normalize_name(clean_name(name))}
        return self._public_client.generate_presigned_url(
            'get_object', Params=params, ExpiresIn=expire or self.querystring_expire, HttpMethod=http_method
        )
