from __future__ import annotations

from typing import Any, Optional

from django.conf import settings

from django.apps import apps

from django.db import connections, OperationalError, ProgrammingError

from forum.models import SiteSetting

def get_value(key: str, default: Optional[str] = None, *, using: str = "default") -> Optional[str]:
    table = SiteSetting._meta.db_table
    if not _table_exists(using, table):
        return default
    try:
        return SiteSetting.objects.using(using).get(key=key).value
    except SiteSetting.DoesNotExist:
        return default
    except (OperationalError, ProgrammingError):
        return default

def set_value(key: str, value: Any) -> None:
    SiteSetting = apps.get_model('forum', 'SiteSetting')
    SiteSetting.objects.update_or_create(key=key, defaults={'value': str(value)})


def get_int(key: str, default: int = 0, *, using: str = "default") -> int:
    raw = get_value(key, None, using=using)
    try:
        return int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def get_float(key: str, default: float) -> float:
    value = get_value(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def _table_exists(connection_alias: str, table_name: str) -> bool:
    try:
        with connections[connection_alias].cursor() as cursor:
            return table_name in connections[connection_alias].introspection.table_names()
    except Exception:
        return False