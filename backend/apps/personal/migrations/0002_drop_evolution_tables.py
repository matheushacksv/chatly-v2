from django.db import migrations

# O canal pessoal saiu do evolution-go pro OpenWA e os dados antigos foram descartados de propósito
# (atendentes re-pareiam por QR). O app `apps.evolution` não existe mais, então as tabelas e o
# histórico de migrations dele são limpos aqui. IF EXISTS: banco novo nunca teve essas tabelas.
DROP_EVOLUTION = """
DROP TABLE IF EXISTS evolution_evolutionmessage CASCADE;
DROP TABLE IF EXISTS evolution_evolutioncontact CASCADE;
DROP TABLE IF EXISTS evolution_evolutionaccount CASCADE;
DELETE FROM django_migrations WHERE app = 'evolution';
DELETE FROM auth_permission WHERE content_type_id IN (SELECT id FROM django_content_type WHERE app_label = 'evolution');
DELETE FROM django_admin_log WHERE content_type_id IN (SELECT id FROM django_content_type WHERE app_label = 'evolution');
DELETE FROM django_content_type WHERE app_label = 'evolution';
"""


class Migration(migrations.Migration):
    dependencies = [
        ('personal', '0001_initial'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('admin', '0003_logentry_add_action_flag_choices'),
    ]

    operations = [migrations.RunSQL(DROP_EVOLUTION, reverse_sql=migrations.RunSQL.noop)]
