from django.contrib import admin

from apps.companies.models import Company


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    # ativação manual do plano até o checkout existir
    list_display = ['id', 'name', 'owner', 'plan', 'plan_until', 'created_at']
    list_editable = ['plan', 'plan_until']
    list_filter = ['plan']
    search_fields = ['name', 'owner__email']
