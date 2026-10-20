from django.db import migrations


def drop_first_name(apps, schema_editor):
    # {{first_name}} virou variável do sistema (sai do nome); o campo personalizado com essa chave sai de todas as empresas
    apps.get_model('chats', 'ContactField').objects.filter(key='first_name').delete()


class Migration(migrations.Migration):
    dependencies = [('chats', '0022_contact_ai_no_catchup')]

    operations = [migrations.RunPython(drop_first_name, migrations.RunPython.noop)]
