from datetime import time

from django.db import migrations, models


def to_schedule(apps, schema_editor):
    # horário único (início/fim + dias) vira faixas por dia; janela que vira a meia-noite se parte em duas
    Agent = apps.get_model('agents', 'Agent')
    for agent in Agent.objects.exclude(ai_hours_start=None).exclude(ai_hours_end=None):
        start, end = agent.ai_hours_start, agent.ai_hours_end
        days = [[] for _ in range(7)]
        for day in agent.ai_days:
            if end > start or end == time(0):
                days[day].append([f'{start:%H:%M}', f'{end:%H:%M}'])
            else:
                days[day].append([f'{start:%H:%M}', '00:00'])
                days[(day + 1) % 7].append(['00:00', f'{end:%H:%M}'])
        agent.ai_schedule = [sorted(d) for d in days]
        agent.save(update_fields=['ai_schedule'])


class Migration(migrations.Migration):
    dependencies = [
        ('agents', '0008_agent_tools'),
    ]

    operations = [
        migrations.AddField(
            model_name='agent',
            name='ai_schedule',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.RunPython(to_schedule, migrations.RunPython.noop),
        migrations.RemoveField(model_name='agent', name='ai_hours_start'),
        migrations.RemoveField(model_name='agent', name='ai_hours_end'),
        migrations.RemoveField(model_name='agent', name='ai_days'),
    ]
