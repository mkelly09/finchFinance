import csv
import io
import calendar
from calendar import monthrange
from collections import defaultdict
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from django.db import IntegrityError

from django.contrib import messages
from django.db.models import Sum, F, Value, DecimalField, ExpressionWrapper, Case, When, Count
from django.forms import ModelForm, formset_factory
from django.http import HttpResponseBadRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
from django.conf import settings
from django.urls import reverse
from django.db.models.functions import Coalesce

from .forms import TransactionForm, CSVUploadForm, TransactionImportForm, ExpenseEditForm, ExpenseAttachmentUploadForm, WithholdingPayoutForm
from .models import (
    Expense,
    ExpenseAttachment,
    Income,
    Category,
    IncomeCategory,
    BankAccount,
    ImportBatch,
    WithholdingCategory,
    WithholdingTransaction,


    # ✅ Rental / CRA additions
    RentalProperty,
    RentalUnit,
    CRARentalExpenseCategory,
)

TransactionImportFormSet = formset_factory(TransactionImportForm, extra=0)

# --- Auto-mapping rules ---

EXPLICIT_EXPENSE_KEYWORD_CATEGORY_NAMES = {
    "GORE MUTUAL": "Subaru Insurance",
    "TD INS": "Arnprior Insurance",
    "FIDO MOBILE": "Cell Phone",
    "BELL CANADA": "Arnprior Internet",
    "LOBLAWS": "Groceries",
    "FOODLAND": "Groceries",
    "COSTCO": "Groceries",
    "ULTRAMAR": "Gas",
    "TIM HORTONS": "Restaurants",
    "STARBUCKS": "Restaurants",
    "TST-HUNTERS": "Restaurants",
    "WAL-MART": "Groceries",
    "PIZZA": "Restaurants",
    "MCDONALD": "Restaurants",
    "NO GO COFFEE": "Restaurants",
    "SHAWARMA": "Restaurants",
    "STINSON": "Foxview Heat",      # covers "STINSON AND SON"
    "FAT LES": "Restaurants",
    "ENBRIDGE": "Arnprior Heat",

    # New mappings requested
    "NETFLIX": "Digital Subscriptions",
    "CHATGPT": "Digital Subscriptions",
    "SPOTIFY": "Digital Subscriptions",
    "NOTION": "Digital Subscriptions",
    "CARLETON": "Golf",
    "FARM BOY": "Groceries",
    "AMAZON": "Miscellaneous",
}

EXACT_ETFR_SNOW_REMOVAL_AMOUNT = Decimal("180.80")


def get_category_cached(name, cache, missing_set):
    if not name:
        return None

    if name in cache:
        return cache[name]
    if name in missing_set:
        return None

    try:
        cat = Category.objects.get(name=name)
        cache[name] = cat
        return cat
    except Category.DoesNotExist:
        missing_set.add(name)
        return None


def apply_income_rules(desc_upper, amount, entry_type_default, parsed_date=None):
    """
    Income inference rules used only during import parsing.

    Important safety constraints:
    - Only runs when entry_type_default is already 'income' (deposit column)
    - E-TRANSFER heuristic triggers only when 'E-TRANSFER' is in description
    - GLOBALIZATION is always Employment Income
    """
    entry_type = entry_type_default
    income_source = ""

    # Employment income (explicit)
    if "GLOBALIZATION" in desc_upper:
        return "income", "Employment Income"

    # Only infer rental income on deposits that contain E-TRANSFER (substring match; unique codes ok)
    if "E-TRANSFER" in desc_upper and entry_type_default == "income":
        # If we have a date, enforce "around the 1st of month" window (± ~1.5 weeks)
        in_window = True
        if parsed_date:
            first_of_month = date(parsed_date.year, parsed_date.month, 1)
            in_window = abs((parsed_date - first_of_month).days) <= 11  # ~1.5 weeks

        if in_window:
            # Amount heuristics (±5%)
            main_target = Decimal("2500")
            loft_target = Decimal("1600")

            main_low = main_target * Decimal("0.95")
            main_high = main_target * Decimal("1.05")

            loft_low = loft_target * Decimal("0.95")
            loft_high = loft_target * Decimal("1.05")

            if main_low <= amount <= main_high:
                return "income", "Arnprior Rental Income (MAIN)"
            if loft_low <= amount <= loft_high:
                return "income", "Arnprior Rental Income (LOFT)"

        # Fallback (your existing loose thresholds) — still only for E-TRANSFER deposits
        if Decimal("2000") <= amount <= Decimal("2700"):
            return "income", "Arnprior Rental Income (MAIN)"
        elif amount < Decimal("2000"):
            return "income", "Arnprior Rental Income (LOFT)"

    return entry_type, income_source

def apply_expense_rules(desc_upper, amount, category_cache, missing_categories):
    if "E-TFR" in desc_upper and amount == EXACT_ETFR_SNOW_REMOVAL_AMOUNT:
        return get_category_cached("Arnprior Snow Removal", category_cache, missing_categories)

    for keyword, cat_name in EXPLICIT_EXPENSE_KEYWORD_CATEGORY_NAMES.items():
        if keyword in desc_upper:
            cat = get_category_cached(cat_name, category_cache, missing_categories)
            if cat:
                return cat

    return None

def get_arnprior_shared_unit_id():
    """
    Best-effort lookup for the Arnprior shared/common unit.
    Returns RentalUnit.id or None (never raises).
    SQLite-safe: uses icontains instead of regex.
    """
    qs = RentalUnit.objects.select_related("property").filter(property__name__iexact="Arnprior")
    unit = (
        qs.filter(name__icontains="shared").order_by("name").first()
        or qs.filter(name__icontains="common").order_by("name").first()
    )
    return unit.id if unit else None

def build_income_rental_unit_map():
    """
    Used by import review UI to display inferred rental unit for an IncomeCategory.
    Returns: { "<income_category_id>": "Property — Unit" }
    """
    m = {}
    for ic in IncomeCategory.objects.select_related("default_rental_unit__property").all():
        if ic.default_rental_unit_id:
            m[str(ic.id)] = f"{ic.default_rental_unit.property.name} — {ic.default_rental_unit.name}"
    return m

def dashboard(request):
    today = date.today()
    selected_month_str = request.GET.get("month", today.strftime("%Y-%m"))

    try:
        year, month = map(int, selected_month_str.split("-"))
    except ValueError:
        year, month = today.year, today.month

    selected_date = date(year, month, 1)
    first_day = date(year, month, 1)
    last_day = date(year, month, monthrange(year, month)[1])
    selected_month_display = first_day.strftime("%B %Y")

    if request.method == "POST":
        # -------------------------
        # Expense modal edit/delete
        # -------------------------
        if "expense_id" in request.POST:
            expense = get_object_or_404(Expense, pk=request.POST["expense_id"])
            if "delete_expense" in request.POST:
                expense.delete()
            else:
                expense.date = datetime.strptime(request.POST["date"], "%Y-%m-%d").date()
                expense.vendor_name = request.POST["vendor_name"]

                category_name = request.POST["category"]
                expense.category = get_object_or_404(Category, name=category_name)

                expense.location = request.POST.get("location", "Ottawa")
                expense.amount = Decimal(request.POST["amount"])
                expense.notes = request.POST.get("notes", "")

                # ✅ NEW: Rental fields (optional)
                rental_unit_id = (request.POST.get("rental_unit") or "").strip()
                if rental_unit_id:
                    expense.rental_unit = get_object_or_404(RentalUnit, pk=rental_unit_id)
                else:
                    expense.rental_unit = None

                cra_category_id = (request.POST.get("cra_category") or "").strip()
                if cra_category_id:
                    expense.cra_category = get_object_or_404(CRARentalExpenseCategory, pk=cra_category_id)
                else:
                    expense.cra_category = None

                pct_str = (request.POST.get("rental_business_use_pct") or "").strip()
                if pct_str:
                    expense.rental_business_use_pct = Decimal(pct_str)
                else:
                    expense.rental_business_use_pct = None

                expense.save()

            selected_month_param = f"{expense.date.year:04d}-{expense.date.month:02d}"
            return redirect(f"/?month={selected_month_param}")

        # -------------------------
        # Income modal edit/delete
        # -------------------------
        elif "income_id" in request.POST:
            income = get_object_or_404(Income, pk=request.POST["income_id"])
            if "delete_income" in request.POST:
                income.delete()
            else:
                income.date = datetime.strptime(request.POST["date"], "%Y-%m-%d").date()

                source_id = request.POST.get("source")
                income_cat = get_object_or_404(IncomeCategory, pk=source_id) if source_id else None

                income.income_category = income_cat
                if income_cat:
                    income.category = income_cat.name  # legacy sync for now

                income.amount = Decimal(request.POST["amount"])
                income.taxable = request.POST.get("taxable") == "1"
                income.notes = request.POST.get("notes", "")
                rental_unit_id = (request.POST.get("rental_unit") or "").strip()
                # If user didn't provide rental_unit, fall back to the category's default (if any)
                if not rental_unit_id and income_cat and income_cat.default_rental_unit_id:
                    income.rental_unit = income_cat.default_rental_unit
                else:
                    if rental_unit_id:
                        income.rental_unit = get_object_or_404(RentalUnit, pk=rental_unit_id)
                    else:
                        income.rental_unit = None

                income.save()

            selected_month_param = f"{income.date.year:04d}-{income.date.month:02d}"
            return redirect(f"/?month={selected_month_param}")

        # -------------------------
        # New transaction (Add Transaction form)
        # -------------------------
        else:
            form = TransactionForm(request.POST)
            if form.is_valid():
                entry_type = form.cleaned_data["entry_type"]
                entry_date = form.cleaned_data["date"]
                amount = form.cleaned_data["amount"]
                notes = form.cleaned_data["notes"]


                if entry_type == "income":
                    source = form.cleaned_data["source"]  # IncomeCategory instance or None
                    income_rental_unit = form.cleaned_data.get("income_rental_unit")
                    if not income_rental_unit and source and source.default_rental_unit_id:
                        income_rental_unit = source.default_rental_unit

                    income_exists = Income.objects.filter(
                        date=entry_date,
                        amount=amount,
                        income_category=source,
                    ).exists()

                    if income_exists:
                        messages.warning(request, "This income transaction already exists and was not added again.")
                    else:
                        Income.objects.create(
                            date=entry_date,
                            amount=amount,
                            income_category=source,
                            category=source.name if source else "",  # legacy sync
                            taxable=form.cleaned_data["taxable"],
                            notes=notes,
                            rental_unit=income_rental_unit,
                        )

                else:
                    vendor_name = form.cleaned_data["vendor_name"]
                    category = form.cleaned_data["category"]
                    location = form.cleaned_data.get("location", "Ottawa")

                    apply_to_withholding = form.cleaned_data.get("apply_to_withholding")
                    withholding_category = form.cleaned_data.get("withholding_category")

                    # ✅ NEW: rental fields from TransactionForm
                    rental_unit = form.cleaned_data.get("rental_unit")
                    cra_category = form.cleaned_data.get("cra_category")
                    rental_pct = form.cleaned_data.get("rental_business_use_pct")

                    expense_exists = Expense.objects.filter(
                        date=entry_date,
                        amount=amount,
                        vendor_name=vendor_name,
                        category=category,
                    ).exists()

                    if expense_exists:
                        messages.warning(request, "This expense transaction already exists and was not added again.")
                    else:
                        Expense.objects.create(
                            date=entry_date,
                            amount=amount,
                            vendor_name=vendor_name,
                            category=category,
                            location=location,
                            notes=notes,

                            # ✅ NEW fields persisted
                            rental_unit=rental_unit,
                            cra_category=cra_category,
                            rental_business_use_pct=rental_pct,
                        )

                        if apply_to_withholding and withholding_category:
                            WithholdingTransaction.objects.create(
                                category=withholding_category,
                                date=entry_date,
                                amount=amount,
                                note=notes or vendor_name or "",
                            )

                selected_month_param = f"{year:04d}-{month:02d}"
                return redirect(f"/?month={selected_month_param}")
    else:
        form = TransactionForm()

    income_entries = Income.objects.filter(date__range=(first_day, last_day)).select_related(
        "income_category",
        "bank_account",
    )

    expense_entries = (
        Expense.objects
        .filter(date__range=(first_day, last_day))
        .select_related("category", "bank_account", "rental_unit", "cra_category")
        .annotate(attachment_count=Count("attachments", distinct=True))
    )

    total_income = sum(i.amount for i in income_entries)
    total_expenses = sum(e.amount for e in expense_entries)
    net_savings = total_income - total_expenses

    income_by_source = defaultdict(list)
    for income in income_entries:
        label = income.income_category.name if income.income_category else (income.category or "Uncategorized")
        income_by_source[label].append(income)

    expenses_by_category = defaultdict(Decimal)
    targets_by_category = {}

    for expense in expense_entries:
        category_name = expense.category.name
        expenses_by_category[category_name] += expense.amount

        if category_name not in targets_by_category:
            try:
                category_obj = Category.objects.get(name=category_name)
                targets_by_category[category_name] = category_obj.monthly_limit
            except Category.DoesNotExist:
                targets_by_category[category_name] = Decimal("0.00")

    month_expenses = Expense.objects.filter(date__year=selected_date.year, date__month=selected_date.month)
    categories_with_targets = Category.objects.exclude(monthly_limit__isnull=True)

    category_summaries = []
    for category in categories_with_targets:
        total_spent = month_expenses.filter(category=category).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        percent_used = (total_spent / category.monthly_limit * 100) if category.monthly_limit > 0 else 0
        category_summaries.append({
            "name": category.name,
            "target": category.monthly_limit,
            "spent": total_spent,
            "percent_used": round(percent_used, 1),
            "over_budget": total_spent > category.monthly_limit,
        })

    accounts = BankAccount.objects.all().order_by("name")

    context = {
        "form": form,
        "selected_month": f"{year:04d}-{month:02d}",
        "selected_month_display": selected_month_display,
        "income_entries": income_entries,
        "expense_entries": expense_entries,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "net_savings": net_savings,
        "income_by_source": dict(income_by_source),
        "expenses_by_category": dict(expenses_by_category),
        "targets_by_category": targets_by_category,
        "all_categories": Category.objects.all(),
        "category_summaries": category_summaries,
        "accounts": accounts,
        "income_categories": IncomeCategory.objects.all().order_by("name"),

        # ✅ NEW: needed for dashboard expense modal + add-transaction dropdowns
        "all_rental_units": RentalUnit.objects.select_related("property").order_by("property__name", "name"),
        "cra_categories": CRARentalExpenseCategory.objects.filter(is_active=True).order_by("sort_order", "name"),
        "income_source_default_unit_map": {
            str(c.id): (c.default_rental_unit_id or "")
            for c in IncomeCategory.objects.all()
        },

    }

    return render(request, "dashboard.html", context)

def category_progress(request):
    today = date.today()
    selected_month_str = request.GET.get("month", today.strftime("%Y-%m"))
    try:
        year, month = map(int, selected_month_str.split("-"))
    except ValueError:
        year, month = today.year, today.month

    first_day = date(year, month, 1)
    last_day = date(year, month, monthrange(year, month)[1])
    selected_month_display = first_day.strftime("%B %Y")

    categories_with_targets = Category.objects.exclude(monthly_limit__isnull=True)

    expense_summaries = []
    for category in categories_with_targets:
        total_spent = (
            Expense.objects.filter(category=category, date__range=(first_day, last_day))
            .aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
        percent_used = (total_spent / category.monthly_limit * 100) if category.monthly_limit and category.monthly_limit > 0 else 0

        if percent_used >= 130:
            bar_class = "bg-danger"
        elif percent_used >= 85:
            bar_class = "bg-warning"
        else:
            bar_class = "bg-success"

        expense_summaries.append({
            "name": category.name,
            "target": category.monthly_limit,
            "actual": total_spent,
            "percent": round(percent_used, 1),
            "bar_class": bar_class,
        })

    income_categories = IncomeCategory.objects.all()

    income_summaries = []
    for inc_cat in income_categories:
        target = inc_cat.monthly_target or Decimal("0.00")

        total_received = (
            Income.objects.filter(income_category=inc_cat, date__range=(first_day, last_day))
            .aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )

        percent_received = (total_received / target * 100) if target > 0 else 0

        if target <= 0:
            bar_class = "bg-secondary"
        elif percent_received >= 100:
            bar_class = "bg-success"
        elif percent_received >= 85:
            bar_class = "bg-warning"
        else:
            bar_class = "bg-danger"

        income_summaries.append({
            "id": inc_cat.id,
            "name": inc_cat.name,
            "target": target,
            "actual": total_received,
            "percent": round(percent_received, 1),
            "bar_class": bar_class,
        })

    income_summaries.sort(key=lambda x: x["name"].lower())
    expense_summaries.sort(key=lambda x: x["name"].lower())

    context = {
        "income_summaries": income_summaries,
        "expense_summaries": expense_summaries,
        "selected_month": f"{year:04d}-{month:02d}",
        "selected_month_display": selected_month_display,
    }
    return render(request, "category_progress.html", context)

def category_expense_list(request, category_name):
    selected_month_str = request.GET.get("month")
    range_key = request.GET.get("range", "6")
    today = date.today()

    try:
        year, month = map(int, (selected_month_str or "").split("-"))
    except Exception:
        year, month = today.year, today.month

    category = get_object_or_404(Category, name=category_name)
    anchor_month_start = date(year, month, 1)

    def add_months(d: date, delta_months: int) -> date:
        y = d.year + (d.month - 1 + delta_months) // 12
        m = (d.month - 1 + delta_months) % 12 + 1
        return date(y, m, 1)

    month_starts = []

    if range_key in ("3", "6", "12"):
        n = int(range_key)
        start = add_months(anchor_month_start, -(n - 1))
        cur = start
        while cur <= anchor_month_start:
            month_starts.append(cur)
            cur = add_months(cur, 1)

    elif range_key == "ytd":
        start = date(anchor_month_start.year, 1, 1)
        cur = start
        while cur <= anchor_month_start:
            month_starts.append(cur)
            cur = add_months(cur, 1)

    elif range_key == "prev_year":
        prev_year = anchor_month_start.year - 1
        cur = date(prev_year, 1, 1)
        end = date(prev_year, 12, 1)
        while cur <= end:
            month_starts.append(cur)
            cur = add_months(cur, 1)

    elif range_key == "all":
        earliest = (
            Expense.objects.filter(category=category)
            .order_by("date")
            .values_list("date", flat=True)
            .first()
        )
        start = date(earliest.year, earliest.month, 1) if earliest else anchor_month_start

        cur = start
        while cur <= anchor_month_start:
            month_starts.append(cur)
            cur = add_months(cur, 1)

    else:
        start = add_months(anchor_month_start, -5)
        cur = start
        while cur <= anchor_month_start:
            month_starts.append(cur)
            cur = add_months(cur, 1)

    month_starts = list(reversed(month_starts))

    monthly_limit = category.monthly_limit or Decimal("0.00")
    has_limit = category.monthly_limit is not None and category.monthly_limit > 0

    month_rows = []
    trend_labels = []
    trend_values = []

    month_starts_chrono = list(reversed(month_starts))

    for m in month_starts_chrono:
        start = date(m.year, m.month, 1)
        end = date(m.year, m.month, monthrange(m.year, m.month)[1])

        expenses_qs = (
            Expense.objects
            .filter(category=category, date__range=(start, end))
            .select_related("bank_account")
            .order_by("-date", "-id")
        )

        total = expenses_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        percent = (total / monthly_limit * 100) if has_limit else 0

        month_rows.append({
            "month": m,
            "expenses": expenses_qs,
            "total": total,
            "percent": round(percent, 1),
        })

        trend_labels.append(m.strftime("%b %Y"))
        trend_values.append(float(total))

    month_rows = list(reversed(month_rows))

    range_total = sum((r["total"] for r in month_rows), Decimal("0.00"))

    range_options = [
        ("3", "Last 3 months"),
        ("6", "Last 6 months"),
        ("12", "Last 12 months"),
        ("ytd", "Year to date"),
        ("prev_year", "Previous year"),
        ("all", "All time"),
    ]

    context = {
        "category": category,
        "selected_month": f"{year:04d}-{month:02d}",
        "selected_range": range_key,
        "range_options": range_options,
        "has_limit": has_limit,
        "monthly_limit": monthly_limit,
        "month_rows": month_rows,
        "trend_labels": trend_labels,
        "trend_values": trend_values,
        "range_total": range_total,
    }
    return render(request, "category_expense_list.html", context)

def income_category_income_list(request, pk):
    selected_month_str = request.GET.get("month")
    range_key = request.GET.get("range", "6")
    today = date.today()

    try:
        year, month = map(int, (selected_month_str or "").split("-"))
    except Exception:
        year, month = today.year, today.month

    inc_cat = get_object_or_404(IncomeCategory, pk=pk)
    anchor_month_start = date(year, month, 1)

    def add_months(d: date, delta_months: int) -> date:
        y = d.year + (d.month - 1 + delta_months) // 12
        m = (d.month - 1 + delta_months) % 12 + 1
        return date(y, m, 1)

    month_starts = []

    if range_key in ("3", "6", "12"):
        n = int(range_key)
        start = add_months(anchor_month_start, -(n - 1))
        cur = start
        while cur <= anchor_month_start:
            month_starts.append(cur)
            cur = add_months(cur, 1)

    elif range_key == "ytd":
        start = date(anchor_month_start.year, 1, 1)
        cur = start
        while cur <= anchor_month_start:
            month_starts.append(cur)
            cur = add_months(cur, 1)

    elif range_key == "prev_year":
        prev_year = anchor_month_start.year - 1
        cur = date(prev_year, 1, 1)
        end = date(prev_year, 12, 1)
        while cur <= end:
            month_starts.append(cur)
            cur = add_months(cur, 1)

    elif range_key == "all":
        earliest = (
            Income.objects.filter(income_category=inc_cat)
            .order_by("date")
            .values_list("date", flat=True)
            .first()
        )
        start = date(earliest.year, earliest.month, 1) if earliest else anchor_month_start

        cur = start
        while cur <= anchor_month_start:
            month_starts.append(cur)
            cur = add_months(cur, 1)

    else:
        start = add_months(anchor_month_start, -5)
        cur = start
        while cur <= anchor_month_start:
            month_starts.append(cur)
            cur = add_months(cur, 1)

    month_starts = list(reversed(month_starts))

    target = inc_cat.monthly_target or Decimal("0.00")

    month_rows = []
    trend_labels = []
    trend_values = []

    month_starts_chrono = list(reversed(month_starts))

    for m in month_starts_chrono:
        start = date(m.year, m.month, 1)
        end = date(m.year, m.month, monthrange(m.year, m.month)[1])

        incomes_qs = (
            Income.objects
            .filter(income_category=inc_cat, date__range=(start, end))
            .select_related("bank_account")
            .order_by("-date", "-id")
        )

        total = incomes_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        percent = (total / target * 100) if target > 0 else 0

        month_rows.append({
            "month": m,
            "incomes": incomes_qs,
            "total": total,
            "percent": round(percent, 1),
        })

        trend_labels.append(m.strftime("%b %Y"))
        trend_values.append(float(total))

    month_rows = list(reversed(month_rows))

    range_total = sum((r["total"] for r in month_rows), Decimal("0.00"))

    range_options = [
        ("3", "Last 3 months"),
        ("6", "Last 6 months"),
        ("12", "Last 12 months"),
        ("ytd", "Year to date"),
        ("prev_year", "Previous year"),
        ("all", "All time"),
    ]

    context = {
        "income_category": inc_cat,
        "selected_month": f"{year:04d}-{month:02d}",
        "selected_range": range_key,
        "range_options": range_options,
        "has_target": target > 0,
        "monthly_target": target,
        "month_rows": month_rows,
        "trend_labels": trend_labels,
        "trend_values": trend_values,
        "range_total": range_total,
    }
    return render(request, "income_category_income_list.html", context)

# -------------------------
# Rental Properties Section
# -------------------------

def rental_properties(request):
    """
    Owned properties overview:
      - income / expenses / net over a selected range
      - mortgage summary (if configured)
      - equity, if an estimated value is set
    """
    selected_month_str = request.GET.get("month")
    range_key = request.GET.get("range", "6")
    today = date.today()

    try:
        year, month = map(int, (selected_month_str or "").split("-"))
    except Exception:
        year, month = today.year, today.month
        selected_month_str = f"{year:04d}-{month:02d}"

    anchor_month_start = date(year, month, 1)

    def add_months(d: date, delta_months: int) -> date:
        y = d.year + (d.month - 1 + delta_months) // 12
        m = (d.month - 1 + delta_months) % 12 + 1
        return date(y, m, 1)

    # Determine range_start and range_end (inclusive)
    if range_key in ("3", "6", "12"):
        n = int(range_key)
        start_month = add_months(anchor_month_start, -(n - 1))
        range_start = start_month
        range_end = date(year, month, monthrange(year, month)[1])
    elif range_key == "ytd":
        range_start = date(year, 1, 1)
        range_end = date(year, month, monthrange(year, month)[1])
    elif range_key == "prev_year":
        prev_year = year - 1
        range_start = date(prev_year, 1, 1)
        range_end = date(prev_year, 12, 31)
    elif range_key == "all":
        earliest_income = (
            Income.objects.order_by("date").values_list("date", flat=True).first()
        )
        earliest_expense = (
            Expense.objects.order_by("date").values_list("date", flat=True).first()
        )
        earliest = earliest_income or earliest_expense
        if earliest_income and earliest_expense:
            earliest = min(earliest_income, earliest_expense)
        if earliest:
            range_start = date(earliest.year, earliest.month, 1)
        else:
            range_start = anchor_month_start
        range_end = date(year, month, monthrange(year, month)[1])
    else:
        # default last 6 months
        start_month = add_months(anchor_month_start, -5)
        range_start = start_month
        range_end = date(year, month, monthrange(year, month)[1])

    properties = RentalProperty.objects.filter(is_active=True).order_by("name")

    rows = []
    for prop in properties:
        income_total = (
            Income.objects.filter(
                rental_unit__property=prop,
                date__range=(range_start, range_end),
            ).aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
        expense_total = (
            Expense.objects.filter(
                rental_unit__property=prop,
                date__range=(range_start, range_end),
            ).aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
        net_total = income_total - expense_total

        active_mortgage = prop.mortgages.filter(is_active=True).order_by("id").first()
        if active_mortgage:
            mortgage_info = {
                "name": active_mortgage.name,
                "lender_name": active_mortgage.lender_name,
                "current_balance": active_mortgage.current_principal_balance,
                "rate": active_mortgage.interest_rate_percent,
                "term_end": active_mortgage.term_end_date,
            }
        else:
            mortgage_info = None

        rows.append({
            "property": prop,
            "mortgage": mortgage_info,
            "estimated_value": prop.estimated_value,
            "equity": prop.equity,
            "ltv_pct": prop.ltv_pct,
            "income_total": income_total,
            "expense_total": expense_total,
            "net_total": net_total,
        })

    range_options = [
        ("3", "Last 3 months"),
        ("6", "Last 6 months"),
        ("12", "Last 12 months"),
        ("ytd", "Year to date"),
        ("prev_year", "Previous year"),
        ("all", "All time"),
    ]

    context = {
        "rows": rows,
        "selected_month": f"{year:04d}-{month:02d}",
        "selected_range": range_key,
        "range_options": range_options,
        "range_start": range_start,
        "range_end": range_end,
    }
    return render(request, "rental_properties.html", context)



def rental_property_detail(request, property_id):
    # Allow POST to carry the same month/range as GET
    selected_month_str = request.POST.get("month") or request.GET.get("month")
    range_key = request.POST.get("range") or request.GET.get("range", "6")
    today = date.today()

    try:
        year, month = map(int, (selected_month_str or "").split("-"))
    except Exception:
        year, month = today.year, today.month
        selected_month_str = f"{year:04d}-{month:02d}"

    prop = get_object_or_404(RentalProperty.objects.prefetch_related("mortgages"), pk=property_id)

    # --- Property value update (sale price / estimated value) ---
    if request.method == "POST" and request.POST.get("action") == "update_property_value":
        value_str = (request.POST.get("estimated_value") or "").strip()
        valued_date_str = (request.POST.get("last_valued_date") or "").strip()

        try:
            prop.estimated_value = Decimal(value_str) if value_str else None
        except (InvalidOperation, ValueError):
            messages.error(request, "Could not parse estimated value. Please enter a valid number.")
        else:
            if valued_date_str:
                try:
                    prop.last_valued_date = date.fromisoformat(valued_date_str)
                except ValueError:
                    messages.error(request, "Could not parse valuation date. Please use YYYY-MM-DD.")
            else:
                prop.last_valued_date = None

            prop.save()
            messages.success(request, "Property value updated.")

        # Redirect (PRG) so a refresh doesn't resubmit the form
        return redirect(f"{request.path}?month={selected_month_str}&range={range_key}")

    # -----------------------
    # Anchor month + range → list of months
    # -----------------------
    anchor_month_start = date(year, month, 1)

    def add_months(d: date, delta_months: int) -> date:
        y = d.year + (d.month - 1 + delta_months) // 12
        m = (d.month - 1 + delta_months) % 12 + 1
        return date(y, m, 1)

    month_starts = []

    if range_key in ("3", "6", "12"):
        n = int(range_key)
        start = add_months(anchor_month_start, -(n - 1))
        cur = start
        while cur <= anchor_month_start:
            month_starts.append(cur)
            cur = add_months(cur, 1)
    elif range_key == "ytd":
        start = date(anchor_month_start.year, 1, 1)
        cur = start
        while cur <= anchor_month_start:
            month_starts.append(cur)
            cur = add_months(cur, 1)
    elif range_key == "prev_year":
        prev_year = anchor_month_start.year - 1
        cur = date(prev_year, 1, 1)
        end = date(prev_year, 12, 1)
        while cur <= end:
            month_starts.append(cur)
            cur = add_months(cur, 1)
    elif range_key == "all":
        earliest_income = (
            Income.objects.filter(rental_unit__property=prop)
            .order_by("date")
            .values_list("date", flat=True)
            .first()
        )
        earliest_expense = (
            Expense.objects.filter(rental_unit__property=prop)
            .order_by("date")
            .values_list("date", flat=True)
            .first()
        )
        earliest = earliest_income or earliest_expense
        if earliest_income and earliest_expense:
            earliest = min(earliest_income, earliest_expense)
        start = date(earliest.year, earliest.month, 1) if earliest else anchor_month_start
        cur = start
        while cur <= anchor_month_start:
            month_starts.append(cur)
            cur = add_months(cur, 1)
    else:
        # default last 6 months
        start = add_months(anchor_month_start, -5)
        cur = start
        while cur <= anchor_month_start:
            month_starts.append(cur)
            cur = add_months(cur, 1)

    month_starts_chrono = sorted(month_starts)

    # -----------------------
    # Monthly cashflow + trend
    # -----------------------
    month_rows = []
    trend_labels = []
    trend_income = []
    trend_expenses = []
    trend_net = []

    for m_start in month_starts_chrono:
        start = date(m_start.year, m_start.month, 1)
        end = date(m_start.year, m_start.month, monthrange(m_start.year, m_start.month)[1])

        incomes_qs = (
            Income.objects.filter(rental_unit__property=prop, date__range=(start, end))
            .select_related("bank_account", "rental_unit", "income_category")
            .order_by("-date", "-id")
        )
        expenses_qs = (
            Expense.objects.filter(rental_unit__property=prop, date__range=(start, end))
            .select_related("bank_account", "rental_unit", "category", "cra_category")
            .order_by("-date", "-id")
        )

        income_total = incomes_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        expense_total = expenses_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        net_total = income_total - expense_total

        month_rows.append({
            "month": m_start,
            "incomes": incomes_qs,
            "expenses": expenses_qs,
            "income_total": income_total,
            "expense_total": expense_total,
            "net_total": net_total,
        })

        trend_labels.append(m_start.strftime("%b %Y"))
        trend_income.append(float(income_total))
        trend_expenses.append(float(expense_total))
        trend_net.append(float(net_total))

    month_rows = list(reversed(month_rows))

    range_income_total = sum((r["income_total"] for r in month_rows), Decimal("0.00"))
    range_expense_total = sum((r["expense_total"] for r in month_rows), Decimal("0.00"))
    range_net_total = range_income_total - range_expense_total

    range_options = [
        ("3", "Last 3 months"),
        ("6", "Last 6 months"),
        ("12", "Last 12 months"),
        ("ytd", "Year to date"),
        ("prev_year", "Previous year"),
        ("all", "All time"),
    ]

    # -----------------------
    # Mortgage panels: YTD ledger + projections + history
    # -----------------------
    anchor_year = year
    anchor_month_end = date(year, month, monthrange(year, month)[1])

    mortgage_panels = []

    for mortgage in prop.mortgages.filter(is_active=True).order_by("name"):
        principal_categories = mortgage.principal_categories()
        interest_category_id = getattr(mortgage, "interest_category_id", None)

        principal_ytd = Decimal("0.00")
        interest_ytd = Decimal("0.00")
        ledger_rows = []
        projected_rows = []
        chart_labels = []
        chart_balances = []

        history_years = []
        history_principal = []
        history_interest = []
        history_total_principal = Decimal("0.00")
        history_pct_of_original = None

        if principal_categories:
            # --- YTD ---
            ytd_start = date(anchor_year, 1, 1)
            ytd_end = anchor_month_end

            principal_qs_ytd = Expense.objects.filter(
                category__in=principal_categories,
                date__gte=ytd_start,
                date__lte=ytd_end,
            )
            principal_ytd = principal_qs_ytd.aggregate(
                total=Coalesce(Sum("amount"), Decimal("0.00"))
            )["total"]

            if interest_category_id:
                interest_qs_ytd = Expense.objects.filter(
                    category_id=interest_category_id,
                    date__gte=ytd_start,
                    date__lte=ytd_end,
                )
                interest_ytd = interest_qs_ytd.aggregate(
                    total=Coalesce(Sum("amount"), Decimal("0.00"))
                )["total"]
            else:
                interest_qs_ytd = Expense.objects.none()
                interest_ytd = Decimal("0.00")

            # --- Ledger (current year) ---
            principal_by_date = defaultdict(lambda: Decimal("0.00"))
            prepayment_flag_by_date = defaultdict(lambda: False)
            for exp in principal_qs_ytd.select_related("category").order_by("date", "id"):
                principal_by_date[exp.date] += exp.amount
                if mortgage.prepayment_category_id and exp.category_id == mortgage.prepayment_category_id:
                    prepayment_flag_by_date[exp.date] = True

            interest_by_date = defaultdict(lambda: Decimal("0.00"))
            for exp in interest_qs_ytd.order_by("date", "id"):
                interest_by_date[exp.date] += exp.amount

            all_dates = sorted(set(principal_by_date.keys()) | set(interest_by_date.keys()))

            balance_after_by_date = {}
            base_balance = None
            base_date = None

            if (
                mortgage.tracking_start_principal is not None
                and mortgage.tracking_start_date is not None
                and mortgage.tracking_start_date in all_dates
            ):
                base_date = mortgage.tracking_start_date
                base_balance = (
                    mortgage.tracking_start_principal
                    + (mortgage.manual_adjustment or Decimal("0.00"))
                )
            elif mortgage.tracking_start_principal is not None and mortgage.tracking_start_date is not None and all_dates:
                base_date = all_dates[0]
                base_balance = (
                    mortgage.tracking_start_principal
                    + (mortgage.manual_adjustment or Decimal("0.00"))
                )

            if base_balance is not None and all_dates:
                if base_date in all_dates:
                    idx = all_dates.index(base_date)
                    balance_after_by_date[base_date] = base_balance

                    # Forward (later dates): subtract that day's principal
                    for i in range(idx + 1, len(all_dates)):
                        d = all_dates[i]
                        prev_d = all_dates[i - 1]
                        prev_bal = balance_after_by_date[prev_d]
                        balance_after_by_date[d] = prev_bal - principal_by_date[d]

                    # Backward (earlier dates): add next day's principal
                    for i in range(idx - 1, -1, -1):
                        d = all_dates[i]
                        next_d = all_dates[i + 1]
                        next_bal = balance_after_by_date[next_d]
                        balance_after_by_date[d] = next_bal + principal_by_date[next_d]
                else:
                    # Simple forward pass (no good anchor)
                    bal = base_balance
                    for d in all_dates:
                        bal = bal - principal_by_date[d]
                        balance_after_by_date[d] = bal

            for d in all_dates:
                principal_amt = principal_by_date[d]
                interest_amt = interest_by_date[d]
                payment_total = principal_amt + interest_amt

                if prepayment_flag_by_date[d] and interest_amt == 0:
                    payment_type = "Prepayment"
                elif principal_amt > 0 and interest_amt > 0:
                    payment_type = "Regular payment"
                elif principal_amt > 0:
                    payment_type = "Principal-only"
                elif interest_amt > 0:
                    payment_type = "Interest-only"
                else:
                    payment_type = "Other"

                balance_after = balance_after_by_date.get(d)

                ledger_rows.append({
                    "date": d,
                    "payment_type": payment_type,
                    "principal": principal_amt,
                    "interest": interest_amt,
                    "payment_total": payment_total,
                    "balance_after": balance_after,
                })

            # --- Projections ---
            last_balance = None
            last_payment_date = None
            for row in ledger_rows:
                if row["balance_after"] is not None:
                    last_balance = row["balance_after"]
                    last_payment_date = row["date"]

            if last_balance is not None and last_payment_date is not None:
                projected_rows = mortgage.projected_rows_to_year_end(
                    starting_balance=last_balance,
                    last_payment_date=last_payment_date,
                    year=anchor_year,
                )

            # --- Chart data (current year + projections) ---
            for row in ledger_rows:
                if row["balance_after"] is not None:
                    chart_labels.append(row["date"].isoformat())
                    chart_balances.append(float(row["balance_after"]))
            for row in projected_rows:
                chart_labels.append(row["date"].isoformat())
                chart_balances.append(float(row["balance_after"]))

            # --- History: principal & interest by year (all time) ---
            principal_all = Expense.objects.filter(category__in=principal_categories)
            principal_by_year = defaultdict(lambda: Decimal("0.00"))
            for exp in principal_all.only("date", "amount"):
                principal_by_year[exp.date.year] += exp.amount

            interest_by_year = defaultdict(lambda: Decimal("0.00"))
            if interest_category_id:
                interest_all = Expense.objects.filter(category_id=interest_category_id)
                for exp in interest_all.only("date", "amount"):
                    interest_by_year[exp.date.year] += exp.amount

            all_years = sorted(set(principal_by_year.keys()) | set(interest_by_year.keys()))
            running = Decimal("0.00")
            for y in all_years:
                p = principal_by_year[y]
                i = interest_by_year[y]
                running += p
                history_years.append(y)
                history_principal.append(float(p))
                history_interest.append(float(i))
                history_total_principal = running

            if mortgage.original_principal and mortgage.original_principal > 0 and history_total_principal > 0:
                history_pct_of_original = (history_total_principal / mortgage.original_principal) * Decimal("100.0")

        mortgage_panels.append({
            "mortgage": mortgage,
            "principal_ytd": principal_ytd,
            "interest_ytd": interest_ytd,
            "ledger_rows": ledger_rows,
            "projected_rows": projected_rows,
            "chart_labels": chart_labels,
            "chart_balances": chart_balances,
            "history_years": history_years,
            "history_principal": history_principal,
            "history_interest": history_interest,
            "history_total_principal": history_total_principal,
            "history_pct_of_original": history_pct_of_original,
        })

    context = {
        "property": prop,
        "selected_month": f"{year:04d}-{month:02d}",
        "selected_range": range_key,
        "range_options": range_options,
        "month_rows": month_rows,
        "trend_labels": trend_labels,
        "trend_income": trend_income,
        "trend_expenses": trend_expenses,
        "trend_net": trend_net,
        "range_income_total": range_income_total,
        "range_expense_total": range_expense_total,
        "range_net_total": range_net_total,
        "mortgage_panels": mortgage_panels,
    }
    return render(request, "rental_property_detail.html", context)







def rental_tax_summary(request, property_id):
    """
    Tax Summary (per property, per year) in a CRA-ish layout:
      - Income: gross rents (all units), unit count (excludes Shared/Common)
      - Expenses: list ALL CRA categories (even if $0)
      - For each CRA category:
          Total expenses (raw)
          Personal portion (raw - rental portion)
          Rental portion (after rental_business_use_pct; default 100%)
      - Drilldown link per CRA category
    """
    prop = get_object_or_404(RentalProperty, pk=property_id)

    # ---- Year dropdown options (earliest -> current) ----
    current_year = date.today().year

    earliest_expense = (
        Expense.objects
        .filter(rental_unit__property=prop)
        .order_by("date")
        .values_list("date", flat=True)
        .first()
    )
    earliest_income = (
        Income.objects
        .filter(rental_unit__property=prop)
        .order_by("date")
        .values_list("date", flat=True)
        .first()
    )

    earliest = earliest_expense or earliest_income
    if earliest_expense and earliest_income:
        earliest = min(earliest_expense, earliest_income)

    start_year = earliest.year if earliest else current_year
    year_options = list(range(start_year, current_year + 1))

    try:
        year = int(request.GET.get("year") or current_year)
    except ValueError:
        year = current_year

    if year not in year_options:
        year_options.append(year)
        year_options = sorted(set(year_options))

    # ---- Unit count (exclude Shared/Common units) ----
    units_qs = RentalUnit.objects.filter(property=prop, is_active=True)
    # simple "smart enough" heuristic
    rentable_units = units_qs.exclude(name__icontains="shared").exclude(name__icontains="common")
    unit_count = rentable_units.count()

    # ---- Income: gross rents for all units (we ignore short-term rentals) ----
    # We assume rental incomes are already tagged with rental_unit.
    income_total = (
        Income.objects
        .filter(rental_unit__property=prop, date__year=year)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )

    # ---- Expenses: only those with CRA category set (for summary table) ----
    expense_qs = (
        Expense.objects
        .filter(
            date__year=year,
            rental_unit__property=prop,
            cra_category__isnull=False,
        )
        .select_related("cra_category", "rental_unit")
    )

    # Rental %: default 100 if null
    pct = Case(
        When(rental_business_use_pct__isnull=False, then=F("rental_business_use_pct")),
        default=Value(100),
        output_field=DecimalField(max_digits=5, decimal_places=2),
    )

    rental_portion_expr = ExpressionWrapper(
        F("amount") * pct / Value(100),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    personal_portion_expr = ExpressionWrapper(
        F("amount") - (F("amount") * pct / Value(100)),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    # Aggregate by CRA category id so we can join to the full category list (including zeros)
    aggregates = (
        expense_qs.values("cra_category_id")
        .annotate(
            total_raw=Sum("amount"),
            total_rental=Sum(rental_portion_expr),
            total_personal=Sum(personal_portion_expr),
        )
    )

    agg_map = {
        row["cra_category_id"]: row
        for row in aggregates
    }

    # ---- Build rows for ALL CRA categories (even if zero) ----
    cra_categories = list(
        CRARentalExpenseCategory.objects
        .filter(is_active=True)
        .order_by("sort_order", "name")
    )

    rows = []
    total_expenses_raw = Decimal("0.00")
    total_expenses_rental = Decimal("0.00")
    total_expenses_personal = Decimal("0.00")

    for cat in cra_categories:
        data = agg_map.get(cat.id) or {}
        raw = data.get("total_raw") or Decimal("0.00")
        rental = data.get("total_rental") or Decimal("0.00")
        personal = data.get("total_personal") or Decimal("0.00")

        total_expenses_raw += raw
        total_expenses_rental += rental
        total_expenses_personal += personal

        rows.append({
            "cat": cat,
            "total_raw": raw,
            "total_rental": rental,
            "total_personal": personal,
        })

    # How many expenses are missing CRA category (for cleanup)
    missing_cra_count = (
        Expense.objects
        .filter(date__year=year, rental_unit__property=prop, cra_category__isnull=True)
        .count()
    )
    missing_cra_expenses = (
        Expense.objects
        .filter(
            rental_unit__property=prop,
            date__year=year,
            cra_category__isnull=True,
        )
        .select_related("category", "rental_unit", "bank_account")
        .order_by("-date", "-id")
    )
    missing_cra_count = missing_cra_expenses.count()

    context = {
        "property": prop,
        "year": year,
        "year_options": year_options,
        "unit_count": unit_count,
        "income_total": income_total,
        "rows": rows,
        "total_expenses_raw": total_expenses_raw,
        "total_expenses_rental": total_expenses_rental,
        "total_expenses_personal": total_expenses_personal,
        "missing_cra_count": missing_cra_count,
        "missing_cra_expenses": missing_cra_expenses,
    }
    return render(request, "rental_tax_summary.html", context)

def rental_tax_category_detail(request, property_id, cra_category_id):
    prop = get_object_or_404(RentalProperty, pk=property_id)
    cra_cat = get_object_or_404(CRARentalExpenseCategory, pk=cra_category_id)

    try:
        year = int(request.GET.get("year") or date.today().year)
    except ValueError:
        year = date.today().year

    qs = (
        Expense.objects
        .filter(
            rental_unit__property=prop,
            date__year=year,
            cra_category=cra_cat,
        )
        .select_related("rental_unit", "category", "bank_account")
        .order_by("-date", "-id")
    )

    pct = Case(
        When(rental_business_use_pct__isnull=False, then=F("rental_business_use_pct")),
        default=Value(100),
        output_field=DecimalField(max_digits=5, decimal_places=2),
    )

    rental_portion_expr = ExpressionWrapper(
        F("amount") * pct / Value(100),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    personal_portion_expr = ExpressionWrapper(
        F("amount") - (F("amount") * pct / Value(100)),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    qs = qs.annotate(
        rental_portion=rental_portion_expr,
        personal_portion=personal_portion_expr,
    )

    context = {
        "property": prop,
        "cra_category": cra_cat,
        "year": year,
        "expenses": qs,
    }
    return render(request, "rental_tax_category_detail.html", context)


# -------------------------
# Categories + Accounts (unchanged)
# -------------------------

class CategoryForm(ModelForm):
    class Meta:
        model = Category
        fields = ["name", "monthly_limit", "savings_target_per_paycheque"]

class CategoryForm(ModelForm):
    class Meta:
        model = Category
        fields = ["name", "monthly_limit", "savings_target_per_paycheque"]

class IncomeCategoryForm(ModelForm):
    class Meta:
        model = IncomeCategory
        fields = ["name", "monthly_target", "taxable_default"]

def category_list(request):
    expense_categories = Category.objects.all().order_by("name")
    income_categories = IncomeCategory.objects.all().order_by("name")

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "expense_delete":
            cat = get_object_or_404(Category, pk=request.POST.get("id"))
            cat.delete()
            return redirect("category_list")

        if action == "expense_save":
            category_id = request.POST.get("id") or ""
            instance = get_object_or_404(Category, pk=category_id) if category_id else None
            form = CategoryForm(request.POST, instance=instance)

            if form.is_valid():
                try:
                    form.save()
                    return redirect("category_list")
                except IntegrityError:
                    messages.error(request, "An expense category with that name already exists.")
            else:
                messages.error(request, "Please correct the errors in the expense category form.")

        if action == "income_delete":
            inc = get_object_or_404(IncomeCategory, pk=request.POST.get("id"))
            inc.delete()
            return redirect("category_list")

        if action == "income_save":
            income_id = request.POST.get("id") or ""
            instance = get_object_or_404(IncomeCategory, pk=income_id) if income_id else None
            form = IncomeCategoryForm(request.POST, instance=instance)

            if form.is_valid():
                try:
                    form.save()
                    return redirect("category_list")
                except IntegrityError:
                    messages.error(request, "An income category with that name already exists.")
            else:
                messages.error(request, "Please correct the errors in the income category form.")

    expense_form = CategoryForm()
    income_form = IncomeCategoryForm()

    return render(request, "category_list.html", {
        "expense_categories": expense_categories,
        "income_categories": income_categories,
        "expense_form": expense_form,
        "income_form": income_form,
    })

class BankAccountForm(ModelForm):
    class Meta:
        model = BankAccount
        fields = [
            "name",
            "institution",
            "account_type",
            "account_number_last4",
            "is_withholding_account",
            "current_balance",
            "is_active",
        ]

def bank_accounts(request):
    accounts = BankAccount.objects.all().order_by("name")

    if request.method == "POST":
        if "delete_account" in request.POST:
            account = get_object_or_404(BankAccount, pk=request.POST.get("account_id"))
            account.delete()
            return redirect("bank_accounts")

        account_id = request.POST.get("account_id")
        if account_id:
            account = get_object_or_404(BankAccount, pk=account_id)
            form = BankAccountForm(request.POST, instance=account)
        else:
            form = BankAccountForm(request.POST)

        if form.is_valid():
            account = form.save(commit=False)
            account.last_updated = date.today()
            account.save()
            return redirect("bank_accounts")
    else:
        form = BankAccountForm()

    return render(request, "bank_accounts.html", {"accounts": accounts, "form": form})

def import_batch_detail(request, batch_id):
    batch = get_object_or_404(ImportBatch, pk=batch_id)
    expenses = Expense.objects.filter(import_batch=batch).order_by("-date")
    incomes = Income.objects.filter(import_batch=batch).order_by("-date")

    return render(request, "import_batch_detail.html", {
        "batch": batch,
        "expenses": expenses,
        "incomes": incomes,
    })

@require_http_methods(["GET", "POST"])
def import_transactions(request):
    if request.method == "GET":
        upload_form = CSVUploadForm()
        recent_batches = ImportBatch.objects.select_related("bank_account").all()[:10]


        return render(request, "import_transactions.html", {
            "step": "upload",
            "upload_form": upload_form,
            "recent_batches": recent_batches,
        })

    step = request.POST.get("step", "upload")

    if step == "upload" and request.FILES.get("csv_file"):
        upload_form = CSVUploadForm(request.POST, request.FILES)
        recent_batches = ImportBatch.objects.select_related("bank_account").all()[:10]

        if not upload_form.is_valid():
            return render(request, "import_transactions.html", {
                "step": "upload",
                "upload_form": upload_form,
                "recent_batches": recent_batches,
            })

        csv_file = upload_form.cleaned_data["csv_file"]
        bank_account = upload_form.cleaned_data["bank_account"]
        uploaded_filename = csv_file.name

        try:
            decoded = io.TextIOWrapper(csv_file.file, encoding="utf-8")
        except Exception:
            decoded = io.TextIOWrapper(csv_file.file, encoding="latin-1")

        reader = csv.reader(decoded)

        initial_rows = []
        category_cache = {}
        missing_categories = set()
        hydro_candidates = []
        earliest_date_in_file = None
        latest_date_in_file = None

        income_cat_cache = {c.name: c for c in IncomeCategory.objects.all()}
        arnprior_shared_unit_id = get_arnprior_shared_unit_id()

        for row in reader:
            if not row or all(not cell.strip() for cell in row):
                continue

            raw_date = row[0].strip() if len(row) > 0 else ""
            raw_desc = row[1].strip() if len(row) > 1 else ""
            raw_withdrawal = row[2].strip() if len(row) > 2 else ""
            raw_deposit = row[3].strip() if len(row) > 3 else ""

            desc_upper = (raw_desc or "").upper()

            if "TFR-TO C/C" in desc_upper:
                continue

            if not raw_desc and not raw_withdrawal and not raw_deposit:
                continue

            parsed_date = None
            for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
                try:
                    parsed_date = datetime.strptime(raw_date, fmt).date()
                    break
                except Exception:
                    continue

            if parsed_date is None:
                continue

            if earliest_date_in_file is None or parsed_date < earliest_date_in_file:
                earliest_date_in_file = parsed_date
            if latest_date_in_file is None or parsed_date > latest_date_in_file:
                latest_date_in_file = parsed_date

            amount_str = None
            entry_type_default = "expense"

            if raw_withdrawal and not raw_deposit:
                amount_str = raw_withdrawal
                entry_type_default = "expense"
            elif raw_deposit and not raw_withdrawal:
                amount_str = raw_deposit
                entry_type_default = "income"
            elif raw_withdrawal and raw_deposit:
                amount_str = raw_withdrawal
                entry_type_default = "expense"
            else:
                continue

            amount_str = amount_str.replace(",", "")
            try:
                amount = Decimal(amount_str)
            except Exception:
                continue

            amount = abs(amount)

            entry_type, income_source_name = apply_income_rules(desc_upper, amount, entry_type_default, parsed_date=parsed_date)


            income_source_obj = None
            if entry_type == "income" and income_source_name:
                income_source_obj = income_cat_cache.get(income_source_name)
                if income_source_obj is None:
                    income_source_obj, _ = IncomeCategory.objects.get_or_create(name=income_source_name)
                    income_cat_cache[income_source_name] = income_source_obj

            expense_category = None
            if entry_type == "expense":
                expense_category = apply_expense_rules(desc_upper, amount, category_cache, missing_categories)

            if "HYDRO ONE" in desc_upper and entry_type == "expense":
                hydro_candidates.append((len(initial_rows), amount))

            expense_rental_unit_id = None
            if entry_type == "expense" and expense_category and arnprior_shared_unit_id:
                # Heuristic: any auto-mapped expense category containing "Arnprior" => Arnprior shared/common unit
                if "ARNPRIOR" in (expense_category.name or "").upper():
                    expense_rental_unit_id = arnprior_shared_unit_id

            initial_rows.append({
                "entry_type": entry_type,
                "date": parsed_date,
                "vendor_name": raw_desc or "Unknown Vendor",
                "amount": amount,
                "location": "Ottawa",
                "notes": "",
                "expense_category": expense_category,
                "income_source": income_source_obj,
                "income_rental_unit": (
                    income_source_obj.default_rental_unit_id if income_source_obj and income_source_obj.default_rental_unit_id else None),
                "apply_to_withholding": False,
                "is_withholding_payout": False,
                "withholding_category": None,
                "expense_rental_unit": expense_rental_unit_id,
            })

        if bank_account and earliest_date_in_file and latest_date_in_file:
            overlapping = ImportBatch.objects.filter(
                bank_account=bank_account,
                earliest_date__lte=latest_date_in_file,
                latest_date__gte=earliest_date_in_file,
            )
            if overlapping.exists():
                ranges = "; ".join(f"{b.earliest_date} to {b.latest_date}" for b in overlapping)
                messages.warning(
                    request,
                    f"This CSV covers {earliest_date_in_file} to {latest_date_in_file}, "
                    f"which overlaps with existing imports for this account: {ranges}. "
                    f"Duplicates will be skipped where detected."
                )

        if hydro_candidates:
            if len(hydro_candidates) == 1:
                idx, amt = hydro_candidates[0]
                cat_name = "Foxview Hydro" if amt > Decimal("200.00") else "Arnprior Hydro"
                cat = get_category_cached(cat_name, category_cache, missing_categories)
                if cat:
                    initial_rows[idx]["expense_category"] = cat
            else:
                sorted_by_amount = sorted(hydro_candidates, key=lambda x: x[1])
                for idx, amt in sorted_by_amount:
                    if len(sorted_by_amount) == 2:
                        cat_name = "Arnprior Hydro" if (idx, amt) == sorted_by_amount[0] else "Foxview Hydro"
                    else:
                        cat_name = "Foxview Hydro" if amt > Decimal("200.00") else "Arnprior Hydro"
                    cat = get_category_cached(cat_name, category_cache, missing_categories)
                    if cat:
                        initial_rows[idx]["expense_category"] = cat

        if not initial_rows:
            messages.warning(request, "No valid transactions were found in the CSV (check the file format).")
            upload_form = CSVUploadForm()
            return render(request, "import_transactions.html", {
                "step": "upload",
                "upload_form": upload_form,
                "recent_batches": ImportBatch.objects.select_related("bank_account").all()[:10],
            })

        if missing_categories:
            missing_list = ", ".join(sorted(missing_categories))
            messages.warning(
                request,
                f"The following auto-mapped categories were not found in your database and were skipped: {missing_list}."
            )

        formset = TransactionImportFormSet(initial=initial_rows)
        income_rental_unit_map = build_income_rental_unit_map()

        return render(request, "import_transactions.html", {
            "step": "review",
            "formset": formset,
            "selected_bank_account": bank_account,
            "uploaded_filename": uploaded_filename,
            "income_rental_unit_map": income_rental_unit_map,
        })

    if step == "review":
        formset = TransactionImportFormSet(request.POST)

        bank_account = None
        bank_account_id = request.POST.get("bank_account_id")
        if bank_account_id:
            try:
                bank_account = BankAccount.objects.get(pk=bank_account_id)
            except BankAccount.DoesNotExist:
                bank_account = None

        uploaded_filename = request.POST.get("uploaded_filename", "")

        if not formset.is_valid():
            messages.error(request, "There were errors in the form. Please correct them.")
            income_rental_unit_map = build_income_rental_unit_map()


            return render(request, "import_transactions.html", {
                "step": "review",
                "formset": formset,
                "selected_bank_account": bank_account,
                "uploaded_filename": uploaded_filename,
                "income_rental_unit_map": income_rental_unit_map,  # ✅ add this
            })

        created_expenses = 0
        created_incomes = 0
        skipped_duplicates = 0
        created_withholding_transactions = 0

        earliest_date = None
        latest_date = None
        total_expense_amount = Decimal("0.00")
        total_income_amount = Decimal("0.00")

        expense_objs = []
        income_objs = []
        withholding_txns = []

        seen_expense_keys = set()
        seen_income_keys = set()

        for form in formset:
            cd = form.cleaned_data
            if not cd:
                continue
            if cd.get("skip"):
                continue

            entry_type = cd.get("entry_type")
            date_val = cd.get("date")
            vendor_name = cd.get("vendor_name")
            amount = cd.get("amount")
            location = cd.get("location") or "Ottawa"
            notes = cd.get("notes")
            expense_category = cd.get("expense_category")
            income_source = cd.get("income_source")
            income_rental_unit = cd.get("income_rental_unit")
            apply_to_withholding = cd.get("apply_to_withholding")
            is_withholding_payout = cd.get("is_withholding_payout")
            withholding_category = cd.get("withholding_category")

            if not (date_val and amount):
                continue

            if earliest_date is None or date_val < earliest_date:
                earliest_date = date_val
            if latest_date is None or date_val > latest_date:
                latest_date = date_val

            if entry_type == "expense":
                if is_withholding_payout and withholding_category:
                    payout_amount = -amount if amount > 0 else amount
                    wt = WithholdingTransaction(
                        category=withholding_category,
                        date=date_val,
                        amount=payout_amount,
                        note=notes or vendor_name or "",
                    )
                    withholding_txns.append(wt)
                    created_withholding_transactions += 1
                    continue

                if not (vendor_name and expense_category):
                    continue

                exp_key = (date_val, vendor_name, amount, expense_category.id)

                if exp_key in seen_expense_keys or Expense.objects.filter(
                    date=date_val,
                    vendor_name=vendor_name,
                    amount=amount,
                    category=expense_category,
                ).exists():
                    skipped_duplicates += 1
                    continue

                seen_expense_keys.add(exp_key)

                exp = Expense(
                    date=date_val,
                    vendor_name=vendor_name,
                    amount=amount,
                    category=expense_category,
                    location=location,
                    notes=notes or "",
                    bank_account=bank_account,
                )
                expense_objs.append(exp)
                total_expense_amount += amount
                created_expenses += 1

                if apply_to_withholding and withholding_category:
                    wt = WithholdingTransaction(
                        category=withholding_category,
                        date=date_val,
                        amount=amount,
                        note=notes or vendor_name or "",
                    )
                    withholding_txns.append(wt)
                    created_withholding_transactions += 1

            elif entry_type == "income":
                if not income_source:
                    continue

                inc_key = (date_val, amount, income_source.id)

                if inc_key in seen_income_keys or Income.objects.filter(
                    date=date_val,
                    amount=amount,
                    income_category=income_source,
                ).exists():
                    skipped_duplicates += 1
                    continue

                seen_income_keys.add(inc_key)

                inc = Income(
                    date=date_val,
                    amount=amount,
                    income_category=income_source,
                    category=income_source.name,
                    taxable=income_source.taxable_default,
                    notes=notes or "",
                    bank_account=bank_account,
                )

                # If user selected an override, use it; otherwise fall back to the IncomeCategory default
                if income_rental_unit:
                    inc.rental_unit = income_rental_unit
                elif income_source and income_source.default_rental_unit_id:
                    inc.rental_unit = income_source.default_rental_unit

                income_objs.append(inc)
                total_income_amount += amount
                created_incomes += 1

        total_transactions = created_expenses + created_incomes

        if total_transactions > 0 and earliest_date and latest_date:
            batch = ImportBatch.objects.create(
                bank_account=bank_account,
                earliest_date=earliest_date,
                latest_date=latest_date,
                total_transactions=total_transactions,
                total_income_amount=total_income_amount,
                total_expense_amount=total_expense_amount,
                filename=uploaded_filename or "",
            )

            for exp in expense_objs:
                exp.import_batch = batch
                exp.save()
            for inc in income_objs:
                inc.import_batch = batch
                inc.save()
        else:
            for exp in expense_objs:
                exp.save()
            for inc in income_objs:
                inc.save()

        for wt in withholding_txns:
            wt.save()

        msg = f"Imported {created_expenses} expenses and {created_incomes} income transactions."
        if created_withholding_transactions:
            msg += f" Applied {created_withholding_transactions} withholding bucket adjustment(s)."
        if skipped_duplicates:
            msg += f" Skipped {skipped_duplicates} duplicate transaction(s)."
        messages.success(request, msg)

        return redirect("dashboard")

    upload_form = CSVUploadForm()
    recent_batches = ImportBatch.objects.select_related("bank_account").all()[:10]
    return render(request, "import_transactions.html", {
        "step": "upload",
        "upload_form": upload_form,
        "recent_batches": recent_batches,
    })

@csrf_exempt
def update_expense(request):
    """
    Legacy endpoint still referenced in urls.py.
    Keep it working (and updated) even if the dashboard modal is the main flow.
    """
    if request.method != "POST":
        return HttpResponseBadRequest("Invalid method")

    expense_id = request.POST.get("id")
    try:
        expense = Expense.objects.get(id=expense_id)
    except Expense.DoesNotExist:
        return HttpResponseBadRequest("Expense not found")

    if "delete" in request.POST:
        expense.delete()
    else:
        expense.date = request.POST.get("date")
        expense.vendor_name = request.POST.get("vendor_name")
        expense.category = get_object_or_404(Category, id=request.POST.get("category_id"))
        expense.location = request.POST.get("location", "Ottawa")
        expense.amount = request.POST.get("amount")
        expense.notes = request.POST.get("notes", "")

        # ✅ Optional rental fields if caller sends them
        rental_unit_id = (request.POST.get("rental_unit") or "").strip()
        expense.rental_unit = get_object_or_404(RentalUnit, pk=rental_unit_id) if rental_unit_id else None

        cra_category_id = (request.POST.get("cra_category") or "").strip()
        expense.cra_category = get_object_or_404(CRARentalExpenseCategory, pk=cra_category_id) if cra_category_id else None

        pct_str = (request.POST.get("rental_business_use_pct") or "").strip()
        expense.rental_business_use_pct = Decimal(pct_str) if pct_str else None

        expense.save()

    selected_month = request.GET.get("month") or expense.date.strftime("%Y-%m")
    return redirect(f"/?month={selected_month}")

def withholding_overview(request):
    accounts = (
        BankAccount.objects
        .filter(is_withholding_account=True, is_active=True)
        .prefetch_related("withholding_categories__transactions")
    )

    payout_form = WithholdingPayoutForm(request.POST or None)

    if request.method == "POST":
        if payout_form.is_valid():
            category = payout_form.cleaned_data["withholding_category"]
            date_val = payout_form.cleaned_data["date"]
            amount = payout_form.cleaned_data["amount"]
            note = payout_form.cleaned_data["note"]

            # 🔑 EXACT SAME SEMANTICS AS CSV IMPORT
            WithholdingTransaction.objects.create(
                category=category,
                date=date_val,
                amount=-amount,  # payout = negative
                note=note or "",
            )

            messages.success(
                request,
                f"Payout of ${amount} recorded from '{category.name}'."
            )
            return redirect("withholding_overview")

        else:
            messages.error(request, "Please correct the payout form errors.")

    return render(
        request,
        "withholding_overview.html",
        {
            "accounts": accounts,
            "payout_form": payout_form,
        },
    )

def withholding_category_detail(request, pk):
    category = get_object_or_404(
        WithholdingCategory.objects.prefetch_related("transactions"), pk=pk
    )

    transactions = list(category.transactions.all().order_by("-date", "-id"))

    running_balance = category.balance
    rows = []
    for tx in transactions:
        rows.append({"tx": tx, "balance_after": running_balance})
        running_balance -= tx.amount

    return render(request, "withholding_category_detail.html", {"category": category, "rows": rows})

@require_POST
def update_withholding_transaction(request, pk):
    tx = get_object_or_404(WithholdingTransaction, pk=pk)
    category_pk = tx.category.pk

    if request.POST.get("delete_withholding") == "1":
        tx.delete()
        return redirect("withholding_category_detail", pk=category_pk)

    date_str = request.POST.get("date")
    amount_str = request.POST.get("amount")
    note = request.POST.get("note", "")

    if date_str:
        try:
            tx.date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    if amount_str:
        cleaned = amount_str.replace("$", "").replace(",", "").strip()
        try:
            tx.amount = Decimal(cleaned)
        except InvalidOperation:
            pass

    tx.note = note
    tx.save()

    return redirect("withholding_category_detail", pk=category_pk)

@require_http_methods(["GET", "POST"])
def expense_edit(request, expense_id):
    expense = get_object_or_404(
        Expense.objects.select_related("category", "bank_account", "rental_unit", "cra_category"),
        pk=expense_id
    )

    if request.method == "POST":
        # Delete attachment
        delete_attachment_id = request.POST.get("delete_attachment_id")
        if delete_attachment_id:
            att = get_object_or_404(ExpenseAttachment, pk=delete_attachment_id, expense=expense)
            if att.file:
                att.file.delete(save=False)
            att.delete()
            messages.success(request, "Attachment deleted.")
            return redirect("expense_edit", expense_id=expense.id)

        form = ExpenseEditForm(request.POST, instance=expense)

        # IMPORTANT: do uploads directly from request.FILES
        files = request.FILES.getlist("files")
        print("FILES KEYS:", list(request.FILES.keys()))
        print("FILES COUNT:", len(files))

        if form.is_valid():
            form.save()

            created = 0
            for f in files:
                # skip empty placeholders (some browsers can submit empties)
                if not f:
                    continue
                ExpenseAttachment.objects.create(
                    expense=expense,
                    file=f,
                    original_name=getattr(f, "name", "") or "",
                )
                created += 1

            if created:
                messages.success(request, f"Saved expense and uploaded {created} attachment(s).")
            else:
                messages.success(request, "Saved expense (no attachments uploaded).")

            return redirect("expense_edit", expense_id=expense.id)

        # If the expense form is invalid, show errors (and keep user on page)
        messages.error(request, "Please correct the errors below.")
    else:
        form = ExpenseEditForm(instance=expense)

    attachments = expense.attachments.all()

    return render(request, "expense_edit.html", {
        "expense": expense,
        "form": form,
        "attachments": attachments,
    })

