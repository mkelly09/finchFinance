from django.db import models
from django.db.models import Sum
from django.db.models.functions import Coalesce
from datetime import date as dt_date, timedelta
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

