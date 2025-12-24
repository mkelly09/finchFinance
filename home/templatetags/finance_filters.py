from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def currency(value):
    """
    Format any numeric value as $1,234.56
    """
    try:
        val = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return "$0.00"
    return "${:,.2f}".format(val)


@register.filter(name="abs")
def abs_filter(value):
    """
    Absolute value filter (so you can use `|abs` in templates).
    """
    try:
        val = Decimal(value)
        return abs(val)
    except (InvalidOperation, TypeError, ValueError):
        return value


@register.filter
def negate(value):
    """
    Multiply by -1 (used when we manually print +/- in templates).

    Example: amount = -100
      In template: -{{ amount|negate|currency }}  -> "-$100.00"
    """
    try:
        val = Decimal(value)
        return -val
    except (InvalidOperation, TypeError, ValueError):
        return value
