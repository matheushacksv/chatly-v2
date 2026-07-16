from celery import shared_task

from apps import ai
from apps.agents import services
from apps.agents.models import Document, DocumentChunk


@shared_task(bind=True, max_retries=3)
def process_document(self, document_id: int):
    document = Document.objects.filter(pk=document_id).first()
    if document is None:
        return
    try:
        DocumentChunk.objects.filter(document=document).delete()
        texts = services.split_text(document.content)
        embeddings = ai.embed_text(texts)
        DocumentChunk.objects.bulk_create(DocumentChunk(document=document, content=text, embedding=vector) for text, vector in zip(texts, embeddings))
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            Document.objects.filter(pk=document_id).update(status=Document.Status.FAILED)
            raise
        raise self.retry(exc=exc, countdown=2**self.request.retries)
    Document.objects.filter(pk=document_id).update(status=Document.Status.READY)
