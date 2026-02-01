from django.contrib import admin
from .models import (
    Category,
    IncomeCategory,
    BankAccount,
    WithholdingCategory,
    WithholdingTransaction,
    ImportBatch,
    Expense,
    ExpenseAttachment,
    Income,
    RentalProperty,
    PropertyMortgage,
)


# ---------- CATEGORY ----------

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "monthly_limit", "savings_target_per_paycheque")
    search_fields = ("name",)


# ---------- INCOME CATEGORY ----------

@admin.register(IncomeCategory)
class IncomeCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "monthly_target", "taxable_default")
    search_fields = ("name",)


# ---------- BANK ACCOUNTS & WITHHOLDINGS ----------

@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "institution",
        "account_type",
        "is_withholding_account",
        "current_balance",
        "withholding_total",
        "unallocated_balance",
        "is_active",
        "account_number_last4",
    )
    list_filter = ("account_type", "is_withholding_account", "is_active")
    search_fields = ("name", "institution", "account_number_last4")


class WithholdingTransactionInline(admin.TabularInline):
    """
    Allows you to edit transactions directly on the WithholdingCategory page.
    """
    model = WithholdingTransaction
    extra = 1
    ordering = ("-date",)
    fields = ("date", "amount", "note")
    # you can set 'show_change_link = True' if you want links to detail view


@admin.register(WithholdingCategory)
class WithholdingCategoryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "account",
        "balance",
        "target_amount",
        "next_due_date",
    )
    list_filter = ("account",)
    search_fields = ("name",)
    inlines = [WithholdingTransactionInline]


@admin.register(WithholdingTransaction)
class WithholdingTransactionAdmin(admin.ModelAdmin):
    list_display = ("date", "category", "amount", "note", "created_at")
    list_filter = ("category", "date")
    search_fields = ("note",)
    ordering = ("-date", "-id")


# ---------- PROPERTIES & MORTGAGES ----------

class PropertyMortgageInline(admin.TabularInline):
    """
    Inline editor for mortgages on the property admin.
    Lets you see/edit the main mortgage metadata directly on the property.
    """
    model = PropertyMortgage
    extra = 0
    fields = (
        "name",
        "lender_name",
        "is_active",
        "original_principal",
        "tracking_start_principal",
        "tracking_start_date",
        "interest_rate_percent",
        "term_end_date",
    )
    readonly_fields = ()
    show_change_link = True


@admin.register(RentalProperty)
class RentalPropertyAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "estimated_value", "equity_display")
    search_fields = ("name",)
    list_filter = ("is_active",)
    inlines = [PropertyMortgageInline]

    def equity_display(self, obj):
        eq = obj.equity
        if eq is None:
            return "—"
        return f"${eq:,.2f}"

    equity_display.short_description = "Equity"



@admin.register(PropertyMortgage)
class PropertyMortgageAdmin(admin.ModelAdmin):
    """
    Standalone admin for mortgages. You can also edit them inline on the property.
    """

    list_display = (
        "owned_property",
        "name",
        "lender_name",
        "is_active",
        "current_balance_display",
        "interest_rate_percent",
        "term_end_date",
    )
    list_filter = (
        "owned_property",
        "is_active",
        "payment_frequency",
        "compounding_frequency",
        "term_end_date",
    )
    search_fields = ("name", "lender_name")
    ordering = ("owned_property__name", "name")

    fieldsets = (
        ("Property & identity", {
            "fields": (
                "owned_property",
                "name",
                "lender_name",
                "is_active",
            )
        }),
        ("Original mortgage", {
            "fields": (
                "original_principal",
                "origination_date",
            )
        }),
        ("Tracking start (for in-app balance)", {
            "fields": (
                "tracking_start_principal",
                "tracking_start_date",
                "manual_adjustment",
                "current_balance_display",
            )
        }),
        ("Amortization & schedule", {
            "fields": (
                "amortization_years_total",
                "amortization_months_extra",
                "amortization_start_date",
                "payment_frequency",
                "regular_payment_amount",
            )
        }),
        ("Interest & term", {
            "fields": (
                "interest_rate_percent",
                "compounding_frequency",
                "interest_rate_effective_date",
                "term_start_date",
                "term_end_date",
            )
        }),
        ("Categories (expense linkage)", {
            "fields": (
                "principal_category",
                "prepayment_category",
                "interest_category",
            )
        }),
    )

    readonly_fields = ("current_balance_display",)

    def current_balance_display(self, obj):
        """
        Nicely formatted current principal balance for admin display.
        """
        balance = obj.current_principal_balance
        if balance is None:
            return "—"
        # You can tweak formatting here if you like
        return f"${balance:,.2f}"

    current_balance_display.short_description = "Current balance"


# ---------- IMPORT BATCHES ----------

class ExpenseInline(admin.TabularInline):
    model = Expense
    extra = 0
    fields = ("date", "vendor_name", "category", "amount", "location")
    readonly_fields = ("date", "vendor_name", "category", "amount", "location")
    can_delete = False


class IncomeInline(admin.TabularInline):
    model = Income
    extra = 0
    fields = ("date", "amount", "category", "taxable")
    readonly_fields = ("date", "amount", "category", "taxable")
    can_delete = False


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "bank_account",
        "imported_at",
        "earliest_date",
        "latest_date",
        "total_transactions",
        "total_income_amount",
        "total_expense_amount",
        "net_amount",
        "filename",
    )
    list_filter = ("bank_account", "imported_at")
    search_fields = ("filename",)
    ordering = ("-imported_at",)
    inlines = [ExpenseInline, IncomeInline]


# ---------- EXPENSES & INCOME ----------

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "vendor_name",
        "category",
        "amount",
        "location",
        "bank_account",
        "import_batch",
    )
    list_filter = ("category", "bank_account", "location", "date")
    search_fields = ("vendor_name", "notes", "location")
    ordering = ("-date",)


@admin.register(Income)
class IncomeAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "category",
        "amount",
        "taxable",
        "bank_account",
        "import_batch",
    )
    list_filter = ("category", "taxable", "bank_account", "date")
    search_fields = ("notes",)
    ordering = ("-date",)


@admin.register(ExpenseAttachment)
class ExpenseAttachmentAdmin(admin.ModelAdmin):
    list_display = ("id", "expense", "original_name", "uploaded_at")
    list_filter = ("uploaded_at",)
    search_fields = ("original_name", "expense__vendor_name")
