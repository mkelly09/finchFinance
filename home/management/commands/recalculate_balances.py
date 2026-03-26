"""
Management command to recalculate account balances from existing transactions.

This command:
1. For each account with balance_tracking_enabled
2. Starts from balance_tracking_start_date baseline
3. Applies all existing transactions dated on/after the start date
4. Updates current_balance to reflect all historical transactions

Use this after:
- Initial setup of balance tracking
- Bulk imports of historical data
- Any manual database changes to transactions
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum, Q
from datetime import date
from decimal import Decimal

from home.models import BankAccount, Income, Expense, Transfer, BalanceAdjustment


class Command(BaseCommand):
    help = 'Recalculate account balances from existing transactions'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )
        parser.add_argument(
            '--account',
            type=int,
            help='Only recalculate for specific account ID',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        account_id = options.get('account')

        # Get accounts with balance tracking enabled
        accounts_qs = BankAccount.objects.filter(balance_tracking_enabled=True)

        if account_id:
            accounts_qs = accounts_qs.filter(pk=account_id)

        if not accounts_qs.exists():
            self.stdout.write(self.style.WARNING("No accounts with balance tracking enabled."))
            return

        self.stdout.write(f"\nRecalculating balances for {accounts_qs.count()} account(s)...\n")

        results = []

        for account in accounts_qs:
            if not account.balance_tracking_start_date:
                self.stdout.write(self.style.WARNING(
                    f"  Skipping {account.name}: no tracking start date set"
                ))
                continue

            # Start with the baseline balance (what was set during init)
            # We need to go back to the tracking start and recalculate
            start_date = account.balance_tracking_start_date

            # Get the baseline by finding what the balance was BEFORE any March transactions
            # This should be the value set by init_balance_tracking
            baseline_balance = account.current_balance

            # Calculate net change from all transactions since start_date
            # Income: increases balance
            income_total = Income.objects.filter(
                bank_account=account,
                date__gte=start_date
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

            # Expenses: decreases balance
            expense_total = Expense.objects.filter(
                bank_account=account,
                date__gte=start_date
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

            # Transfers out: decreases balance
            transfers_out = Transfer.objects.filter(
                from_account=account,
                date__gte=start_date
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

            # Transfers in: increases balance
            transfers_in = Transfer.objects.filter(
                to_account=account,
                date__gte=start_date
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

            # Balance adjustments: can be positive or negative
            adjustments_total = BalanceAdjustment.objects.filter(
                bank_account=account,
                date__gte=start_date
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

            # But wait - we need to recalculate from the ORIGINAL baseline
            # The current balance might already have some updates applied
            # Let's calculate what the balance SHOULD be from the baseline

            # We need to find the original baseline from init
            # For now, let's calculate the net change and show what needs to happen

            net_change = (
                income_total
                - expense_total
                - transfers_out
                + transfers_in
                + adjustments_total
            )

            # The correct balance should be baseline + net_change
            # But we need the baseline from BEFORE signals started firing
            # For safety, let's use the tracking_start_date to reconstruct

            # Actually, let me recalculate from the beginning properly:
            # We need to reset to the baseline and recalculate
            # The baseline should have been set by init_balance_tracking

            # Let's get the AccountSnapshot for the previous month
            from home.models import AccountSnapshot, MonthEndClose
            from datetime import timedelta

            # Find the month close just before the tracking start date
            prev_month = start_date.replace(day=1) - timedelta(days=1)
            prev_month_first = prev_month.replace(day=1)

            try:
                month_close = MonthEndClose.objects.get(month=prev_month_first, is_locked=True)
                snapshot = AccountSnapshot.objects.get(month_close=month_close, bank_account=account)
                baseline_balance = snapshot.balance
            except (MonthEndClose.DoesNotExist, AccountSnapshot.DoesNotExist):
                self.stdout.write(self.style.WARNING(
                    f"  WARNING: Could not find snapshot for {account.name}, using current balance as baseline"
                ))
                # Fall back to using current balance minus calculated changes
                baseline_balance = account.current_balance - net_change

            # Calculate what the balance should be
            calculated_balance = baseline_balance + net_change

            results.append({
                'account': account,
                'baseline': baseline_balance,
                'income': income_total,
                'expenses': expense_total,
                'transfers_out': transfers_out,
                'transfers_in': transfers_in,
                'adjustments': adjustments_total,
                'net_change': net_change,
                'current_balance': account.current_balance,
                'calculated_balance': calculated_balance,
                'difference': calculated_balance - account.current_balance,
            })

        # Display results
        self.stdout.write("\n" + "="*120)
        self.stdout.write(f"{'Account':<30} {'Baseline':>12} {'Net Change':>12} {'Current':>12} {'Calculated':>12} {'Diff':>12}")
        self.stdout.write("="*120)

        for result in results:
            diff_str = f"${result['difference']:,.2f}"
            if result['difference'] != 0:
                diff_str = self.style.WARNING(diff_str)
            else:
                diff_str = self.style.SUCCESS(diff_str)

            self.stdout.write(
                f"{result['account'].name[:30]:<30} "
                f"${result['baseline']:>11,.2f} "
                f"${result['net_change']:>11,.2f} "
                f"${result['current_balance']:>11,.2f} "
                f"${result['calculated_balance']:>11,.2f} "
                f"{diff_str:>12}"
            )

        self.stdout.write("\nTransaction breakdown:")
        for result in results:
            if result['net_change'] != 0:
                self.stdout.write(f"\n{result['account'].name}:")
                self.stdout.write(f"  Income:        +${result['income']:,.2f}")
                self.stdout.write(f"  Expenses:      -${result['expenses']:,.2f}")
                self.stdout.write(f"  Transfers out: -${result['transfers_out']:,.2f}")
                self.stdout.write(f"  Transfers in:  +${result['transfers_in']:,.2f}")
                self.stdout.write(f"  Adjustments:   ${result['adjustments']:>+,.2f}")
                self.stdout.write(f"  Net change:    ${result['net_change']:>+,.2f}")

        if dry_run:
            self.stdout.write(self.style.WARNING(
                "\n[DRY RUN] No changes made. Remove --dry-run to update balances."
            ))
            return

        # Apply updates
        self.stdout.write("\nApplying balance updates...")

        with transaction.atomic():
            updated_count = 0
            for result in results:
                if result['difference'] != 0:
                    account = result['account']
                    account.current_balance = result['calculated_balance']
                    account.last_updated = date.today()
                    account.save(update_fields=['current_balance', 'last_updated'])
                    updated_count += 1

            self.stdout.write(self.style.SUCCESS(
                f"\nOK Successfully updated {updated_count} account balance(s)"
            ))
