import csv
import io
import calendar
from calendar import monthrange
from collections import defaultdict
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from django.db import IntegrityError

from django.contrib import messages
from django.db.models import Sum
from django.forms import ModelForm, formset_factory
from django.http import HttpResponseBadRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from .forms import TransactionForm, CSVUploadForm, TransactionImportForm
from .models import (
    Expense,
    Income,
    Category,
    IncomeCategory,
    BankAccount,
    ImportBatch,
    WithholdingCategory,
    WithholdingTransaction,
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


def apply_income_rules(desc_upper, amount, entry_type_default):
    entry_type = entry_type_default
    income_source = ""

    if "GLOBALIZATION" in desc_upper:
        entry_type = "income"
        income_source = "Employment Income"
        return entry_type, income_source

    if "E-TRANSFER" in desc_upper and entry_type_default == "income":
        if Decimal("2000") <= amount <= Decimal("2700"):
            entry_type = "income"
            income_source = "Arnprior Rental Income (MAIN)"
        elif amount < Decimal("2000"):
            entry_type = "income"
            income_source = "Arnprior Rental Income (LOFT)"

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
                expense.notes = request.POST["notes"]
                expense.save()

            selected_month_param = f"{expense.date.year:04d}-{expense.date.month:02d}"
            return redirect(f"/?month={selected_month_param}")

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
                income.taxable = request.POST.get("taxable") == "on"
                income.notes = request.POST["notes"]
                income.save()

            selected_month_param = f"{income.date.year:04d}-{income.date.month:02d}"
            return redirect(f"/?month={selected_month_param}")

        else:
            form = TransactionForm(request.POST)
            if form.is_valid():
                entry_type = form.cleaned_data["entry_type"]
                entry_date = form.cleaned_data["date"]
                amount = form.cleaned_data["amount"]
                notes = form.cleaned_data["notes"]

                if entry_type == "income":
                    source = form.cleaned_data["source"]  # IncomeCategory instance or None

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
                        )

                else:
                    vendor_name = form.cleaned_data["vendor_name"]
                    category = form.cleaned_data["category"]
                    location = form.cleaned_data.get("location", "Ottawa")

                    apply_to_withholding = form.cleaned_data.get("apply_to_withholding")
                    withholding_category = form.cleaned_data.get("withholding_category")

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

    income_entries = Income.objects.filter(date__range=(first_day, last_day)).select_related("income_category", "bank_account")
    expense_entries = Expense.objects.filter(date__range=(first_day, last_day)).select_related("category", "bank_account")

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

    # Expense progress
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

    # Income progress
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
    """
    Expense drilldown: show expense transactions for a Category across a selectable date range,
    with monthly totals and a chart-friendly series.
    """
    selected_month_str = request.GET.get("month")
    range_key = request.GET.get("range", "6")  # default: last 6 months
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
    """
    Income drilldown: show income transactions for an IncomeCategory across a selectable date range,
    with monthly totals and a chart-friendly series.
    """
    selected_month_str = request.GET.get("month")
    range_key = request.GET.get("range", "6")  # default: last 6 months
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

    # newest first for cards
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

    # newest first in UI
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
    """
    Unified Manage Categories page:
      - Expense categories (Category)
      - Income categories (IncomeCategory)
    Both are editable via modal and deletable.
    """
    expense_categories = Category.objects.all().order_by("name")
    income_categories = IncomeCategory.objects.all().order_by("name")

    if request.method == "POST":
        action = request.POST.get("action")

        # ---------- EXPENSE ----------
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
            # fall through to render page with errors

        # ---------- INCOME ----------
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
            # fall through to render page with errors

    # GET (or failed POST)
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

            entry_type, income_source_name = apply_income_rules(desc_upper, amount, entry_type_default)

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

            initial_rows.append({
                "entry_type": entry_type,
                "date": parsed_date,
                "vendor_name": raw_desc or "Unknown Vendor",
                "amount": amount,
                "location": "Ottawa",
                "notes": "",
                "expense_category": expense_category,
                "income_source": income_source_obj,
                "apply_to_withholding": False,
                "is_withholding_payout": False,
                "withholding_category": None,
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
                "recent_batches": recent_batches,
            })

        if missing_categories:
            missing_list = ", ".join(sorted(missing_categories))
            messages.warning(
                request,
                f"The following auto-mapped categories were not found in your database and were skipped: {missing_list}."
            )

        formset = TransactionImportFormSet(initial=initial_rows)
        return render(request, "import_transactions.html", {
            "step": "review",
            "formset": formset,
            "selected_bank_account": bank_account,
            "uploaded_filename": uploaded_filename,
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
            return render(request, "import_transactions.html", {
                "step": "review",
                "formset": formset,
                "selected_bank_account": bank_account,
                "uploaded_filename": uploaded_filename,
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
                    category=income_source.name,  # legacy sync for now
                    taxable=income_source.taxable_default,
                    notes=notes or "",
                    bank_account=bank_account,
                )
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
        expense.notes = request.POST.get("notes")
        expense.save()

    selected_month = request.GET.get("month") or expense.date.strftime("%Y-%m")
    return redirect(f"/?month={selected_month}")


def withholding_overview(request):
    accounts = (
        BankAccount.objects
        .filter(is_withholding_account=True, is_active=True)
        .prefetch_related("withholding_categories__transactions")
    )
    return render(request, "withholding_overview.html", {"accounts": accounts})


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
