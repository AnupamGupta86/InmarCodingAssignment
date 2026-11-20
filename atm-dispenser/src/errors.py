"""Errors raised for invalid input.

A failed withdrawal ("the notes in the machine cannot make this amount") is NOT an error:
it is a normal outcome and is reported by returning None. Errors are only for input that
makes no sense, such as a negative amount or a note with a negative value.
"""


class ATMError(ValueError):
    """Base class for every validation error raised by this package."""


class InvalidAmountError(ATMError):
    """The withdrawal amount is not a positive whole number."""


class InvalidInventoryError(ATMError):
    """The ATM inventory (denominations and note counts) is not valid."""
