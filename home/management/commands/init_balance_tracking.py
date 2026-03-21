"""
Management command to initialize automatic balance tracking.

This command:
1. Finds the February 2026 MonthEndClose (must be locked)
2. Gets all AccountSnapshot objects from that close
3. For each account:
   - Sets current_balance to the February snapshot balance
   - Enables balance tracking (balance_tracking_enabled = True)
   - Sets tracking start date to March 1, 2026
   - Sets last_updated to March 1, 2026

This provides a clean starting point for automatic balance updates,
using the locked month-end close as the baseline.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from datetime import date
from decimal import Decimal

from home.models import MonthEndClose, AccountSnapshot, BankAccount


class Command(BaseCommand):
    help = 'Initialize automatic balance tracking from February 2026 month-end close'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force initialization even if some accounts already have tracking enabled',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']

        # Find February 2026 MonthEndClose
        february_2026 = date(2026, 2, 1)
        march_1_2026 = date(2026, 3, 1)

        self.stdout.write(f"Looking for February 2026 month-end close (locked)...")

        try:
            month_close = MonthEndClose.objects.get(
                month=february_2026,
                is_locked=True
            )
        except MonthEndClose.DoesNotExist:
            raise CommandError(
                "February 2026 month-end close not found or not locked. "
                "Please complete and lock the February close before running this command."
            )

        self.stdout.write(self.style.SUCCESS(
            f"OK Found February 2026 close (locked on {month_close.closed_at})"
        ))

        # Get all account snapshots from this close
        snapshots = AccountSnapshot.objects.filter(
            month_close=month_close
        ).select_related('bank_account')

        if not snapshots.exists():
            raise CommandError(
                "No account snapshots found for February 2026 month-end close."
            )

        self.stdout.write(f"\nFound {snapshots.count()} account snapshots:")

        # Check if any accounts already have tracking enabled
        already_enabled = []
        updates = []

        for snapshot in snapshots:
            account = snapshot.bank_account
            if account.balance_tracking_enabled and not force:
                already_enabled.append(account)
            else:
                updates.append((account, snapshot))

        if already_enabled:
            self.stdout.write(self.style.WARNING(
                f"\nWARNING {len(already_enabled)} accounts already have balance tracking enabled:"
            ))
            for account in already_enabled:
                self.stdout.write(f"  - {account.name}")
            if not force:
                self.stdout.write(
                    "\nUse --force to reinitialize these accounts (will overwrite current settings)"
                )

        if not updates:
            self.stdout.write(self.style.WARNING("\nNo accounts to update."))
            return

        # Show what will be updated
        self.stdout.write(f"\nWill initialize balance tracking for {len(updates)} accounts:")
        self.stdout.write(f"{'Account':<40} {'Feb Balance':>15} {'Action':>20}")
        self.stdout.write("-" * 77)

        for account, snapshot in updates:
            action = "Initialize" if not account.balance_tracking_enabled else "Reinitialize"
            self.stdout.write(
                f"{account.name[:40]:<40} "
                f"${snapshot.balance:>14,.2f} "
                f"{action:>20}"
            )

        if dry_run:
            self.stdout.write(self.style.WARNING(
                "\n[DRY RUN] No changes made. Remove --dry-run to apply changes."
            ))
            return

        # Apply updates
        self.stdout.write("\nApplying updates...")

        with transaction.atomic():
            updated_count = 0
            for account, snapshot in updates:
                account.current_balance = snapshot.balance
                account.balance_tracking_enabled = True
                account.balance_tracking_start_date = march_1_2026
                account.last_updated = march_1_2026
                account.save(update_fields=[
                    'current_balance',
                    'balance_tracking_enabled',
                    'balance_tracking_start_date',
                    'last_updated'
                ])
                updated_count += 1

            self.stdout.write(self.style.SUCCESS(
                f"\nOK Successfully initialized balance tracking for {updated_count} accounts"
            ))

        self.stdout.write(self.style.SUCCESS(
            f"\nBalance tracking is now active for transactions dated {march_1_2026} and later."
        ))
