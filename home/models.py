from django.db import models
from datetime import date as dt_date
from decimal import Decimal

class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    monthly_limit = models.DecimalField(max_digits=10, decimal_places=2)
    savings_target_per_paycheque = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True, help_text="Leave blank if not applicable."
    )

    def __str__(self):
        return self.name


class BankAccount(models.Model):
    name = models.CharField(max_length=100)
    institution = models.CharField(max_length=100, blank=True)
    account_number_last4 = models.CharField(
        max_length=4,
        blank=True,
        help_text="Optional: last 4 digits for easier identification.",
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        label = self.name
        if self.institution:
            label = f"{self.institution} – {label}"
        if self.account_number_last4:
            label = f"{label} (...{self.account_number_last4})"
        return label

class ImportBatch(models.Model):
    bank_account = models.ForeignKey(
        "BankAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_batches",
    )
    imported_at = models.DateTimeField(auto_now_add=True)
    earliest_date = models.DateField()
    latest_date = models.DateField()
    total_transactions = models.IntegerField(default=0)
    total_income_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    total_expense_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    filename = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-imported_at"]

    def __str__(self):
        acc = self.bank_account or "Unknown account"
        return f"Import {self.pk} – {acc} – {self.earliest_date} to {self.latest_date}"

    @property
    def net_amount(self):
        return (self.total_income_amount or Decimal("0.00")) - (
            self.total_expense_amount or Decimal("0.00")
        )

class Expense(models.Model):
    date = models.DateField(default=dt_date.today)
    vendor_name = models.CharField(max_length=100, default="Unknown Vendor")
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    location = models.CharField(max_length=100, default="Ottawa")
    notes = models.TextField(blank=True, default='')
    bank_account = models.ForeignKey(
        BankAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
    )
    import_batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
    )

    def __str__(self):
        return f"{self.date} | {self.vendor_name} | {self.amount}"

class Income(models.Model):
    CATEGORY_CHOICES = [
        ('Arnprior Rental Income (MAIN)', 'Arnprior Rental Income (MAIN)'),
        ('Arnprior Rental Income (LOFT)', 'Arnprior Rental Income (LOFT)'),
        ('Employment Income', 'Employment Income'),
        ('Investment Income', 'Investment Income'),
    ]

    date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, null=True, blank=True)
    taxable = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    bank_account = models.ForeignKey(
        BankAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incomes",
    )
    import_batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incomes",
    )

    def save(self, *args, **kwargs):
        # Automatically set taxable = True for all categories except Employment Income
        if self.category != 'Employment Income':
            self.taxable = True
        super().save(*args, **kwargs)

