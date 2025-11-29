import csv
import io

from django.shortcuts import render, redirect, get_object_or_404
from django.utils.timezone import now
from calendar import monthrange
from datetime import datetime, date, timedelta
from decimal import Decimal
from collections import defaultdict
from django.db.models import Sum

from .forms import TransactionForm, CSVUploadForm, TransactionImportForm
from .models import Expense, Income, Category, BankAccount, ImportBatch
from django.forms import ModelForm, formset_factory
from django.urls import reverse
from django.http import HttpResponseRedirect, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib import messages
import calendar

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
    """
    Lookup Category by name with simple caching and tracking of missing names.
    """
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
    """
    Apply explicit rules for income based on description + amount.

    Returns:
        (entry_type, income_source)  # income_source is one of Income.CATEGORY_CHOICES or ""
    """
    entry_type = entry_type_default
    income_source = ""

    # GLOBALIZATION -> Employment Income
    if "GLOBALIZATION" in desc_upper:
        entry_type = "income"
        income_source = "Employment Income"
        return entry_type, income_source

    # E-TRANSFER deposits -> Arnprior Rental Income (MAIN/LOFT)
    if "E-TRANSFER" in desc_upper and entry_type_default == "income":
        if Decimal("2000") <= amount <= Decimal("2700"):
            entry_type = "income"
            income_source = "Arnprior Rental Income (MAIN)"
        elif amount < Decimal("2000"):
            entry_type = "income"
            income_source = "Arnprior Rental Income (LOFT)"

    return entry_type, income_source


def apply_expense_rules(desc_upper, amount, category_cache, missing_categories):
    """
    Apply explicit expense rules based on description + amount.

    Returns:
        Category instance or None
    """
    # E-TFR with exact 180.80 -> Arnprior Snow Removal
    if "E-TFR" in desc_upper and amount == EXACT_ETFR_SNOW_REMOVAL_AMOUNT:
        return get_category_cached("Arnprior Snow Removal", category_cache, missing_categories)

    # Keyword-based mappings
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
            # Inline Edit or Delete - Expense
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
            # Inline Edit or Delete - Income
            income = get_object_or_404(Income, pk=request.POST["income_id"])
            if "delete_income" in request.POST:
                income.delete()
            else:
                income.date = datetime.strptime(request.POST["date"], "%Y-%m-%d").date()
                income.category = request.POST["source"]
                income.amount = Decimal(request.POST["amount"])
                income.taxable = request.POST.get("taxable") == "on"
                income.notes = request.POST["notes"]
                income.save()
            selected_month_param = f"{income.date.year:04d}-{income.date.month:02d}"
            return redirect(f"/?month={selected_month_param}")

        else:
            # New Entry from Add Transaction form
            form = TransactionForm(request.POST)
            if form.is_valid():
                entry_type = form.cleaned_data["entry_type"]
                entry_date = form.cleaned_data["date"]
                amount = form.cleaned_data["amount"]

                if entry_type == "income":
                    source = form.cleaned_data["source"]

                    # Duplicate protection: income
                    income_exists = Income.objects.filter(
                        date=entry_date,
                        amount=amount,
                        category=source,
                    ).exists()

                    if income_exists:
                        messages.warning(
                            request,
                            "This income transaction already exists and was not added again."
                        )
                    else:
                        Income.objects.create(
                            date=entry_date,
                            amount=amount,
                            category=source,
                            taxable=form.cleaned_data['taxable'],
                            notes=form.cleaned_data['notes'],
                        )

                else:
                    vendor_name = form.cleaned_data["vendor_name"]
                    category = form.cleaned_data["category"]

                    # Duplicate protection: expense
                    expense_exists = Expense.objects.filter(
                        date=entry_date,
                        amount=amount,
                        vendor_name=vendor_name,
                        category=category,
                    ).exists()

                    if expense_exists:
                        messages.warning(
                            request,
                            "This expense transaction already exists and was not added again."
                        )
                    else:
                        Expense.objects.create(
                            date=entry_date,
                            amount=amount,
                            vendor_name=vendor_name,
                            category=category,
                            location=form.cleaned_data.get("location", "Ottawa"),
                            notes=form.cleaned_data["notes"],
                        )

                selected_month_param = f"{year:04d}-{month:02d}"
                return redirect(f"/?month={selected_month_param}")
    else:
        form = TransactionForm()

    income_entries = Income.objects.filter(date__range=(first_day, last_day))
    expense_entries = Expense.objects.filter(date__range=(first_day, last_day))

    total_income = sum(i.amount for i in income_entries)
    total_expenses = sum(e.amount for e in expense_entries)
    net_savings = total_income - total_expenses

    income_by_source = defaultdict(list)
    for income in income_entries:
        income_by_source[income.category].append(income)

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

    # Monthly spending progress bar summary
    month_expenses = Expense.objects.filter(date__year=selected_date.year, date__month=selected_date.month)
    categories_with_targets = Category.objects.exclude(monthly_limit__isnull=True)

    category_summaries = []
    for category in categories_with_targets:
        total_spent = month_expenses.filter(category=category).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        percent_used = (total_spent / category.monthly_limit * 100) if category.monthly_limit > 0 else 0
        category_summaries.append({
            'name': category.name,
            'target': category.monthly_limit,
            'spent': total_spent,
            'percent_used': round(percent_used, 1),
            'over_budget': total_spent > category.monthly_limit,
        })

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

    category_summaries = []

    for category in categories_with_targets:
        total_spent = Expense.objects.filter(
            category=category,
            date__range=(first_day, last_day)
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        percent_used = (total_spent / category.monthly_limit * 100) if category.monthly_limit > 0 else 0

        # Assign color class
        if percent_used >= 130:
            bar_class = "bg-danger"
        elif percent_used >= 100:
            bar_class = "bg-warning"
        elif percent_used >= 85:
            bar_class = "bg-warning"
        else:
            bar_class = "bg-success"

        category_summaries.append({
            'name': category.name,
            'target': category.monthly_limit,
            'spent': total_spent,
            'percent_used': round(percent_used, 1),
            'bar_class': bar_class,
        })

    context = {
        "category_summaries": category_summaries,
        "selected_month": f"{year:04d}-{month:02d}",
        "selected_month_display": selected_month_display,
    }
    return render(request, "category_progress.html", context)


def category_expense_list(request, category_name):
    from django.utils.timezone import now
    selected_month_str = request.GET.get("month")
    today = now().date()

    try:
        year, month = map(int, selected_month_str.split("-"))
    except Exception:
        year, month = today.year, today.month

    category = get_object_or_404(Category, name=category_name)

    # Build list of 4 months: selected + 3 previous (latest first)
    month_keys = []
    for i in range(3, -1, -1):
        d = date(year, month, 1) - timedelta(days=30 * i)
        month_keys.append(date(d.year, d.month, 1))
    month_keys = list(reversed(month_keys))  # Make newest month appear first

    # Build expense dictionary grouped by month
    expenses_by_month = defaultdict(list)
    totals_by_month = {}
    percentages_by_month = {}
    trend_labels = []
    trend_values = []

    for m in month_keys:
        start = date(m.year, m.month, 1)
        end = date(m.year, m.month, monthrange(m.year, m.month)[1])
        month_expenses = Expense.objects.filter(category=category, date__range=(start, end)).order_by('-date')
        total = month_expenses.aggregate(total=Sum('amount'))['total'] or Decimal("0.00")
        expenses_by_month[m] = month_expenses
        totals_by_month[m] = total

        percent = (total / category.monthly_limit * 100) if category.monthly_limit else 0
        percentages_by_month[m] = round(percent, 1)

        trend_labels.append(m.strftime("%b %Y"))
        trend_values.append(float(total))

    context = {
        "category": category,
        "selected_month": f"{year:04d}-{month:02d}",
        "expenses_by_month": dict(expenses_by_month),
        "totals_by_month": totals_by_month,
        "percentages_by_month": percentages_by_month,
        "sorted_months": month_keys,
        "has_limit": category.monthly_limit is not None,
        "monthly_limit": category.monthly_limit or Decimal("0.00"),
        "trend_labels": trend_labels,
        "trend_values": trend_values,
    }

    return render(request, "category_expense_list.html", context)


class CategoryForm(ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'monthly_limit', 'savings_target_per_paycheque']


def category_list(request):
    categories = Category.objects.all().order_by("name")

    if request.method == "POST":
        if "delete" in request.POST:
            category = get_object_or_404(Category, pk=request.POST.get("delete"))
            category.delete()
            return redirect("category_list")

        category_id = request.POST.get("category_id")
        if category_id:
            category = get_object_or_404(Category, pk=category_id)
            form = CategoryForm(request.POST, instance=category)
        else:
            form = CategoryForm(request.POST)

        if form.is_valid():
            form.save()
            return redirect("category_list")
    else:
        form = CategoryForm()

    return render(request, "category_list.html", {
        "categories": categories,
        "form": form,
    })


class BankAccountForm(ModelForm):
    class Meta:
        model = BankAccount
        fields = ["name", "institution", "account_number_last4", "is_active"]


def bank_accounts(request):
    accounts = BankAccount.objects.all().order_by("name")

    if request.method == "POST":
        if "delete" in request.POST:
            account = get_object_or_404(BankAccount, pk=request.POST.get("delete"))
            account.delete()
            return redirect("bank_accounts")

        account_id = request.POST.get("account_id")
        if account_id:
            account = get_object_or_404(BankAccount, pk=account_id)
            form = BankAccountForm(request.POST, instance=account)
        else:
            form = BankAccountForm(request.POST)

        if form.is_valid():
            form.save()
            return redirect("bank_accounts")
    else:
        form = BankAccountForm()

    return render(request, "bank_accounts.html", {
        "accounts": accounts,
        "form": form,
    })


def import_batch_detail(request, batch_id):
    batch = get_object_or_404(ImportBatch, pk=batch_id)
    expenses = Expense.objects.filter(import_batch=batch).order_by("-date")
    incomes = Income.objects.filter(import_batch=batch).order_by("-date")

    context = {
        "batch": batch,
        "expenses": expenses,
        "incomes": incomes,
    }
    return render(request, "import_batch_detail.html", context)


@require_http_methods(["GET", "POST"])
def import_transactions(request):
    """
    Step 1 (GET or POST 'upload'):
        - Show an upload form for CSV.
        - Parse CSV into a formset of TransactionImportForm for review,
          applying auto-mapping rules.

    Step 2 (POST 'review'):
        - Validate each row.
        - Create Expense or Income objects accordingly and attach to an ImportBatch.
    """
    if request.method == "GET":
        upload_form = CSVUploadForm()
        recent_batches = ImportBatch.objects.select_related("bank_account").all()[:10]
        return render(request, "import_transactions.html", {
            "step": "upload",
            "upload_form": upload_form,
            "recent_batches": recent_batches,
        })

    # POST
    step = request.POST.get("step", "upload")

    # STEP 1: CSV uploaded, parse and show review formset
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

        # Try to decode uploaded file as text
        try:
            decoded = io.TextIOWrapper(csv_file.file, encoding="utf-8")
        except Exception:
            decoded = io.TextIOWrapper(csv_file.file, encoding="latin-1")

        # Our CSV has NO headers.
        # Columns: 0=date, 1=description, 2=withdrawal, 3=deposit, 4=balance
        reader = csv.reader(decoded)

        initial_rows = []
        category_cache = {}
        missing_categories = set()
        hydro_candidates = []  # list of (index_in_initial_rows, amount)
        earliest_date_in_file = None
        latest_date_in_file = None

        for row in reader:
            # Skip completely empty rows
            if not row or all(not cell.strip() for cell in row):
                continue

            # Safely unpack with defaults if the row is short
            raw_date = row[0].strip() if len(row) > 0 else ""
            raw_desc = row[1].strip() if len(row) > 1 else ""
            raw_withdrawal = row[2].strip() if len(row) > 2 else ""
            raw_deposit = row[3].strip() if len(row) > 3 else ""
            # balance is row[4], ignored

            # Normalize description
            desc_upper = (raw_desc or "").upper()

            # Rule: internal transfer, we skip completely
            if "TFR-TO C/C" in desc_upper:
                continue

            # Skip rows without a description and without any money movement
            if not raw_desc and not raw_withdrawal and not raw_deposit:
                continue

            # Parse date
            parsed_date = None
            for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
                try:
                    parsed_date = datetime.strptime(raw_date, fmt).date()
                    break
                except Exception:
                    continue

            if parsed_date is None:
                # If we can't parse the date, skip this row for now
                continue

            # Track CSV date range
            if earliest_date_in_file is None or parsed_date < earliest_date_in_file:
                earliest_date_in_file = parsed_date
            if latest_date_in_file is None or parsed_date > latest_date_in_file:
                latest_date_in_file = parsed_date

            # Decide if this is expense (withdrawal) or income (deposit),
            # and pick the correct amount column.
            amount_str = None
            entry_type_default = "expense"

            if raw_withdrawal and not raw_deposit:
                amount_str = raw_withdrawal
                entry_type_default = "expense"
            elif raw_deposit and not raw_withdrawal:
                amount_str = raw_deposit
                entry_type_default = "income"
            elif raw_withdrawal and raw_deposit:
                # Rare / odd case: both have values; default to expense for now
                amount_str = raw_withdrawal
                entry_type_default = "expense"
            else:
                # No amount at all
                continue

            # Normalize amount (remove commas etc.)
            amount_str = amount_str.replace(",", "")
            try:
                amount = Decimal(amount_str)
            except Exception:
                continue

            # Store amount as positive; meaning is in entry_type
            amount = abs(amount)

            # --- Apply income rules ---
            entry_type, income_source = apply_income_rules(
                desc_upper=desc_upper,
                amount=amount,
                entry_type_default=entry_type_default,
            )

            # --- Apply expense rules ---
            expense_category = None
            if entry_type == "expense":
                expense_category = apply_expense_rules(
                    desc_upper=desc_upper,
                    amount=amount,
                    category_cache=category_cache,
                    missing_categories=missing_categories,
                )

            # Track Hydro One candidates for later group logic
            if "HYDRO ONE" in desc_upper and entry_type == "expense":
                # We'll adjust their category after reading all rows
                hydro_candidates.append((len(initial_rows), amount))

            # Build the initial form row
            initial_rows.append({
                "entry_type": entry_type,
                "date": parsed_date,
                "vendor_name": raw_desc or "Unknown Vendor",
                "amount": amount,
                "location": "Ottawa",
                "notes": "",
                "expense_category": expense_category,
                "income_source": income_source,
            })

        # Overlap warning with existing batches for this account
        if (
            bank_account
            and earliest_date_in_file is not None
            and latest_date_in_file is not None
        ):
            overlapping = ImportBatch.objects.filter(
                bank_account=bank_account,
                earliest_date__lte=latest_date_in_file,
                latest_date__gte=earliest_date_in_file,
            )
            if overlapping.exists():
                ranges = "; ".join(
                    f"{b.earliest_date} to {b.latest_date}"
                    for b in overlapping
                )
                messages.warning(
                    request,
                    f"This CSV covers {earliest_date_in_file} to {latest_date_in_file}, "
                    f"which overlaps with existing imports for this account: {ranges}. "
                    f"Duplicates will be skipped where detected."
                )

        # --- After reading all rows: apply Hydro One rules ---
        if hydro_candidates:
            if len(hydro_candidates) == 1:
                idx, amt = hydro_candidates[0]
                if amt > Decimal("200.00"):
                    cat_name = "Foxview Hydro"
                else:
                    cat_name = "Arnprior Hydro"
                cat = get_category_cached(cat_name, category_cache, missing_categories)
                if cat:
                    initial_rows[idx]["expense_category"] = cat
            else:
                # Two or more Hydro One entries
                sorted_by_amount = sorted(hydro_candidates, key=lambda x: x[1])

                for idx, amt in sorted_by_amount:
                    if len(sorted_by_amount) == 2:
                        # Two entries: smaller -> Arnprior, larger -> Foxview
                        if (idx, amt) == sorted_by_amount[0]:
                            cat_name = "Arnprior Hydro"
                        else:
                            cat_name = "Foxview Hydro"
                    else:
                        # For more than two, fall back to simple threshold rule
                        if amt > Decimal("200.00"):
                            cat_name = "Foxview Hydro"
                        else:
                            cat_name = "Arnprior Hydro"

                    cat = get_category_cached(cat_name, category_cache, missing_categories)
                    if cat:
                        initial_rows[idx]["expense_category"] = cat

        if not initial_rows:
            messages.warning(
                request,
                "No valid transactions were found in the CSV (check the file format)."
            )
            upload_form = CSVUploadForm()
            return render(request, "import_transactions.html", {
                "step": "upload",
                "upload_form": upload_form,
                "recent_batches": recent_batches,
            })

        # If any categories were missing in the mapping, warn the user
        if missing_categories:
            missing_list = ", ".join(sorted(missing_categories))
            messages.warning(
                request,
                f"The following auto-mapped categories were not found in your database "
                f"and were skipped: {missing_list}."
            )

        formset = TransactionImportFormSet(initial=initial_rows)
        return render(request, "import_transactions.html", {
            "step": "review",
            "formset": formset,
            "selected_bank_account": bank_account,
            "uploaded_filename": uploaded_filename,
        })

    # STEP 2: Review submitted -> create Expense / Income records
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
        earliest_date = None
        latest_date = None
        total_expense_amount = Decimal("0.00")
        total_income_amount = Decimal("0.00")

        expense_objs = []
        income_objs = []

        # Track duplicates within this single import run
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

            if not (date_val and amount):
                continue

            # track date range
            if earliest_date is None or date_val < earliest_date:
                earliest_date = date_val
            if latest_date is None or date_val > latest_date:
                latest_date = date_val

            if entry_type == "expense":
                if not (vendor_name and expense_category):
                    continue

                # Duplicate protection key (ignoring bank account to catch manual vs import too)
                exp_key = (date_val, vendor_name, amount, expense_category.id)

                # Check within this import and in the DB
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

            elif entry_type == "income":
                if not income_source:
                    continue

                inc_key = (date_val, amount, income_source)

                if inc_key in seen_income_keys or Income.objects.filter(
                    date=date_val,
                    amount=amount,
                    category=income_source,
                ).exists():
                    skipped_duplicates += 1
                    continue

                seen_income_keys.add(inc_key)

                inc = Income(
                    date=date_val,
                    amount=amount,
                    category=income_source,
                    notes=notes or "",
                    bank_account=bank_account,
                )
                income_objs.append(inc)
                total_income_amount += amount
                created_incomes += 1

        total_transactions = created_expenses + created_incomes

        # Only create a batch if we actually created some transactions
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

            # attach batch to each expense/income and save
            for exp in expense_objs:
                exp.import_batch = batch
                exp.save()
            for inc in income_objs:
                inc.import_batch = batch
                inc.save()
        else:
            # no transactions created, just save none
            batch = None
            for exp in expense_objs:
                exp.save()
            for inc in income_objs:
                inc.save()

        msg = f"Imported {created_expenses} expenses and {created_incomes} income transactions."
        if skipped_duplicates:
            msg += f" Skipped {skipped_duplicates} duplicate transaction(s)."
        messages.success(request, msg)

        return redirect("dashboard")

    # Fallback: go back to upload step
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

    # Redirect back to the dashboard with the same month as query string
    selected_month = request.GET.get("month") or expense.date.strftime("%Y-%m")
    return redirect(f"/?month={selected_month}")
