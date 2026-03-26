from django.db import models
from django.db.models import Sum
from django.db.models.functions import Coalesce
from datetime import date as dt_date, timedelta
from decimal import Decimal
from django.db.models import SET_NULL
from django.contrib.auth.models import User




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
    is_archived = models.BooleanField(
        default=False,
        help_text="Archived categories are hidden from dashboards and dropdowns but preserved in historical data.",
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

    # Automatic balance tracking
    balance_tracking_enabled = models.BooleanField(
        default=False,
        help_text="Enable automatic balance updates from transactions.",
    )
    balance_tracking_start_date = models.DateField(
        null=True,
        blank=True,
        help_text="Only transactions on/after this date will affect the balance.",
    )

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

    # Optional: how much you aim to contribute each month
    monthly_target = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Desired monthly contribution into this bucket (e.g. 250.00 for insurance).",
    )

    # Optional: where you're trying to get to (overall balance)
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
        "ImportBatch",
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

    # NEW: If this real-world expense is funded from a withholding bucket,
    # we can tag it here. This will let us compute payouts from buckets
    # without faking those payouts as separate WithholdingTransaction rows.
    withholding_category = models.ForeignKey(
        "WithholdingCategory",
        on_delete=SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
        help_text="If this expense is funded from a withholding bucket, select it here.",
    )

    rental_business_use_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Optional: percent (0–100) of this expense that relates to rental use "
            "(useful for mixed-use properties like Foxview)."
        ),
    )

    def __str__(self):
        return f"{self.date} | {self.vendor_name} | {self.amount}"


def expense_attachment_upload_to(instance, filename):
    """
    Generate descriptive filename for expense attachments.
    Format: YYYY-MM-DD_CategoryName_VendorName_$Amount.ext
    Example: 2026-02-15_Home-Repairs_Home-Depot_$125.50.pdf
    """
    import os
    import re
    from datetime import datetime

    expense = instance.expense

    # Date in YYYY-MM-DD format
    date_str = expense.date.strftime("%Y-%m-%d")

    # Category name - sanitize for filesystem (remove special chars, replace spaces with dashes)
    category_name = expense.category.name if expense.category else "Uncategorized"
    category_clean = re.sub(r'[^\w\s-]', '', category_name)
    category_clean = re.sub(r'[-\s]+', '-', category_clean).strip('-')

    # Vendor name - sanitize for filesystem
    vendor_name = expense.vendor_name or "Unknown-Vendor"
    vendor_clean = re.sub(r'[^\w\s-]', '', vendor_name)
    vendor_clean = re.sub(r'[-\s]+', '-', vendor_clean).strip('-')

    # Amount formatted as dollar amount
    amount_str = f"${expense.amount:.2f}"

    # Get file extension from original filename
    _, ext = os.path.splitext(filename)

    # Build the final filename (no original filename, just expense data)
    new_filename = f"{date_str}_{category_clean}_{vendor_clean}_{amount_str}{ext}"

    # Upload to year/month directory based on expense date (for better organization)
    return f"expense_attachments/{expense.date.year}/{expense.date.month:02d}/{new_filename}"


class ExpenseAttachment(models.Model):
    expense = models.ForeignKey(
        "Expense",
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField(upload_to=expense_attachment_upload_to)
    original_name = models.CharField(max_length=255, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at", "-id"]

    def __str__(self):
        return self.original_name or f"Attachment {self.id} (Expense {self.expense_id})"

class Transfer(models.Model):
    """
    A movement of money between two accounts.

    Typical examples:
      - Moving cash from chequing to a withholding/savings account
      - Paying off a credit card from a chequing account
      - Partner contributions into a joint account
      - Internal reshuffling between your own accounts

    CONVENTION:
      - `amount` is always stored as a positive value.
      - Direction is determined by `from_account` and `to_account`.
    """

    date = models.DateField(default=dt_date.today)

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Positive amount of the transfer.",
    )

    description = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional short label (e.g. 'Jenna contribution', 'Move to Arnprior tax bucket').",
    )

    notes = models.TextField(
        blank=True,
        default="",
        help_text="Optional longer notes about this transfer.",
    )

    from_account = models.ForeignKey(
        "BankAccount",
        on_delete=SET_NULL,
        null=True,
        blank=True,
        related_name="outgoing_transfers",
        help_text="Account the money is leaving. Leave blank if this is funding from an external source.",
    )

    to_account = models.ForeignKey(
        "BankAccount",
        on_delete=SET_NULL,
        null=True,
        blank=True,
        related_name="incoming_transfers",
        help_text="Account the money is going into. Leave blank if this is going out to an external destination.",
    )

    # Optional link to a withholding bucket, when this transfer represents
    # a contribution INTO or payout FROM a specific bucket.
    withholding_category = models.ForeignKey(
        "WithholdingCategory",
        on_delete=SET_NULL,
        null=True,
        blank=True,
        related_name="transfers",
        help_text="If this transfer is related to a withholding bucket, select it here.",
    )

    import_batch = models.ForeignKey(
        "ImportBatch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transfers",
        help_text="Optional: if this transfer came from a CSV import batch.",
    )

    # Split transfer fields
    parent_transfer = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='splits',
        help_text='Parent transfer if this is a split'
    )
    is_split_parent = models.BooleanField(
        default=False,
        help_text='True if this transfer has been split into child transfers'
    )
    split_order = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text='Order of split (1, 2, 3...) for display purposes'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "id"]

    def __str__(self):
        parts = [str(self.date), f"${self.amount}"]
        if self.from_account and self.to_account:
            parts.append(f"{self.from_account} → {self.to_account}")
        elif self.from_account:
            parts.append(f"from {self.from_account} → external")
        elif self.to_account:
            parts.append(f"external → {self.to_account}")
        else:
            parts.append("(no accounts)")
        if self.description:
            parts.append(f"– {self.description}")
        return " ".join(parts)

    @property
    def is_split(self) -> bool:
        """Returns True if this transfer is part of a split (parent or child)."""
        return self.is_split_parent or self.parent_transfer is not None

    @property
    def split_count(self) -> int:
        """Returns number of child splits if this is a parent."""
        return self.splits.count() if self.is_split_parent else 0

    @property
    def total_split_amount(self) -> Decimal:
        """Returns sum of all child split amounts."""
        if not self.is_split_parent:
            return Decimal('0')
        return self.splits.aggregate(
            total=Coalesce(Sum('amount'), Decimal('0'))
        )['total']

    def validate_split_amounts(self) -> bool:
        """Validates that split amounts sum to parent amount within tolerance."""
        if not self.is_split_parent:
            return True
        tolerance = Decimal('0.005')
        diff = abs(self.amount - self.total_split_amount)
        return diff <= tolerance

    def can_be_split(self) -> bool:
        """Returns True if transfer can be split."""
        return self.parent_transfer is None and not self.is_split_parent


class BalanceAdjustment(models.Model):
    """
    Manual balance reconciliation adjustment for a bank account.

    Used to reconcile differences between tracked balance and actual bank statement,
    such as bank fees, interest earned, or other items not yet recorded as transactions.

    Provides a full audit trail for all balance changes.
    """
    bank_account = models.ForeignKey(
        BankAccount,
        on_delete=models.CASCADE,
        related_name='balance_adjustments',
        help_text='Account being adjusted',
    )
    date = models.DateField(
        help_text='Date of the adjustment',
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='Adjustment amount (positive = increase balance, negative = decrease balance)',
    )
    reason = models.CharField(
        max_length=255,
        help_text='Brief reason for adjustment (e.g., "Bank reconciliation")',
    )
    notes = models.TextField(
        blank=True,
        default='',
        help_text='Optional detailed notes about this adjustment',
    )
    created_by = models.CharField(
        max_length=100,
        default='System',
        help_text='User who created this adjustment',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ['date', 'id']

    def __str__(self):
        sign = '+' if self.amount >= 0 else ''
        return f"{self.date} - {self.bank_account.name}: {sign}${self.amount} ({self.reason})"


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

    # Estimated market value for equity / LTV
    estimated_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Current estimated market value for this property.",
    )
    last_valued_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date this estimated value was last updated.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def total_mortgage_balance(self):
        """
        Sum of current principal balances across active mortgages for this property.
        """
        total = Decimal("0.00")
        any_balance = False
        for m in self.mortgages.filter(is_active=True):
            bal = m.current_principal_balance
            if bal is not None:
                total += bal
                any_balance = True
        return total if any_balance else None

    @property
    def equity(self):
        """
        Property equity = estimated_value - total_mortgage_balance, if both are known.
        """
        if self.estimated_value is None:
            return None
        total_mortgage = self.total_mortgage_balance
        if total_mortgage is None:
            return None
        return self.estimated_value - total_mortgage

    @property
    def ltv_pct(self):
        """
        Loan-to-value ratio in percent (0–100+) if both value and mortgage balance are known.
        """
        if self.estimated_value is None or self.estimated_value <= 0:
            return None
        total_mortgage = self.total_mortgage_balance
        if total_mortgage is None:
            return None
        return (total_mortgage / self.estimated_value) * Decimal("100.0")



class MortgagePaymentFrequency(models.TextChoices):
    MONTHLY = "MONTHLY", "Monthly"
    BIWEEKLY = "BIWEEKLY", "Biweekly"
    ACCELERATED_BIWEEKLY = "ACCEL_BIWEEKLY", "Accelerated biweekly"
    WEEKLY = "WEEKLY", "Weekly"


class MortgageCompoundingFrequency(models.TextChoices):
    SEMI_ANNUAL = "SEMI_ANNUAL", "Semi-annual (Canadian standard)"
    MONTHLY = "MONTHLY", "Monthly"
    ANNUAL = "ANNUAL", "Annual"
    OTHER = "OTHER", "Other / Unknown"


class PropertyMortgage(models.Model):
    """
    Mortgage tied to an owned property (currently your RentalProperty model).

    Design goals:
    - Track where you started: original principal, origination date.
    - Track where you started "proper tracking" in this app: tracking_start_principal/date.
    - Compute current balance by subtracting principal-paid since tracking_start_date.
    - Link explicit principal / prepayment / interest categories.
    - Provide progress helpers for principal-based and time-based amortization.
    """

    owned_property = models.ForeignKey(
        RentalProperty,
        on_delete=models.CASCADE,
        related_name="mortgages",
    )

    # Friendly identifiers
    name = models.CharField(
        max_length=100,
        help_text="Short label, e.g. 'Scotia 5yr fixed' or 'TD Arnprior Mortgage'.",
    )
    lender_name = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Optional: lender name (e.g. Scotiabank, TD).",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Uncheck if this mortgage has been fully paid off or refinanced.",
    )

    # --- Original mortgage info (historical context) ---

    original_principal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Mortgage amount at origination (optional but recommended).",
    )
    origination_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date the mortgage originally started.",
    )

    # --- Tracking start (for accurate ongoing balance in this app) ---

    tracking_start_principal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Outstanding principal when you started tracking in this app. "
            "Typically this should match your lender balance on the tracking start date."
        ),
    )
    tracking_start_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date that the tracking_start_principal corresponds to.",
    )

    manual_adjustment = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=(
            "Optional tweak to reconcile against lender balance if there are "
            "small differences. Applied as: current = tracked - principal_paid + adjustment."
        ),
    )

    # --- Amortization / schedule ---

    amortization_years_total = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Total amortization in years (e.g. 25).",
    )
    amortization_months_extra = models.PositiveSmallIntegerField(
        default=0,
        help_text="Extra months beyond whole years in the amortization (e.g. 6 for 25.5 years).",
    )
    amortization_start_date = models.DateField(
        null=True,
        blank=True,
        help_text="Start date for the amortization schedule (often the origination date).",
    )

    payment_frequency = models.CharField(
        max_length=20,
        choices=MortgagePaymentFrequency.choices,
        default=MortgagePaymentFrequency.MONTHLY,
        help_text="How often regular payments are made.",
    )
    regular_payment_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Contractual payment amount (principal + interest) per payment.",
    )

    # --- Interest rate & term ---

    interest_rate_percent = models.DecimalField(
        max_digits=5,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Current nominal annual interest rate (e.g. 5.490 means 5.490%).",
    )
    compounding_frequency = models.CharField(
        max_length=20,
        choices=MortgageCompoundingFrequency.choices,
        default=MortgageCompoundingFrequency.OTHER,
        help_text="Compounding convention (Canadian mortgages are usually semi-annual).",
    )
    interest_rate_effective_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date this interest rate took effect (if known).",
    )

    term_start_date = models.DateField(
        null=True,
        blank=True,
        help_text="Start date of the current term.",
    )
    term_end_date = models.DateField(
        null=True,
        blank=True,
        help_text="End date of the current term.",
    )

    # --- Category linkage (principal / prepayment / interest) ---

    principal_category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="principal_mortgages",
        help_text="Category used for the principal portion of regular mortgage payments.",
    )
    prepayment_category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prepayment_mortgages",
        help_text="Optional: category used for extra principal prepayments, if any.",
    )
    interest_category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interest_mortgages",
        help_text="Category used for the interest portion of mortgage payments.",
    )

    class Meta:
        ordering = ["owned_property__name", "name"]

    def __str__(self):
        base = f"{self.owned_property.name} – {self.name}"
        return base

    # ----- Helper methods / properties -----

    def principal_categories(self):
        """
        Categories that count as principal reduction for this mortgage:
        - Regular principal category (required for live tracking)
        - Optional prepayment category
        """
        cats = []
        if self.principal_category_id:
            cats.append(self.principal_category)
        if self.prepayment_category_id:
            cats.append(self.prepayment_category)
        return cats

    def _principal_queryset_since_tracking_start(self):
        """
        Internal helper: Expense queryset for principal-like categories
        since the tracking_start_date. Returns an empty queryset if we
        don't have enough configuration yet.
        """
        if not self.tracking_start_date:
            return Expense.objects.none()
        cats = self.principal_categories()
        if not cats:
            return Expense.objects.none()
        return Expense.objects.filter(
            category__in=cats,
            date__gte=self.tracking_start_date,
        )

    @property
    def principal_paid_since_tracking_start(self) -> Decimal:
        """
        Total principal paid since tracking_start_date, based on:
        - principal_category
        - prepayment_category (if set)
        """
        qs = self._principal_queryset_since_tracking_start()
        return qs.aggregate(
            total=Coalesce(Sum("amount"), Decimal("0.00"))
        )["total"]

    @property
    def current_principal_balance(self):
        """
        Estimated outstanding principal today.

        Semantics:

        - tracking_start_principal is a known lender balance that ALREADY
          includes all payments on tracking_start_date.
        - We subtract principal from any expenses STRICTLY AFTER that date.
        - manual_adjustment lets you nudge the computed balance if needed.
        """
        # Base starting point: prefer tracking_start_principal, fall back to original_principal
        base = self.tracking_start_principal or self.original_principal
        if base is None:
            return None

        principal_categories = self.principal_categories()
        if not principal_categories:
            # No categories configured; just return the base value.
            return base

        from .models import Expense  # if you already import Expense at top, you can omit this

        if self.tracking_start_date:
            # IMPORTANT: strictly greater than tracking_start_date
            principal_qs = Expense.objects.filter(
                category__in=principal_categories,
                date__gt=self.tracking_start_date,
            )
        else:
            principal_qs = Expense.objects.filter(
                category__in=principal_categories
            )

        principal_since_start = principal_qs.aggregate(
            total=Coalesce(Sum("amount"), Decimal("0.00"))
        )["total"]

        return (
                base
                - principal_since_start
                + (self.manual_adjustment or Decimal("0.00"))
        )

    def principal_balance_as_of(self, as_of_date):
        """
        Estimated outstanding principal as of a specific date.
        Same logic as current_principal_balance but caps principal payments
        at as_of_date (inclusive).
        """
        base = self.tracking_start_principal or self.original_principal
        if base is None:
            return None

        principal_categories = self.principal_categories()
        if not principal_categories:
            return base

        qs = Expense.objects.filter(
            category__in=principal_categories,
            date__lte=as_of_date,
        )
        if self.tracking_start_date:
            qs = qs.filter(date__gt=self.tracking_start_date)

        principal_paid = qs.aggregate(
            total=Coalesce(Sum("amount"), Decimal("0.00"))
        )["total"]

        return (
            base
            - principal_paid
            + (self.manual_adjustment or Decimal("0.00"))
        )

    @property
    def interest_paid_current_year(self) -> Decimal:
        """
        Interest paid in the current calendar year (based on interest_category).
        """
        if not self.interest_category_id:
            return Decimal("0.00")
        today = dt_date.today()
        qs = Expense.objects.filter(
            category=self.interest_category,
            date__year=today.year,
        )
        return qs.aggregate(
            total=Coalesce(Sum("amount"), Decimal("0.00"))
        )["total"]

    @property
    def total_amortization_months(self):
        """
        Total amortization duration in months, if known.
        """
        if self.amortization_years_total is None:
            return None
        extra = self.amortization_months_extra or 0
        return self.amortization_years_total * 12 + extra

    @property
    def amortization_months_elapsed(self):
        """
        Rough count of months elapsed since amortization_start_date.
        """
        if not self.amortization_start_date or not self.total_amortization_months:
            return None
        today = dt_date.today()
        # Approximate month difference (year, month granularity)
        months = (today.year - self.amortization_start_date.year) * 12 + (
            today.month - self.amortization_start_date.month
        )
        # Clamp at [0, total_amortization_months]
        if months < 0:
            months = 0
        total = self.total_amortization_months
        if months > total:
            months = total
        return months

    @property
    def amortization_time_progress_pct(self):
        """
        Fraction of the amortization completed based on time (0–100).
        """
        total = self.total_amortization_months
        elapsed = self.amortization_months_elapsed
        if total is None or elapsed is None or total <= 0:
            return None
        return (Decimal(elapsed) / Decimal(total)) * Decimal("100.0")

    @property
    def principal_progress_pct(self):
        """
        Fraction of original principal that has been paid off (0–100),
        if both original_principal and current_principal_balance are known.
        """
        if not self.original_principal or self.original_principal <= 0:
            return None
        current = self.current_principal_balance
        if current is None:
            return None
        paid = self.original_principal - current
        if paid < 0:
            # If tracking_start_principal was mid-stream, this can be negative initially.
            return Decimal("0.00")
        return (paid / self.original_principal) * Decimal("100.0")

    def projected_rows_to_year_end(self, starting_balance, last_payment_date, year):
        """
        Generate simple projected payment rows from the payment after `last_payment_date`
        up to 31-Dec-`year`, assuming:
        - payments of regular_payment_amount
        - interest_rate_percent is nominal annual rate (e.g. 5.490 for 5.490%)
        - per-period interest = balance * (rate_annual / payments_per_year)
          so principal = payment - interest

        This is an approximation but good enough for forward-looking visuals.
        """
        if starting_balance is None:
            return []
        if not self.regular_payment_amount or not self.interest_rate_percent:
            return []
        if last_payment_date is None:
            return []

        # Determine payments per year based on frequency
        if self.payment_frequency == MortgagePaymentFrequency.MONTHLY:
            payments_per_year = 12
        elif self.payment_frequency in (
            MortgagePaymentFrequency.BIWEEKLY,
            MortgagePaymentFrequency.ACCELERATED_BIWEEKLY,
        ):
            payments_per_year = 26
        elif self.payment_frequency == MortgagePaymentFrequency.WEEKLY:
            payments_per_year = 52
        else:
            payments_per_year = 12

        payment_amount = self.regular_payment_amount
        rate_annual = (self.interest_rate_percent or Decimal("0.0")) / Decimal("100.0")
        rate_period = rate_annual / Decimal(payments_per_year)

        def add_months(d: dt_date, n: int) -> dt_date:
            y = d.year + (d.month - 1 + n) // 12
            m = (d.month - 1 + n) % 12 + 1
            # clamp day to 28 to avoid month-end issues
            day = min(d.day, 28)
            return dt_date(y, m, day)

        def next_payment_date(d: dt_date) -> dt_date:
            if self.payment_frequency == MortgagePaymentFrequency.MONTHLY:
                return add_months(d, 1)
            elif self.payment_frequency in (
                MortgagePaymentFrequency.BIWEEKLY,
                MortgagePaymentFrequency.ACCELERATED_BIWEEKLY,
            ):
                return d + timedelta(days=14)
            elif self.payment_frequency == MortgagePaymentFrequency.WEEKLY:
                return d + timedelta(days=7)
            else:
                return add_months(d, 1)

        year_end = dt_date(year, 12, 31)
        rows = []
        balance = starting_balance
        current_date = last_payment_date

        while True:
            current_date = next_payment_date(current_date)
            if current_date > year_end:
                break

            # simple interest per period
            interest = (balance * rate_period).quantize(Decimal("0.01"))
            principal = payment_amount - interest
            if principal < Decimal("0.00"):
                principal = Decimal("0.00")

            balance = balance - principal

            rows.append(
                {
                    "date": current_date,
                    "payment_type": "Projected",
                    "principal": principal,
                    "interest": interest,
                    "payment_total": payment_amount,
                    "balance_after": balance,
                }
            )

        return rows


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


class MonthEndClose(models.Model):
    """
    Represents a closed month with locked transactions and financial snapshots.
    Used for month-end closing process to maintain data integrity and track historical balances.
    """
    month = models.DateField(
        unique=True,
        help_text="First day of the closed month (e.g., 2024-01-01 for January 2024)"
    )
    closed_at = models.DateTimeField(auto_now_add=True)
    closed_by = models.CharField(max_length=100, default="System")
    backup_file = models.CharField(
        max_length=255,
        blank=True,
        help_text="Path to backup file created during close"
    )
    notes = models.TextField(blank=True)

    # Financial summary at time of close
    total_income = models.DecimalField(max_digits=12, decimal_places=2)
    total_expenses = models.DecimalField(max_digits=12, decimal_places=2)
    net_savings = models.DecimalField(max_digits=12, decimal_places=2)
    total_transfers = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    transaction_count = models.IntegerField(default=0)

    # Enhanced financial breakdown (added for detailed month-end analysis)
    planned_budget_total = models.DecimalField(
        max_digits=12, decimal_places=2,
        default=0,
        help_text="Total budget for planned expense categories (with monthly_limit)"
    )
    planned_spent_total = models.DecimalField(
        max_digits=12, decimal_places=2,
        default=0,
        help_text="Total spent in planned expense categories"
    )
    unplanned_spent_total = models.DecimalField(
        max_digits=12, decimal_places=2,
        default=0,
        help_text="Total spent in unplanned expense categories (without monthly_limit)"
    )
    withholding_target_total = models.DecimalField(
        max_digits=12, decimal_places=2,
        default=0,
        help_text="Total monthly targets for withholding buckets"
    )
    withholding_actual_total = models.DecimalField(
        max_digits=12, decimal_places=2,
        default=0,
        help_text="Total contributions to withholding buckets"
    )
    excess_saved = models.DecimalField(
        max_digits=12, decimal_places=2,
        default=0,
        help_text="Amount saved to Excess/Surplus bucket"
    )

    # Lock control
    is_locked = models.BooleanField(
        default=True,
        help_text="When locked, transactions in this month cannot be edited or deleted"
    )

    # Reopening audit trail
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.CharField(max_length=100, blank=True)
    reopen_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-month']
        verbose_name = "Month-End Close"
        verbose_name_plural = "Month-End Closes"

    def __str__(self):
        return f"{self.month.strftime('%B %Y')} - {'Locked' if self.is_locked else 'Reopened'}"

    @property
    def month_display(self):
        return self.month.strftime('%B %Y')


class AccountSnapshot(models.Model):
    """
    Snapshot of a bank account balance at month-end.
    Tracks account balances over time for historical reporting.
    """
    month_close = models.ForeignKey(
        MonthEndClose,
        on_delete=models.CASCADE,
        related_name='account_snapshots'
    )
    bank_account = models.ForeignKey(
        'BankAccount',
        on_delete=models.CASCADE,
        related_name='historical_snapshots'
    )
    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Account balance at month-end"
    )
    snapshot_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-snapshot_date']
        unique_together = ['month_close', 'bank_account']

    def __str__(self):
        return f"{self.bank_account.name} - {self.month_close.month_display}: ${self.balance}"


class NetWorthSnapshot(models.Model):
    """
    Snapshot of total net worth at month-end.
    Tracks overall financial health over time.
    """
    month_close = models.ForeignKey(
        MonthEndClose,
        on_delete=models.CASCADE,
        related_name='net_worth_snapshot'
    )

    # Total net worth calculation
    total_net_worth = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Total assets minus liabilities"
    )

    # Asset breakdown
    liquid_assets = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Cash and easily convertible assets (bank accounts)"
    )
    investment_assets = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Stocks, bonds, retirement accounts"
    )
    property_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Real estate and property values"
    )

    # Liabilities
    liabilities = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Loans, credit cards, mortgages"
    )

    snapshot_date = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-snapshot_date']

    def __str__(self):
        return f"{self.month_close.month_display}: ${self.total_net_worth}"


class MonthEndExpenseCategorySnapshot(models.Model):
    """
    Snapshot of each expense category's monthly_limit and actual spending at month-end.
    Preserves historical target values so changing a limit today does not corrupt closed-month views.
    """
    month_close = models.ForeignKey(
        MonthEndClose, on_delete=models.CASCADE,
        related_name='expense_snapshots'
    )
    category = models.ForeignKey(
        'Category', on_delete=models.PROTECT,
        related_name='monthly_snapshots'
    )
    monthly_limit = models.DecimalField(max_digits=10, decimal_places=2)
    actual_spent = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        unique_together = ['month_close', 'category']
        ordering = ['category__name']

    def __str__(self):
        return f"{self.category.name} - {self.month_close.month_display}: limit ${self.monthly_limit}"


class MonthEndWithholdingCategorySnapshot(models.Model):
    """
    Snapshot of each withholding bucket's monthly_target and actual contributions at month-end.
    Preserves historical target values so changing a target today does not corrupt closed-month views.
    """
    month_close = models.ForeignKey(
        MonthEndClose, on_delete=models.CASCADE,
        related_name='withholding_snapshots'
    )
    withholding_category = models.ForeignKey(
        'WithholdingCategory', on_delete=models.PROTECT,
        related_name='monthly_snapshots'
    )
    monthly_target = models.DecimalField(max_digits=12, decimal_places=2)
    actual_contributed = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        unique_together = ['month_close', 'withholding_category']
        ordering = ['withholding_category__name']

    def __str__(self):
        return f"{self.withholding_category.name} - {self.month_close.month_display}: target ${self.monthly_target}"


class MonthEndIncomeCategorySnapshot(models.Model):
    """
    Snapshot of each income category's monthly_target and actual income received at month-end.
    Preserves historical target values so changing a target today does not corrupt closed-month views.
    """
    month_close = models.ForeignKey(
        MonthEndClose, on_delete=models.CASCADE,
        related_name='income_snapshots'
    )
    income_category = models.ForeignKey(
        'IncomeCategory', on_delete=models.PROTECT,
        related_name='monthly_snapshots'
    )
    monthly_target = models.DecimalField(max_digits=10, decimal_places=2)
    actual_received = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        unique_together = ['month_close', 'income_category']
        ordering = ['income_category__name']

    def __str__(self):
        return f"{self.income_category.name} - {self.month_close.month_display}: target ${self.monthly_target}"


class ForecastWorksheet(models.Model):
    """
    Persists per-month forecast worksheet adjustments.
    Stores only the delta from DB state (overrides, excluded rows, new projected rows)
    so fresh actual transactions always appear correctly on the next visit.
    """
    month = models.DateField(unique=True, help_text="First day of the forecast month")
    state = models.JSONField(default=dict, help_text="Delta state: overrides, excluded, new_rows")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-month']

    def __str__(self):
        return f"Forecast {self.month.strftime('%B %Y')}"


class WebAuthnCredential(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="webauthn_credentials")
    credential_id = models.BinaryField(unique=True)
    public_key = models.BinaryField()
    sign_count = models.PositiveBigIntegerField(default=0)
    device_name = models.CharField(max_length=100, default="Passkey")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} — {self.device_name}"


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    pinned_categories = models.ManyToManyField(
        "Category",
        blank=True,
        help_text="Expense categories shown on this user's dashboard. Leave empty to show all.",
    )
    pinned_income_categories = models.ManyToManyField(
        "IncomeCategory",
        blank=True,
        help_text="Income categories shown on this user's dashboard. Leave empty to show all.",
    )
    pinned_withholding_categories = models.ManyToManyField(
        "WithholdingCategory",
        blank=True,
        help_text="Withholding buckets shown on this user's dashboard. Leave empty to show all.",
    )

    def __str__(self):
        return f"Profile: {self.user.username}"
