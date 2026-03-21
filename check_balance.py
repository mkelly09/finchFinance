from home.models import BankAccount
from datetime import date

# Check one account
account = BankAccount.objects.filter(balance_tracking_enabled=True).first()
if account:
    print(f'Account: {account.name}')
    print(f'Current Balance: ${account.current_balance:,.2f}')
    print(f'Tracking Start Date: {account.balance_tracking_start_date}')
    print(f'Last Updated: {account.last_updated}')

    # Check latest transaction
    latest_income = account.incomes.order_by('-date').first()
    latest_expense = account.expenses.order_by('-date').first()
    latest_transfer_in = account.incoming_transfers.order_by('-date').first()
    latest_transfer_out = account.outgoing_transfers.order_by('-date').first()

    dates = []
    if latest_income: dates.append(latest_income.date)
    if latest_expense: dates.append(latest_expense.date)
    if latest_transfer_in: dates.append(latest_transfer_in.date)
    if latest_transfer_out: dates.append(latest_transfer_out.date)

    if dates:
        print(f'Latest transaction date: {max(dates)}')
