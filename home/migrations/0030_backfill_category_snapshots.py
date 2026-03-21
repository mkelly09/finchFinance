"""
Data migration: backfill MonthEnd*CategorySnapshot records for all existing
MonthEndClose records using current field values.

This is safe to run because no category targets have been changed yet —
current values ARE the historical values for all previously closed months.
"""
from decimal import Decimal
from django.db import migrations
from django.db.models import Sum


def backfill_category_snapshots(apps, schema_editor):
    MonthEndClose = apps.get_model('home', 'MonthEndClose')
    Category = apps.get_model('home', 'Category')
    IncomeCategory = apps.get_model('home', 'IncomeCategory')
    WithholdingCategory = apps.get_model('home', 'WithholdingCategory')
    Expense = apps.get_model('home', 'Expense')
    Income = apps.get_model('home', 'Income')
    Transfer = apps.get_model('home', 'Transfer')
    MonthEndExpenseCategorySnapshot = apps.get_model('home', 'MonthEndExpenseCategorySnapshot')
    MonthEndWithholdingCategorySnapshot = apps.get_model('home', 'MonthEndWithholdingCategorySnapshot')
    MonthEndIncomeCategorySnapshot = apps.get_model('home', 'MonthEndIncomeCategorySnapshot')

    from calendar import monthrange
    from datetime import date

    for month_close in MonthEndClose.objects.all():
        first_day = month_close.month
        last_day = date(first_day.year, first_day.month, monthrange(first_day.year, first_day.month)[1])

        expense_entries = Expense.objects.filter(date__range=(first_day, last_day))
        income_entries = Income.objects.filter(date__range=(first_day, last_day))
        transfer_entries = Transfer.objects.filter(date__range=(first_day, last_day))

        # Expense category snapshots (categories with monthly_limit > 0, excl. Business Expense)
        planned_categories = Category.objects.filter(monthly_limit__gt=0).exclude(name='Business Expense')
        for category in planned_categories:
            if MonthEndExpenseCategorySnapshot.objects.filter(
                month_close=month_close, category=category
            ).exists():
                continue
            spent = expense_entries.filter(category=category).aggregate(
                total=Sum('amount')
            )['total'] or Decimal('0')
            MonthEndExpenseCategorySnapshot.objects.create(
                month_close=month_close,
                category=category,
                monthly_limit=category.monthly_limit,
                actual_spent=spent,
            )

        # Withholding bucket snapshots
        withholding_buckets = WithholdingCategory.objects.exclude(monthly_target__isnull=True)
        for bucket in withholding_buckets:
            if MonthEndWithholdingCategorySnapshot.objects.filter(
                month_close=month_close, withholding_category=bucket
            ).exists():
                continue
            contrib = transfer_entries.filter(
                withholding_category=bucket,
                to_account=bucket.account,
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            MonthEndWithholdingCategorySnapshot.objects.create(
                month_close=month_close,
                withholding_category=bucket,
                monthly_target=bucket.monthly_target or Decimal('0'),
                actual_contributed=contrib,
            )

        # Income category snapshots (categories with monthly_target > 0)
        for inc_cat in IncomeCategory.objects.filter(monthly_target__gt=0):
            if MonthEndIncomeCategorySnapshot.objects.filter(
                month_close=month_close, income_category=inc_cat
            ).exists():
                continue
            received = income_entries.filter(
                income_category=inc_cat
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            MonthEndIncomeCategorySnapshot.objects.create(
                month_close=month_close,
                income_category=inc_cat,
                monthly_target=inc_cat.monthly_target,
                actual_received=received,
            )


def reverse_backfill(apps, schema_editor):
    MonthEndExpenseCategorySnapshot = apps.get_model('home', 'MonthEndExpenseCategorySnapshot')
    MonthEndWithholdingCategorySnapshot = apps.get_model('home', 'MonthEndWithholdingCategorySnapshot')
    MonthEndIncomeCategorySnapshot = apps.get_model('home', 'MonthEndIncomeCategorySnapshot')
    MonthEndExpenseCategorySnapshot.objects.all().delete()
    MonthEndWithholdingCategorySnapshot.objects.all().delete()
    MonthEndIncomeCategorySnapshot.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0029_add_category_snapshots'),
    ]

    operations = [
        migrations.RunPython(backfill_category_snapshots, reverse_backfill),
    ]
