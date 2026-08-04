from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import PrintGroup


@receiver(post_save, sender=PrintGroup)
def sync_new_master_print_group_to_all_accounts(sender, instance, created, **kwargs):
    """When a shared catalog print group is added (e.g. Django admin), enable it for every subaccount."""
    if not created:
        return
    if instance.request_id or instance.owner_account_id:
        return
    from .account_library import sync_master_print_group_to_all_accounts

    sync_master_print_group_to_all_accounts(instance)
