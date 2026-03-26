# Month-End Close Feature - Implementation Guide

## ✅ Completed Steps

1. **Models created** (`home/models.py`) ✓
   - `MonthEndClose` - Tracks closed months with financial snapshots
   - `AccountSnapshot` - Stores account balances at month-end
   - `NetWorthSnapshot` - Tracks net worth over time

2. **Migration created and applied** ✓
   - Migration: `0024_monthendclose_networthsnapshot_accountsnapshot.py`

3. **Admin registration complete** ✓
   - All three models registered in Django admin
   - Inline editors for snapshots within MonthEndClose

---

## 🚀 Next Steps - Complete Implementation

### Step 4: Create Month-End Close View

Add this to `home/views.py`:

```python
import os
import json
from datetime import datetime, date
from calendar import monthrange
from django.core.management import call_command
from django.db import transaction as db_transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from .models import (
    MonthEndClose,
    AccountSnapshot,
    NetWorthSnapshot,
    Expense,
    Income,
    Transfer,
    BankAccount,
)

@require_POST
@csrf_exempt
def close_month(request):
    """
    Close a month: create snapshots, lock transactions, create backup.
    """
    try:
        # Parse month from request
        month_str = request.POST.get('month')  # Format: "2024-01"
        if not month_str:
            return JsonResponse({'success': False, 'error': 'Month parameter required'}, status=400)

        year, month = map(int, month_str.split('-'))
        month_first_day = date(year, month, 1)
        _, last_day_num = monthrange(year, month)
        month_last_day = date(year, month, last_day_num)

        # Check if month is already closed
        if MonthEndClose.objects.filter(month=month_first_day).exists():
            return JsonResponse({'success': False, 'error': 'Month already closed'}, status=400)

        # Don't allow closing future months
        if month_first_day > date.today():
            return JsonResponse({'success': False, 'error': 'Cannot close future months'}, status=400)

        # Calculate financial summary
        income_entries = Income.objects.filter(date__range=(month_first_day, month_last_day))
        expense_entries = Expense.objects.filter(date__range=(month_first_day, month_last_day))
        transfer_entries = Transfer.objects.filter(date__range=(month_first_day, month_last_day))

        total_income = sum(i.amount for i in income_entries)
        total_expenses = sum(e.amount for e in expense_entries)
        total_transfers = sum(t.amount for t in transfer_entries)
        net_savings = total_income - total_expenses
        transaction_count = income_entries.count() + expense_entries.count() + transfer_entries.count()

        # Create backup
        backup_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backups')
        os.makedirs(backup_dir, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f'monthend_{month_str}_{timestamp}.json'
        backup_path = os.path.join(backup_dir, backup_filename)

        # Export database to JSON
        with open(backup_path, 'w') as f:
            call_command('dumpdata', 'home', indent=2, stdout=f)

        # Use atomic transaction to create close record and snapshots
        with db_transaction.atomic():
            # Create month-end close record
            month_close = MonthEndClose.objects.create(
                month=month_first_day,
                closed_by=request.user.username if hasattr(request, 'user') and request.user.is_authenticated else 'System',
                backup_file=backup_filename,
                total_income=total_income,
                total_expenses=total_expenses,
                net_savings=net_savings,
                total_transfers=total_transfers,
                transaction_count=transaction_count,
                is_locked=True,
            )

            # Create account snapshots
            for account in BankAccount.objects.filter(is_active=True):
                AccountSnapshot.objects.create(
                    month_close=month_close,
                    bank_account=account,
                    balance=account.current_balance or 0,
                )

            # Create net worth snapshot (placeholder - customize based on your needs)
            total_liquid = sum(
                acc.current_balance or 0
                for acc in BankAccount.objects.filter(is_active=True, account_type='CHECKING')
            ) + sum(
                acc.current_balance or 0
                for acc in BankAccount.objects.filter(is_active=True, account_type='SAVINGS')
            )

            NetWorthSnapshot.objects.create(
                month_close=month_close,
                total_net_worth=total_liquid,  # Simplified - add property values, investments, liabilities
                liquid_assets=total_liquid,
                investment_assets=0,  # TODO: Add investment tracking
                property_value=0,  # TODO: Link to RentalProperty values
                liabilities=0,  # TODO: Add liability tracking
            )

        return JsonResponse({
            'success': True,
            'message': f'{month_close.month_display} closed successfully',
            'data': {
                'month': month_str,
                'net_savings': str(net_savings),
                'transaction_count': transaction_count,
                'backup_file': backup_filename,
            }
        })

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
@csrf_exempt
def reopen_month(request):
    """
    Reopen a closed month (admin function).
    """
    try:
        month_str = request.POST.get('month')
        reason = request.POST.get('reason', '')

        if not month_str:
            return JsonResponse({'success': False, 'error': 'Month parameter required'}, status=400)

        year, month = map(int, month_str.split('-'))
        month_first_day = date(year, month, 1)

        month_close = MonthEndClose.objects.get(month=month_first_day)

        if not month_close.is_locked:
            return JsonResponse({'success': False, 'error': 'Month is already open'}, status=400)

        # Update close record
        month_close.is_locked = False
        month_close.reopened_at = datetime.now()
        month_close.reopened_by = request.user.username if hasattr(request, 'user') and request.user.is_authenticated else 'System'
        month_close.reopen_reason = reason
        month_close.save()

        return JsonResponse({
            'success': True,
            'message': f'{month_close.month_display} reopened'
        })

    except MonthEndClose.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Month not found or not closed'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def check_month_locked(request):
    """
    Check if a given month is locked.
    Used by frontend to disable edit/delete buttons.
    """
    month_str = request.GET.get('month')
    if not month_str:
        return JsonResponse({'locked': False})

    try:
        year, month = map(int, month_str.split('-'))
        month_first_day = date(year, month, 1)

        month_close = MonthEndClose.objects.filter(month=month_first_day, is_locked=True).first()

        return JsonResponse({
            'locked': month_close is not None,
            'month': month_str,
            'closed_at': month_close.closed_at.isoformat() if month_close else None,
        })
    except:
        return JsonResponse({'locked': False})
```

---

### Step 5: Add URL Routes

Add to `home/urls.py`:

```python
from django.urls import path
from . import views

urlpatterns = [
    # ... existing URLs ...

    # Month-end close
    path('api/close-month/', views.close_month, name='close_month'),
    path('api/reopen-month/', views.reopen_month, name='reopen_month'),
    path('api/check-month-locked/', views.check_month_locked, name='check_month_locked'),
]
```

---

### Step 6: Add UI to Dashboard

Add this to `dashboard.html` after the month selector section (around line 400):

```html
<!-- Month-End Close Section -->
{% if not is_month_closed %}
<div class="finch-card mb-4">
  <div class="finch-card-header gradient-purple">
    <h5 class="mb-0">📊 Month-End Close</h5>
  </div>
  <div class="card-body">
    <p class="text-muted mb-3">
      Close <strong>{{ selected_month_display }}</strong> to lock transactions and create a financial snapshot.
    </p>

    <div class="alert alert-info mb-3">
      <strong>This will:</strong>
      <ul class="mb-0">
        <li>Lock all transactions for this month (prevent edits/deletes)</li>
        <li>Create backup of database</li>
        <li>Snapshot account balances</li>
        <li>Record net worth and financial summary</li>
      </ul>
    </div>

    <div class="d-flex gap-2 align-items-center">
      <button
        type="button"
        class="btn btn-primary finch-btn"
        id="close-month-btn"
        onclick="openCloseMonthModal()">
        🔒 Close {{ selected_month_display }}
      </button>

      <div id="close-month-spinner" class="spinner-border spinner-border-sm text-primary d-none" role="status">
        <span class="visually-hidden">Processing...</span>
      </div>

      <div id="close-month-status" class="text-muted small"></div>
    </div>
  </div>
</div>
{% else %}
<div class="alert alert-warning">
  <strong>🔒 Month Closed:</strong> {{ selected_month_display }} was closed on {{ month_close.closed_at|date:"F j, Y" }}.
  Transactions are locked. <a href="#" onclick="openReopenMonthModal(); return false;">Reopen month</a>
</div>
{% endif %}
```

---

### Step 7: Add Confirmation Modal

Add to `dashboard.html` before `{% endblock %}`:

```html
<!-- Close Month Confirmation Modal -->
<div class="modal fade" id="closeMonthModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title">Close {{ selected_month_display }}?</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <p><strong>Summary for {{ selected_month_display }}:</strong></p>
        <table class="table table-sm">
          <tr>
            <td>Total Income:</td>
            <td class="text-end"><strong class="text-success">${{ total_income|floatformat:2 }}</strong></td>
          </tr>
          <tr>
            <td>Total Expenses:</td>
            <td class="text-end"><strong class="text-danger">${{ total_expenses|floatformat:2 }}</strong></td>
          </tr>
          <tr>
            <td>Net Savings:</td>
            <td class="text-end"><strong>${{ net_savings|floatformat:2 }}</strong></td>
          </tr>
          <tr>
            <td>Transactions:</td>
            <td class="text-end">{{ income_entries|length|add:expense_entries|length|add:transfer_entries|length }}</td>
          </tr>
        </table>

        <div class="alert alert-warning">
          <strong>⚠️ Warning:</strong> Once closed, you cannot edit or delete transactions for this month unless you reopen it.
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
        <button type="button" class="btn btn-primary" onclick="confirmCloseMonth()">
          🔒 Close Month & Create Backup
        </button>
      </div>
    </div>
  </div>
</div>
```

---

### Step 8: Add JavaScript

Add to `dashboard.html` in the `<script>` section:

```javascript
function openCloseMonthModal() {
  const modal = new bootstrap.Modal(document.getElementById('closeMonthModal'));
  modal.show();
}

function confirmCloseMonth() {
  const btn = document.getElementById('close-month-btn');
  const spinner = document.getElementById('close-month-spinner');
  const status = document.getElementById('close-month-status');

  // Disable button and show spinner
  btn.disabled = true;
  spinner.classList.remove('d-none');
  status.textContent = 'Creating backup and snapshots...';

  // Get current month
  const monthParam = '{{ selected_month }}';  // Django template variable

  // Make AJAX request
  fetch('/api/close-month/', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: `month=${monthParam}`
  })
  .then(response => response.json())
  .then(data => {
    if (data.success) {
      // Close modal
      bootstrap.Modal.getInstance(document.getElementById('closeMonthModal')).hide();

      // Show success message
      status.textContent = '✓ Month closed successfully';
      status.classList.add('text-success');

      // Reload page to show locked state
      setTimeout(() => {
        window.location.reload();
      }, 1500);
    } else {
      alert('Error: ' + data.error);
      btn.disabled = false;
      spinner.classList.add('d-none');
      status.textContent = '';
    }
  })
  .catch(error => {
    alert('Network error: ' + error);
    btn.disabled = false;
    spinner.classList.add('d-none');
    status.textContent = '';
  });
}
```

---

### Step 9: Update Dashboard View Context

Add to `dashboard()` view in `views.py`:

```python
# Check if current month is closed
is_month_closed = False
month_close = None
if selected_date:
    month_close = MonthEndClose.objects.filter(
        month=selected_date,
        is_locked=True
    ).first()
    is_month_closed = month_close is not None

# Add to context
context = {
    # ... existing context ...
    'is_month_closed': is_month_closed,
    'month_close': month_close,
}
```

---

### Step 10: Prevent Edits to Locked Months

Add validation to edit/delete views:

```python
def check_month_locked_for_transaction(transaction_date):
    """Helper to check if a transaction's month is locked."""
    month_first_day = date(transaction_date.year, transaction_date.month, 1)
    return MonthEndClose.objects.filter(month=month_first_day, is_locked=True).exists()

# In your expense/income/transfer edit views, add:
if check_month_locked_for_transaction(expense.date):
    messages.error(request, "Cannot edit transactions in a closed month.")
    return redirect('dashboard')
```

---

## 🎯 Testing Checklist

- [ ] Close a month and verify backup file created in `backups/`
- [ ] Verify `MonthEndClose` record created in database
- [ ] Verify `AccountSnapshot` records created for all active accounts
- [ ] Verify `NetWorthSnapshot` record created
- [ ] Try to edit/delete transaction in closed month (should be blocked)
- [ ] Reopen month via admin panel
- [ ] Verify transactions become editable again
- [ ] Check admin panel displays for all three models

---

## 📦 Backup Files Location

Backups are stored in: `C:\Users\Mike\PycharmProjects\finchFinance\backups\`

Format: `monthend_YYYY-MM_YYYYMMDD_HHMMSS.json`

---

## 🔮 Future Enhancements

1. **Net Worth Tracking**
   - Add investment account values
   - Track property values over time
   - Add liability tracking (mortgages, loans)

2. **Reconciliation**
   - Compare expected vs actual account balances
   - Flag discrepancies before closing

3. **Reports**
   - Generate PDF month-end report
   - Email summary to user
   - Year-over-year comparisons

4. **Bulk Operations**
   - Close multiple months at once
   - Automated month-end reminders

---

## 🚨 Important Notes

1. **Backup Strategy**: Month-end backups are in addition to (not a replacement for) your regular backup strategy
2. **Reopening**: Use sparingly - frequent reopening defeats the purpose of locking
3. **Net Worth**: Current implementation is simplified - customize based on your assets/liabilities
4. **Performance**: For many years of data, consider archiving old snapshots

---

## 📞 Next Steps

1. Review this implementation
2. Implement views and URLs (Steps 4-5)
3. Add UI components (Steps 6-8)
4. Test thoroughly with a non-production month
5. Document your month-end process workflow
6. Consider adding net worth tracking for your specific assets

