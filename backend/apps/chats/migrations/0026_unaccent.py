from django.contrib.postgres.operations import UnaccentExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('chats', '0025_campaign_draft'),
    ]

    operations = [
        UnaccentExtension(),
    ]
