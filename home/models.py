from django.db import models
from django.db.models import Sum
from django.db.models.functions import Coalesce
from datetime import date as dt_date
from decimal import Decimal
from django.db.models import SET_NULL



class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    monthly_limit = models.DecimalField(max_digits=10, decimal_places=2)
    savings_target_per_paycheque = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Leave blank if not applicable.",
    )

    def __str__(self):
        return self.name


class IncomeCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)

    # For "income category progress" (expected monthly amount per category)
    monthly_target = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Expected monthly income for this category (used for progress bars).",
    )

    # Default taxable behaviour for new income entries
    taxable_default = models.BooleanField(
        default=True,
        help_text="Default taxable setting for new income entries in this category.",
    )

    default_rental_unit = models.ForeignKey(
        "RentalUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="default_income_categories",
    )

    def __str__(self):
        return self.name


class BankAccountType(models.TextChoices):
    CHEQUING = "CHEQUING", "Chequing"
    CREDIT_CARD = "CREDIT_CARD", "Credit card"
    SAVINGS = "SAVINGS", "Savings"
    TFSA = "TFSA", "TFSA"
    RETIREMENT = "RETIREMENT", "Retirement (RRSP, etc.)"
    OTHER = "OTHER", "Other"


class BankAccount(models.Model):
    name = models.CharField(max_length=100)
    institution = models.CharField(max_length=100, blank=True)
    account_number_last4 = models.CharField(
        max_length=4,
        blank=True,
        help_text="Optional: last 4 digits for easier identification.",
    )

    # NEW: what kind of account is this?
    account_type = models.CharField(
        max_length=20,
        choices=BankAccountType.choices,
        default=BankAccountType.CHEQUING,
    )

    # NEW: does this account contain withholding “buckets”?
    is_withholding_account = models.BooleanField(default=False)

    # NEW: real-world balance + when you last updated it
    current_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Latest known bank balance for this account.",
    )
    last_updated = models.DateField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    def __str__(self):
        label = self.name
        if self.institution:
            label = f"{self.institution} – {label}"
        if self.account_number_last4:
            label = f"{label} (...{self.account_number_last4})"
        return label

    @property
    def withholding_total(self) -> Decimal:
        """
        Total of all withholding bucket balances inside this account.
        If there are no withholding categories, returns 0.
        """
        return self.withholding_categories.aggregate(
            total=Coalesce(Sum("transactions__amount"), Decimal("0"))
        )["total"]

    @property
    def unallocated_balance(self) -> Decimal:
        """
        Portion of this account's balance that is NOT assigned to any withholding bucket.
        Can be negative if buckets over-allocate compared to the real bank balance.
        """
        return self.current_balance - self.withholding_total


class WithholdingCategory(models.Model):
    """
    A named bucket inside a withholding account.
    Examples:
      - 'Foxview insurance'
      - 'Arnprior property tax – Main'
      - 'Rental income tax – Arnprior Loft'
    """

    account = models.ForeignKey(
        BankAccount,
        on_delete=models.CASCADE,
        related_name="withholding_categories",
        limit_choices_to={"is_withholding_account": True},
    )
    name = models.CharField(max_length=100)

    # Optional: where you're trying to get to
    target_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Desired balance for this bucket (e.g. yearly bill amount).",
    )
    next_due_date = models.DateField(
        null=True,
        blank=True,
        help_text="When the related bill/tax is next due, if known.",
    )

    def __str__(self):
        return f"{self.name} ({self.account.name})"

    @property
    def balance(self) -> Decimal:
        """
        Current balance for this bucket, using a clear sign convention:
        - Positive transaction = contribution into the bucket.
        - Negative transaction = money taken out to pay the real bill.
        """
        return self.transactions.aggregate(
            total=Coalesce(Sum("amount"), Decimal("0"))
        )["total"]

    def remaining_to_target(self) -> Decimal:
        """
        How much more you need to reach the target.
        If negative, you’re over-funded.
        """
        return self.target_amount - self.balance


class WithholdingTransaction(models.Model):
    """
    One movement into or out of a withholding bucket.

    SIGN CONVENTION:
      - Positive amount  => contribution INTO the bucket
                           (e.g. biweekly savings for Foxview insurance)
      - Negative amount  => money taken OUT of the bucket
                           (e.g. when you actually pay the insurance bill)
    """

    category = models.ForeignKey(
        WithholdingCategory,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "id"]

    def __str__(self):
        sign = "+" if self.amount >= 0 else "-"
        return f"{self.date} {sign}${abs(self.amount)} → {self.category.name}"


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
    notes = models.TextField(blank=True, default="")
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
    rental_unit = models.ForeignKey(
        "RentalUnit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
        help_text="Optional: tag this expense to a rental unit (including Shared/Common).",
    )

    cra_category = models.ForeignKey(
        "CRARentalExpenseCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
        help_text="Optional: CRA rental expense classification for tax reporting.",
    )

    rental_business_use_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Optional: percent (0–100) of this expense that is attributable to rental use (useful for mixed-use properties like Foxview).",
    )


    def __str__(self):
        return f"{self.date} | {self.vendor_name} | {self.amount}"

class ExpenseAttachment(models.Model):
    expense = models.ForeignKey(
        "Expense",
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField(upload_to="expense_attachments/%Y/%m/")
    original_name = models.CharField(max_length=255, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at", "-id"]

    def __str__(self):
        return self.original_name or f"Attachment {self.id} (Expense {self.expense_id})"


class Income(models.Model):
    CATEGORY_CHOICES = [
        ("Arnprior Rental Income (MAIN)", "Arnprior Rental Income (MAIN)"),
        ("Arnprior Rental Income (LOFT)", "Arnprior Rental Income (LOFT)"),
        ("Employment Income", "Employment Income"),
        ("Investment Income", "Investment Income"),
        ("Miscellaneous", "Miscellaneous"),
    ]

    rental_unit = models.ForeignKey("RentalUnit", null=True, blank=True, on_delete=SET_NULL, related_name="income_entries")

    date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.CharField(
        max_length=50, choices=CATEGORY_CHOICES, null=True, blank=True
    )
    income_category = models.ForeignKey(
        "IncomeCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incomes",
    )

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
    rental_unit = models.ForeignKey(
        "RentalUnit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incomes",
        help_text="Optional: tag this income to a rental unit (e.g. Arnprior MAIN/LOFT).",
    )

    def save(self, *args, **kwargs):
        # Only apply defaults on CREATE (do not override user edits on updates)
        if self.pk is None:
            if self.income_category:
                self.taxable = self.income_category.taxable_default
            # else: leave whatever default/explicit value is already set
        super().save(*args, **kwargs)

    def __str__(self):
        cat = self.income_category.name if self.income_category else self.category
        return f"{self.date} | {cat} | {self.amount}"

class RentalProperty(models.Model):
    name = models.CharField(max_length=100, unique=True)
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class RentalUnitType(models.TextChoices):
    UNIT = "UNIT", "Unit"
    SHARED = "SHARED", "Shared/Common"


class RentalUnit(models.Model):
    property = models.ForeignKey(
        RentalProperty,
        on_delete=models.CASCADE,
        related_name="units",
    )
    name = models.CharField(max_length=100)
    unit_type = models.CharField(
        max_length=10,
        choices=RentalUnitType.choices,
        default=RentalUnitType.UNIT,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["property__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "name"],
                name="uniq_rentalunit_per_property",
            )
        ]

    def __str__(self):
        return f"{self.property.name} – {self.name}"


class CRARentalExpenseCategory(models.Model):
    """
    CRA rental expense classification bucket (e.g. Advertising, Insurance, Property taxes).
    We'll seed common CRA categories in a follow-up data migration.
    """
    name = models.CharField(max_length=100, unique=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name

