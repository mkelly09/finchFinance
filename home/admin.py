from django.contrib import admin
from .models import (
    Category,
    IncomeCategory,
    BankAccount,
    WithholdingCategory,
    WithholdingTransaction,
    ImportBatch,
    Expense,
    Income,
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
