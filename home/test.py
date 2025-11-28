if request.method == "POST":
    # Handle editing or deleting an expense
    if "expense_id" in request.POST:
        expense = get_object_or_404(Expense, pk=request.POST["expense_id"])
        if "delete_expense" in request.POST:
            expense.delete()
        else:
            expense.date = request.POST["date"]
            expense.vendor_name = request.POST["vendor_name"]
            expense.category = get_object_or_404(Category, name=request.POST["category"])
            expense.location = request.POST["location"]
            expense.amount = request.POST["amount"]
            expense.notes = request.POST["notes"]
            expense.save()

        # Redirect to the same month view after edit/delete
        selected_month_param = request.GET.get("month") or f"{expense.date.year:04d}-{expense.date.month:02d}"
        return redirect(f"/?month={selected_month_param}")
