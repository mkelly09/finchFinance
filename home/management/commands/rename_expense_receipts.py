"""
Management command to rename all existing expense receipts to the new naming convention.
Format: YYYY-MM-DD_CategoryName_VendorName_$Amount.ext
"""
import os
import re
import shutil
from django.core.management.base import BaseCommand
from django.conf import settings
from home.models import ExpenseAttachment


class Command(BaseCommand):
    help = 'Rename all existing expense receipts to the new naming convention'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be renamed without actually renaming files',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No files will be renamed\n'))

        attachments = ExpenseAttachment.objects.select_related('expense', 'expense__category').all()
        total = attachments.count()

        self.stdout.write(f'Found {total} expense attachments to process\n')

        renamed_count = 0
        skipped_count = 0
        error_count = 0

        for i, attachment in enumerate(attachments, 1):
            expense = attachment.expense
            old_path = attachment.file.path
            old_name = os.path.basename(old_path)

            # Generate new filename using the same logic as the model
            date_str = expense.date.strftime("%Y-%m-%d")

            # Category name - sanitize
            category_name = expense.category.name if expense.category else "Uncategorized"
            category_clean = re.sub(r'[^\w\s-]', '', category_name)
            category_clean = re.sub(r'[-\s]+', '-', category_clean).strip('-')

            # Vendor name - sanitize
            vendor_name = expense.vendor_name or "Unknown-Vendor"
            vendor_clean = re.sub(r'[^\w\s-]', '', vendor_name)
            vendor_clean = re.sub(r'[-\s]+', '-', vendor_clean).strip('-')

            # Amount
            amount_str = f"${expense.amount:.2f}"

            # Get file extension
            _, ext = os.path.splitext(old_name)

            # Build new filename
            new_filename = f"{date_str}_{category_clean}_{vendor_clean}_{amount_str}{ext}"

            # Determine correct directory based on expense date (not upload date)
            year_dir = expense.date.strftime("%Y")
            month_dir = expense.date.strftime("%m")
            target_dir = os.path.join(settings.MEDIA_ROOT, 'expense_attachments', year_dir, month_dir)

            # Create directory if it doesn't exist
            os.makedirs(target_dir, exist_ok=True)

            new_path = os.path.join(target_dir, new_filename)

            # Check if file needs renaming
            if old_path == new_path:
                self.stdout.write(f'[{i}/{total}] SKIP: Already correctly named: {old_name}')
                skipped_count += 1
                continue

            # Check if old file exists
            if not os.path.exists(old_path):
                self.stdout.write(self.style.ERROR(f'[{i}/{total}] ERROR: File not found: {old_path}'))
                error_count += 1
                continue

            # Handle duplicate filenames
            if os.path.exists(new_path):
                # Add a counter suffix before extension
                base_name, ext = os.path.splitext(new_filename)
                counter = 1
                while os.path.exists(new_path):
                    new_filename = f"{base_name}_{counter}{ext}"
                    new_path = os.path.join(os.path.dirname(old_path), new_filename)
                    counter += 1
                self.stdout.write(self.style.WARNING(f'[{i}/{total}] Note: Added counter to avoid duplicate'))

            # Show what will be done
            old_rel_path = os.path.relpath(old_path, settings.MEDIA_ROOT).replace('\\', '/')
            new_rel_path = os.path.relpath(new_path, settings.MEDIA_ROOT).replace('\\', '/')

            self.stdout.write(f'[{i}/{total}] MOVE/RENAME:')
            self.stdout.write(f'  FROM: {old_rel_path}')
            self.stdout.write(f'  TO:   {new_rel_path}')

            if not dry_run:
                try:
                    # Move/rename the physical file (shutil.move works across directories)
                    shutil.move(old_path, new_path)

                    # Update the database record
                    # Get the relative path from MEDIA_ROOT
                    rel_path = os.path.relpath(new_path, settings.MEDIA_ROOT)
                    # Convert to forward slashes for database storage
                    rel_path = rel_path.replace('\\', '/')
                    attachment.file.name = rel_path
                    attachment.save(update_fields=['file'])

                    renamed_count += 1
                    self.stdout.write(self.style.SUCCESS(f'  SUCCESS\n'))

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'  ERROR: {str(e)}\n'))
                    error_count += 1
            else:
                self.stdout.write('')

        # Summary
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS(f'SUMMARY:'))
        self.stdout.write(f'  Total attachments: {total}')
        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f'  Renamed: {renamed_count}'))
            self.stdout.write(f'  Skipped (already correct): {skipped_count}')
            if error_count > 0:
                self.stdout.write(self.style.ERROR(f'  Errors: {error_count}'))
        else:
            self.stdout.write(self.style.WARNING(f'  Would rename: {total - skipped_count}'))
            self.stdout.write(f'  Would skip: {skipped_count}')
            self.stdout.write('\nRun without --dry-run to actually rename files')
