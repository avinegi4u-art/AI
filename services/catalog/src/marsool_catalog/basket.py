"""Basket validation and pricing.

Pure functions over already-loaded catalogue data: no database access, no I/O. That keeps
the rules — which options are required, how many may be chosen, what the line costs —
exhaustively testable, and it is why the order service delegates here instead of
reimplementing them.

Every line is checked independently and all issues are returned together, so a customer
sees every problem at once instead of discovering them one request at a time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from marsool_catalog.models import MenuItem, Option, OptionGroup, SelectionType
from marsool_catalog.schemas import (
    BasketLineIssue,
    BasketLineRequest,
    BasketValidationResponse,
    PricedBasketLine,
    PricedOption,
)
from marsool_core.money import from_minor_units

#: Builds an issue for the line currently being validated, binding its index and item id.
IssueFactory = Callable[..., BasketLineIssue]


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """The catalogue rows needed to validate one basket."""

    items: dict[str, MenuItem]
    groups_by_item: dict[str, list[OptionGroup]]
    options_by_group: dict[str, list[Option]]


def validate_basket(
    *,
    merchant_id: str,
    lines: list[BasketLineRequest],
    snapshot: CatalogSnapshot,
    currency: str,
) -> BasketValidationResponse:
    """Validate and price a basket against a catalogue snapshot."""
    priced_lines: list[PricedBasketLine] = []
    issues: list[BasketLineIssue] = []

    for index, line in enumerate(lines):
        line_issues, priced = _validate_line(
            line_index=index, line=line, merchant_id=merchant_id, snapshot=snapshot
        )
        issues.extend(line_issues)
        if priced is not None:
            priced_lines.append(priced)

    valid = not issues
    # Quantized to the currency's exponent even when zero, so clients always see the same
    # shape ("0.00", not "0").
    zero = from_minor_units(0, currency)
    items_amount = (
        sum((line.line_total for line in priced_lines), zero) if valid else zero
    )
    return BasketValidationResponse(
        merchant_id=merchant_id,
        valid=valid,
        currency=currency,
        items_amount=items_amount,
        # Priced lines are still returned alongside issues: a client can show correct
        # prices for the lines that are fine while flagging the ones that are not.
        lines=priced_lines,
        issues=issues,
    )


def _validate_line(
    *,
    line_index: int,
    line: BasketLineRequest,
    merchant_id: str,
    snapshot: CatalogSnapshot,
) -> tuple[list[BasketLineIssue], PricedBasketLine | None]:
    """Validate one line, returning its issues and its priced form when valid."""
    issues: list[BasketLineIssue] = []

    def issue(code: str, message: str, **details: object) -> BasketLineIssue:
        return BasketLineIssue(
            line_index=line_index,
            item_id=line.item_id,
            code=code,
            message=message,
            details=details,
        )

    item = snapshot.items.get(line.item_id)
    if item is None:
        return [issue("item_not_found", "This item does not exist")], None
    if item.merchant_id != merchant_id:
        # Guards against a basket mixing merchants, which would make the order
        # unfulfillable and the delivery unroutable.
        return [
            issue(
                "item_not_in_merchant",
                "This item belongs to a different merchant",
                expected_merchant_id=merchant_id,
                actual_merchant_id=item.merchant_id,
            )
        ], None
    if not item.is_available:
        issues.append(issue("item_unavailable", f"{item.name} is currently unavailable"))

    groups = {group.id: group for group in snapshot.groups_by_item.get(item.id, [])}
    selections = {selection.group_id: selection.option_ids for selection in line.selections}

    options_total_minor = 0
    selected: list[PricedOption] = []

    for group_id, option_ids in selections.items():
        group = groups.get(group_id)
        if group is None:
            issues.append(
                issue(
                    "option_group_not_found",
                    "This option group does not belong to the item",
                    group_id=group_id,
                )
            )
            continue

        available = {option.id: option for option in snapshot.options_by_group.get(group_id, [])}
        for option_id in option_ids:
            option = available.get(option_id)
            if option is None:
                issues.append(
                    issue(
                        "option_not_found",
                        "This option does not belong to the group",
                        group_id=group_id,
                        option_id=option_id,
                    )
                )
                continue
            if not option.is_available:
                issues.append(
                    issue(
                        "option_unavailable",
                        f"{option.name} is currently unavailable",
                        group_id=group_id,
                        option_id=option_id,
                    )
                )
                continue
            options_total_minor += option.price_delta_minor
            selected.append(
                PricedOption(
                    option_id=option.id,
                    group_id=group_id,
                    name=option.name,
                    price_delta=from_minor_units(option.price_delta_minor, item.currency),
                )
            )

        issues.extend(
            _check_selection_count(
                group=group, chosen=len(option_ids), make_issue=issue
            )
        )

    # Required groups the client omitted entirely are only detectable here, after the
    # supplied selections have been walked.
    for group in groups.values():
        if group.min_select > 0 and group.id not in selections:
            issues.append(
                issue(
                    "required_group_missing",
                    f"'{group.name}' requires a selection",
                    group_id=group.id,
                    min_select=group.min_select,
                )
            )

    if issues:
        return issues, None

    unit_price_minor = item.price_minor + options_total_minor
    return [], PricedBasketLine(
        line_index=line_index,
        item_id=item.id,
        name=item.name,
        quantity=line.quantity,
        unit_base_price=from_minor_units(item.price_minor, item.currency),
        unit_options_price=from_minor_units(options_total_minor, item.currency),
        unit_price=from_minor_units(unit_price_minor, item.currency),
        line_total=from_minor_units(unit_price_minor * line.quantity, item.currency),
        selected_options=selected,
        notes=line.notes,
    )


def _check_selection_count(
    *, group: OptionGroup, chosen: int, make_issue: IssueFactory
) -> list[BasketLineIssue]:
    """Check a group's selection count against its bounds."""
    issues: list[BasketLineIssue] = []
    # A SINGLE group can never accept more than one choice regardless of max_select, so the
    # effective ceiling is the stricter of the two.
    effective_max = 1 if group.selection_type is SelectionType.SINGLE else group.max_select

    if chosen < group.min_select:
        issues.append(
            make_issue(
                "too_few_options",
                f"'{group.name}' requires at least {group.min_select} selection(s)",
                group_id=group.id,
                min_select=group.min_select,
                chosen=chosen,
            )
        )
    if chosen > effective_max:
        issues.append(
            make_issue(
                "too_many_options",
                f"'{group.name}' allows at most {effective_max} selection(s)",
                group_id=group.id,
                max_select=effective_max,
                chosen=chosen,
            )
        )
    return issues
