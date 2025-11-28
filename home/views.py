from django.shortcuts import render, redirect
from django.utils.timezone import now
from calendar import monthrange
from datetime import datetime, date
from decimal import Decimal
from collections import defaultdict
from django.db.models import Sum

from .forms import TransactionForm
from .models import Expense, Income, Category
from django.shortcuts import get_object_or_404
from calendar import monthrange
from django.shortcuts import get_object_or_404
from django.forms import ModelForm
from django.urls import reverse
from django.http import HttpResponseRedirect
from .models import Category
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponseBadRequest
from collections import defaultdict
import calendar
from django.db.models import Sum
from collections import defaultdict
from datetime import date, timedelta
from calendar import monthrange


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
                if entry_type == "income":
                    Income.objects.create(
                        date=form.cleaned_data['date'],
                        amount=form.cleaned_data['amount'],
                        category=form.cleaned_data['source'],
                        taxable=form.cleaned_data['taxable'],
                        notes=form.cleaned_data['notes'],
                    )
                else:
                    Expense.objects.create(
                        date=form.cleaned_data["date"],
                        amount=form.cleaned_data["amount"],
                        vendor_name=form.cleaned_data["vendor_name"],
                        category=form.cleaned_data["category"],
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




