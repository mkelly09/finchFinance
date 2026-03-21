"""
Signal handlers for automatic bank account balance updates.

These signals update account balances in real-time when transactions are created or deleted.
Only applies to accounts with balance_tracking_enabled=True and transactions on/after
the balance_tracking_start_date.
"""

from django.db import transaction
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from decimal import Decimal
from datetime import date as dt_date

from django.contrib.auth.models import User
from .models import Income, Expense, Transfer, BalanceAdjustment, BankAccount, UserProfile


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


def should_update_balance(account, transaction_date):
    """
    Determine if a transaction should trigger a balance update.

    Args:
        account: BankAccount instance (or None)
        transaction_date: Date of the transaction

    Returns:
        bool: True if balance should be updated
    """
    if account is None:
        return False

    if not account.balance_tracking_enabled:
        return False

    if account.balance_tracking_start_date is None:
        return False

    # Only update for transactions on or after the tracking start date
    if transaction_date < account.balance_tracking_start_date:
        return False

    return True


def update_account_balance(account_id, amount_delta):
    """
    Update an account's balance by the given delta amount.

    Uses select_for_update() to prevent race conditions when multiple
    transactions are being processed concurrently.

    Args:
        account_id: ID of the BankAccount to update
        amount_delta: Amount to add to the current balance (can be negative)
    """
    with transaction.atomic():
        account = BankAccount.objects.select_for_update().get(pk=account_id)
        account.current_balance += Decimal(str(amount_delta))
        account.last_updated = dt_date.today()
        account.save(update_fields=['current_balance', 'last_updated'])


# =============================================================================
# INCOME SIGNALS
# =============================================================================

@receiver(post_save, sender=Income)
def income_post_save(sender, instance, created, **kwargs):
    """
    When income is created, increase the account balance.
    """
    if not created:
        # Updates to existing income are not currently handled
        # (would require tracking previous values)
        return

    account = instance.bank_account
    if not should_update_balance(account, instance.date):
        return

    # Income increases the account balance
    update_account_balance(account.id, instance.amount)


@receiver(post_delete, sender=Income)
def income_post_delete(sender, instance, **kwargs):
    """
    When income is deleted, decrease the account balance.
    """
    account = instance.bank_account
    if not should_update_balance(account, instance.date):
        return

    # Reverse the income (decrease balance)
    update_account_balance(account.id, -instance.amount)


# =============================================================================
# EXPENSE SIGNALS
# =============================================================================

@receiver(post_save, sender=Expense)
def expense_post_save(sender, instance, created, **kwargs):
    """
    When expense is created, decrease the account balance.

    Special handling for withholding bucket expenses:
    - If an expense is tagged to a withholding_category, it decreases the account balance
    - This represents money actually leaving the account to pay a bill
    """
    if not created:
        # Updates to existing expenses are not currently handled
        return

    account = instance.bank_account
    if not should_update_balance(account, instance.date):
        return

    # Expenses decrease the account balance (including withholding bucket expenses)
    update_account_balance(account.id, -instance.amount)


@receiver(post_delete, sender=Expense)
def expense_post_delete(sender, instance, **kwargs):
    """
    When expense is deleted, increase the account balance.
    """
    account = instance.bank_account
    if not should_update_balance(account, instance.date):
        return

    # Reverse the expense (increase balance)
    update_account_balance(account.id, instance.amount)


# =============================================================================
# TRANSFER SIGNALS
# =============================================================================

@receiver(post_save, sender=Transfer)
def transfer_post_save(sender, instance, created, **kwargs):
    """
    When transfer is created:
    - Decrease from_account balance
    - Increase to_account balance
    """
    if not created:
        # Updates to existing transfers are not currently handled
        return

    # Update from_account (decrease)
    if instance.from_account:
        if should_update_balance(instance.from_account, instance.date):
            update_account_balance(instance.from_account.id, -instance.amount)

    # Update to_account (increase)
    if instance.to_account:
        if should_update_balance(instance.to_account, instance.date):
            update_account_balance(instance.to_account.id, instance.amount)


@receiver(post_delete, sender=Transfer)
def transfer_post_delete(sender, instance, **kwargs):
    """
    When transfer is deleted, reverse both balance changes.
    """
    # Reverse from_account (increase)
    if instance.from_account:
        if should_update_balance(instance.from_account, instance.date):
            update_account_balance(instance.from_account.id, instance.amount)

    # Reverse to_account (decrease)
    if instance.to_account:
        if should_update_balance(instance.to_account, instance.date):
            update_account_balance(instance.to_account.id, -instance.amount)


# =============================================================================
# BALANCE ADJUSTMENT SIGNALS
# =============================================================================

@receiver(post_save, sender=BalanceAdjustment)
def balance_adjustment_post_save(sender, instance, created, **kwargs):
    """
    When balance adjustment is created, apply the adjustment amount.
    """
    if not created:
        # Updates to existing adjustments are not currently handled
        return

    account = instance.bank_account
    if not should_update_balance(account, instance.date):
        return

    # Apply the adjustment (can be positive or negative)
    update_account_balance(account.id, instance.amount)


@receiver(post_delete, sender=BalanceAdjustment)
def balance_adjustment_post_delete(sender, instance, **kwargs):
    """
    When balance adjustment is deleted, reverse the adjustment.
    """
    account = instance.bank_account
    if not should_update_balance(account, instance.date):
        return

    # Reverse the adjustment
    update_account_balance(account.id, -instance.amount)
